"""Firefly III: Buchungen einer Kategorie lesen und klassifizieren."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import date

import aiohttp


@dataclass
class Buchung:
    datum: date
    betrag: float
    typ: str  # withdrawal | deposit | transfer
    beschreibung: str
    gruppe_id: str


@dataclass
class Klassifizierung:
    abschlaege: list[Buchung]
    einspeisung: list[Buchung]
    ignoriert: list[tuple[Buchung, str]]


class FireflyFehler(Exception):
    pass


def normalisieren(name: str) -> str:
    """Vergleichsform: Unicode-NFC, Groß/Klein egal, Leerraum zusammengefasst."""
    return " ".join(unicodedata.normalize("NFC", name or "").casefold().split())


def parse_buchungen(antwort: dict, kategorie_id: str) -> list[Buchung]:
    """Zerlegt eine Firefly-Antwort (Transaktionsgruppen) in einzelne Buchungen."""
    buchungen: list[Buchung] = []
    for gruppe in antwort.get("data", []):
        for t in gruppe.get("attributes", {}).get("transactions", []):
            if str(t.get("category_id")) != str(kategorie_id):
                continue  # Split-Buchung mit anderer Kategorie
            buchungen.append(
                Buchung(
                    datum=date.fromisoformat(t["date"][:10]),
                    betrag=abs(float(t["amount"])),
                    typ=t.get("type", ""),
                    beschreibung=t.get("description", "") or "",
                    gruppe_id=str(gruppe.get("id", "")),
                )
            )
    return buchungen


def klassifizieren(
    buchungen: list[Buchung],
    ausschluss_stichworte: list[str],
    einspeise_stichwort: str | None = None,
) -> Klassifizierung:
    abschlaege: list[Buchung] = []
    einspeisung: list[Buchung] = []
    ignoriert: list[tuple[Buchung, str]] = []
    for b in sorted(buchungen, key=lambda b: b.datum):
        text = b.beschreibung.lower()
        if einspeise_stichwort and einspeise_stichwort.lower() in text and b.typ == "deposit":
            einspeisung.append(b)
        elif any(w in text for w in ausschluss_stichworte):
            ignoriert.append((b, "Ausschluss-Stichwort"))
        elif b.typ == "withdrawal":
            abschlaege.append(b)
        else:
            ignoriert.append((b, f"Typ {b.typ}"))
    return Klassifizierung(abschlaege, einspeisung, ignoriert)


def token_bereinigen(token: str) -> str:
    """Entfernt Leerraum/Zeilenumbrüche, Anführungszeichen und ein mitkopiertes „Bearer“."""
    t = "".join((token or "").split())
    t = t.strip("\"'")
    if t.lower().startswith("bearer"):
        t = t[6:]
    return t


def url_bereinigen(url: str) -> str:
    """Basis-URL ohne abschließendes / und ohne mitkopiertes /api bzw. /api/v1."""
    u = (url or "").strip().rstrip("/")
    for endung in ("/api/v1", "/api"):
        if u.lower().endswith(endung):
            u = u[: -len(endung)]
    return u


class Firefly:
    def __init__(self, sitzung: aiohttp.ClientSession, url: str, token: str):
        self._s = sitzung
        self._url = url_bereinigen(url)
        self._token = token_bereinigen(token)
        self._kopf = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.api+json",
        }
        # normalisierter Name -> (ID, Originalname)
        self._kategorien: dict[str, tuple[str, str]] | None = None

    async def _get(self, pfad: str, **params) -> dict:
        url = f"{self._url}/api/v1/{pfad}"
        async with self._s.get(
            url,
            headers=self._kopf,
            params=params,
            timeout=aiohttp.ClientTimeout(total=30),
            # Weiterleitungen nicht folgen: aiohttp verwirft dabei den
            # Authorization-Header, Firefly sähe dann gar keinen Token.
            allow_redirects=False,
        ) as r:
            if r.status in (301, 302, 303, 307, 308):
                ziel = r.headers.get("Location", "?")
                raise FireflyFehler(
                    f"Firefly leitet {url} weiter nach {ziel}. Bitte diese Adresse "
                    "(ohne /api/…) als firefly_url eintragen."
                )
            if r.status == 401:
                raise FireflyFehler(await self._diagnose_401(r))
            if r.status >= 400:
                raise FireflyFehler(f"Firefly antwortet mit {r.status} für {pfad}.")
            try:
                return await r.json(content_type=None)
            except ValueError:
                raise FireflyFehler(
                    f"Antwort von {url} ist kein JSON – zeigt firefly_url wirklich auf Firefly III?"
                ) from None

    async def _diagnose_401(self, r: aiohttp.ClientResponse) -> str:
        text = (await r.text())[:300]
        teile = [f"Firefly lehnt den Token ab (401) – Token-Länge {len(self._token)} Zeichen"]
        if len(self._token) < 200:
            teile.append(
                "ein Personal Access Token ist meist über 900 Zeichen lang; "
                "evtl. wurde Client-ID/Secret statt des Tokens kopiert oder der Token abgeschnitten"
            )
        if "Unauthenticated" in text:
            teile.append("Firefly selbst kennt den Token nicht (abgelaufen, gelöscht, oder APP_KEY geändert)")
        elif r.headers.get("WWW-Authenticate", "").lower().startswith("basic") or "<html" in text.lower():
            teile.append(
                "die Ablehnung kommt vermutlich von einem vorgeschalteten Proxy/Login, nicht von Firefly"
            )
        return "; ".join(teile) + "."

    async def _alle_seiten(self, pfad: str, **params) -> list[dict]:
        seiten = []
        seite = 1
        while True:
            antwort = await self._get(pfad, page=seite, **params)
            seiten.append(antwort)
            gesamt = (
                antwort.get("meta", {}).get("pagination", {}).get("total_pages", 1) or 1
            )
            if seite >= gesamt:
                return seiten
            seite += 1

    async def kategorie_id(self, name: str) -> str:
        schluessel = normalisieren(name)
        if self._kategorien is not None and schluessel in self._kategorien:
            return self._kategorien[schluessel][0]
        # Nicht (mehr) im Zwischenspeicher: frisch laden. Erst nach erfolgreichem
        # Abruf speichern, damit ein Fehler den Speicher nicht leer zurücklässt.
        geladen: dict[str, tuple[str, str]] = {}
        for antwort in await self._alle_seiten("categories"):
            for k in antwort.get("data", []):
                original = k.get("attributes", {}).get("name", "")
                geladen[normalisieren(original)] = (str(k["id"]), original)
        self._kategorien = geladen
        if schluessel in geladen:
            return geladen[schluessel][0]
        vorhanden = ", ".join(f"„{o}“" for _, o in sorted(geladen.values(), key=lambda x: normalisieren(x[1])))
        raise FireflyFehler(
            f"Kategorie „{name}“ nicht gefunden. Firefly liefert für diesen Token "
            + (f"{len(geladen)} Kategorien: {vorhanden}." if geladen else "keine Kategorien – "
               "gehört der Token zum richtigen Firefly-Benutzer?")
        )

    async def buchungen(self, kategorie: str, start: date, ende: date) -> list[Buchung]:
        kid = await self.kategorie_id(kategorie)
        ergebnis: list[Buchung] = []
        for antwort in await self._alle_seiten(
            f"categories/{kid}/transactions",
            start=start.isoformat(),
            end=ende.isoformat(),
        ):
            ergebnis.extend(parse_buchungen(antwort, kid))
        return ergebnis
