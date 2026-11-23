"""Strompreise aus EEDC (Energie Effizienz Data Center) lesen.

EEDC läuft als Add-on im internen Home-Assistant-Netz und lauscht dort auf
Port 8099. Die Lese-API (`GET /api/strompreise/`) braucht keine Anmeldung.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import date

import aiohttp

from .prognose import Tarif

LOG = logging.getLogger(__name__)

EEDC_REPO = "https://github.com/supernova1963/eedc-homeassistant"


def standard_urls() -> list[str]:
    """Interne Adressen, unter denen das EEDC-Add-on typischerweise erreichbar ist.

    Der Supervisor benennt Add-ons aus Fremd-Repositories `<hash>-<slug>`, wobei
    der Hash die ersten 8 Zeichen des SHA-1 der Repository-URL sind.
    """
    h = hashlib.sha1(EEDC_REPO.lower().encode()).hexdigest()[:8]
    return [f"http://{h}-eedc:8099", "http://local-eedc:8099"]


class EEDCFehler(Exception):
    pass


@dataclass
class EEDCTarife:
    url: str
    tarife: list[Tarif]
    einspeiseverguetung_eur_kwh: float | None  # aktuell gültig


def tarife_aus_antwort(daten: list[dict], verwendung: str = "allgemein") -> list[Tarif]:
    tarife = []
    for t in daten:
        if (t.get("verwendung") or "allgemein") != verwendung:
            continue
        tarife.append(
            Tarif(
                gueltig_ab=date.fromisoformat(t["gueltig_ab"]),
                gueltig_bis=date.fromisoformat(t["gueltig_bis"]) if t.get("gueltig_bis") else None,
                preis_eur_einheit=float(t["netzbezug_arbeitspreis_cent_kwh"]) / 100,
                grundpreis_eur_monat=(
                    float(t["grundpreis_euro_monat"]) if t.get("grundpreis_euro_monat") else None
                ),
                einspeiseverguetung_eur_kwh=float(t.get("einspeiseverguetung_cent_kwh") or 0) / 100,
                name=" · ".join(x for x in (t.get("anbieter"), t.get("tarifname")) if x) or None,
            )
        )
    return sorted(tarife, key=lambda t: t.gueltig_ab)


async def tarife_laden(sitzung: aiohttp.ClientSession, url: str | None, heute: date) -> EEDCTarife:
    kandidaten = [url.rstrip("/")] if url else standard_urls()
    fehler = []
    for basis in kandidaten:
        try:
            async with sitzung.get(
                f"{basis}/api/strompreise/", timeout=aiohttp.ClientTimeout(total=10)
            ) as r:
                if r.status != 200:
                    fehler.append(f"{basis}: HTTP {r.status}")
                    continue
                daten = await r.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as e:
            fehler.append(f"{basis}: {e.__class__.__name__}")
            continue
        tarife = tarife_aus_antwort(daten)
        if not tarife:
            raise EEDCFehler(f"EEDC ({basis}) erreichbar, aber kein allgemeiner Stromtarif hinterlegt.")
        aktuell = next((t for t in reversed(tarife) if t.gilt_am(heute)), None)
        LOG.info("EEDC: %d Tarif(e) von %s", len(tarife), basis)
        return EEDCTarife(basis, tarife, aktuell.einspeiseverguetung_eur_kwh if aktuell else None)
    raise EEDCFehler("EEDC nicht erreichbar (" + "; ".join(fehler) + ").")
