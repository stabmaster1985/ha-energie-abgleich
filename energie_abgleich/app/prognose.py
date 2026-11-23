"""Reine Rechenlogik: Bereinigung, Hochrechnung, Kosten, Abschlagsempfehlung.

Bewusst ohne Netzwerk-/I/O-Abhängigkeiten, damit alles testbar bleibt.
"""

from __future__ import annotations

import calendar
import math
import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta

# Vereinfachtes Monatsprofil für den Gasverbrauch eines Haushalts (Heizung +
# Warmwasser), Anteile in Prozent des Jahresverbrauchs. Angelehnt an
# typische Standardlastprofile; wird nur genutzt, wenn keine Vorjahresdaten
# vorliegen.
GAS_MONATSPROFIL = {
    1: 16.0, 2: 14.0, 3: 12.0, 4: 8.0, 5: 4.5, 6: 2.5,
    7: 2.0, 8: 2.0, 9: 3.5, 10: 8.0, 11: 12.0, 12: 15.5,
}

MIN_ABDECKUNG_VORJAHR = 0.9
FAKTOR_GRENZEN = (0.5, 2.0)


@dataclass
class Bereinigung:
    werte: dict[date, float]
    entfernt: list[tuple[date, float, str]] = field(default_factory=list)
    fehlende_tage: int = 0


@dataclass
class Mengenprognose:
    ist: float
    rest: float
    methode: str
    rest_je_tag: dict[date, float]
    hinweise: list[str] = field(default_factory=list)

    @property
    def gesamt(self) -> float:
        return self.ist + self.rest


@dataclass
class Abrechnung:
    kosten_prognose: float
    gezahlt: float
    anzahl_gezahlt: int
    abschlag_aktuell: float
    offene_abschlaege: int
    saldo: float  # > 0: Nachzahlung, < 0: Guthaben
    empfehlung_rest: float | None  # Abschlag für die restlichen Raten
    empfehlung_folgejahr: float


def abrechnungszeitraum(beginn: date, heute: date) -> tuple[date, date]:
    """Liefert [start, ende) des Abrechnungsjahres, in dem `heute` liegt.

    `beginn` darf ein beliebiger (auch älterer oder zukünftiger) Beginn eines
    Abrechnungsjahres sein; maßgeblich sind Monat und Tag.
    """

    def am(jahr: int) -> date:
        tag = min(beginn.day, calendar.monthrange(jahr, beginn.month)[1])
        return date(jahr, beginn.month, tag)

    start = am(heute.year)
    if start > heute:
        start = am(heute.year - 1)
    return start, am(start.year + 1)


def ein_jahr_frueher(tag: date) -> date:
    try:
        return tag.replace(year=tag.year - 1)
    except ValueError:  # 29. Februar
        return tag.replace(year=tag.year - 1, day=28)


def ein_jahr_spaeter(tag: date) -> date:
    try:
        return tag.replace(year=tag.year + 1)
    except ValueError:  # 29. Februar
        return tag.replace(year=tag.year + 1, day=28)


def vorjahr_je_monat(vorjahr_werte: dict[date, float]) -> dict[str, float]:
    """Vorjahresmengen, um ein Jahr verschoben, als Monatssummen ("JJJJ-MM")."""
    monate: dict[str, float] = {}
    for t, v in vorjahr_werte.items():
        z = ein_jahr_spaeter(t)
        k = f"{z.year}-{z.month:02d}"
        monate[k] = monate.get(k, 0.0) + v
    return monate


def tage(start: date, ende: date):
    t = start
    while t < ende:
        yield t
        t += timedelta(days=1)


def bereinigen(
    werte: dict[date, float], start: date, ende: date, max_pro_tag: float | None = None
) -> Bereinigung:
    """Entfernt negative Tageswerte (Zählertausch/Reset) und unplausible Spitzen.

    Spitzen direkt nach einer Datenlücke bleiben erhalten: Bei kumulierten
    Zählern holt der erste Wert nach einer Lücke den Verbrauch der Lücke nach.
    """
    ergebnis: dict[date, float] = {}
    entfernt: list[tuple[date, float, str]] = []
    fehlend = 0
    vorher_fehlend = False
    for t in tage(start, ende):
        if t not in werte or werte[t] is None:
            fehlend += 1
            vorher_fehlend = True
            continue
        w = float(werte[t])
        if w < 0:
            entfernt.append((t, w, "negativ"))
        elif max_pro_tag is not None and w > max_pro_tag and not vorher_fehlend:
            entfernt.append((t, w, f"über {max_pro_tag:g} pro Tag"))
        else:
            ergebnis[t] = w
        vorher_fehlend = False
    return Bereinigung(ergebnis, entfernt, fehlend)


def _profil_gewicht(t: date) -> float:
    return GAS_MONATSPROFIL[t.month] / calendar.monthrange(t.year, t.month)[1]


def prognose_menge(
    ist_werte: dict[date, float],
    vorjahr_werte: dict[date, float],
    start: date,
    ende: date,
    heute: date,
    methode: str = "auto",
    sparte: str = "strom",
) -> Mengenprognose:
    """Hochrechnung des Verbrauchs bis Ende des Abrechnungsjahres.

    `ist_werte` enthält Tagesmengen ab `start` bis einschließlich gestern,
    `vorjahr_werte` Tagesmengen des Vorjahreszeitraums.
    """
    hinweise: list[str] = []
    ist = sum(v for t, v in ist_werte.items() if start <= t < heute)
    rest_tage = list(tage(heute, ende))
    vergangene_tage = list(tage(start, heute))

    if not rest_tage:
        return Mengenprognose(ist, 0.0, "abgeschlossen", {}, hinweise)

    # Vorjahresabdeckung prüfen
    vj_zeitraum = [ein_jahr_frueher(t) for t in tage(start, ende)]
    abdeckung = (
        sum(1 for t in vj_zeitraum if t in vorjahr_werte) / len(vj_zeitraum)
        if vj_zeitraum
        else 0.0
    )

    gewaehlt = methode
    if methode == "auto":
        if abdeckung >= MIN_ABDECKUNG_VORJAHR:
            gewaehlt = "vorjahr"
        elif sparte == "gas":
            gewaehlt = "profil"
        else:
            gewaehlt = "linear"
    elif methode == "vorjahr" and abdeckung < MIN_ABDECKUNG_VORJAHR:
        hinweise.append(
            f"Vorjahresdaten nur zu {abdeckung:.0%} vorhanden – "
            + ("Profil" if sparte == "gas" else "linear")
            + " verwendet."
        )
        gewaehlt = "profil" if sparte == "gas" else "linear"

    tage_mit_daten = sum(1 for t in vergangene_tage if t in ist_werte)
    if tage_mit_daten < 14:
        hinweise.append(
            f"Erst {tage_mit_daten} Tage Daten im Abrechnungsjahr – Prognose noch unsicher."
        )

    rest_je_tag: dict[date, float] = {}

    if gewaehlt == "vorjahr":
        vj_ist = sum(vorjahr_werte.get(ein_jahr_frueher(t), 0.0) for t in vergangene_tage)
        faktor = ist / vj_ist if vj_ist > 0 and ist > 0 else 1.0
        unten, oben = FAKTOR_GRENZEN
        if not unten <= faktor <= oben:
            hinweise.append(
                f"Verbrauch weicht stark vom Vorjahr ab (Faktor {faktor:.2f}), begrenzt."
            )
            faktor = min(max(faktor, unten), oben)
        vj_werte = list(vorjahr_werte.values())
        ersatz = statistics.fmean(vj_werte) if vj_werte else 0.0
        for t in rest_tage:
            rest_je_tag[t] = vorjahr_werte.get(ein_jahr_frueher(t), ersatz) * faktor
        gewaehlt = f"vorjahr (Faktor {faktor:.2f})"

    elif gewaehlt == "profil":
        anteil_ist = sum(_profil_gewicht(t) for t in vergangene_tage if t in ist_werte)
        # Menge je Prozentpunkt des Jahresprofils
        je_prozent = ist / anteil_ist if anteil_ist > 0 else 0.0
        for t in rest_tage:
            rest_je_tag[t] = _profil_gewicht(t) * je_prozent

    else:  # linear
        gewaehlt = "linear"
        mittel = ist / tage_mit_daten if tage_mit_daten else 0.0
        for t in rest_tage:
            rest_je_tag[t] = mittel

    return Mengenprognose(ist, sum(rest_je_tag.values()), gewaehlt, rest_je_tag, hinweise)


def kosten(menge: float, preis_eur_einheit: float, grundpreis_eur_monat: float) -> float:
    return menge * preis_eur_einheit + grundpreis_eur_monat * 12


@dataclass
class Tarif:
    gueltig_ab: date
    gueltig_bis: date | None  # einschließlich; None = unbefristet
    preis_eur_einheit: float
    grundpreis_eur_monat: float | None = None
    einspeiseverguetung_eur_kwh: float = 0.0
    name: str | None = None

    def gilt_am(self, t: date) -> bool:
        return self.gueltig_ab <= t and (self.gueltig_bis is None or t <= self.gueltig_bis)


@dataclass
class Kostenrechnung:
    kosten: float
    arbeitskosten: float
    grundkosten: float
    tage_ohne_tarif: int
    verwendete_tarife: list[Tarif]


def kosten_tagesgenau(
    menge_je_tag: dict[date, float],
    tarife: list[Tarif],
    start: date,
    ende: date,
    ersatz_preis: float,
    ersatz_grundpreis_monat: float,
    faktor: float = 1.0,
) -> Kostenrechnung:
    """Kosten mit dem am jeweiligen Tag gültigen Tarif (Preisänderungen im Jahr).

    Der Grundpreis wird tageweise anteilig berechnet (Monatspreis × 12 / Tage
    im Abrechnungsjahr). Tage ohne passenden Tarif nutzen die Ersatzwerte.
    """
    jahrestage = (ende - start).days
    arbeit = grund = 0.0
    ohne = 0
    genutzt: list[Tarif] = []
    for t in tage(start, ende):
        tarif = next((x for x in reversed(tarife) if x.gilt_am(t)), None)
        if tarif is None:
            ohne += 1
            preis, gp = ersatz_preis, ersatz_grundpreis_monat
        else:
            preis = tarif.preis_eur_einheit
            gp = tarif.grundpreis_eur_monat if tarif.grundpreis_eur_monat else ersatz_grundpreis_monat
            if tarif not in genutzt:
                genutzt.append(tarif)
        arbeit += menge_je_tag.get(t, 0.0) * faktor * preis
        grund += gp * 12 / jahrestage
    return Kostenrechnung(arbeit + grund, arbeit, grund, ohne, genutzt)


def offene_raten_kalender(
    heute: date, ende: date, letzte_zahlung: date | None, abschlaege_pro_jahr: int
) -> int:
    """Wie viele Abschläge sind bis zum Ende des Abrechnungsjahres noch fällig?

    Gezählt werden der laufende Monat (falls darin noch nicht gezahlt wurde) und
    alle folgenden Monate, deren Fälligkeitstag (Tag der letzten Zahlung, sonst
    der 1.) vor dem Ende des Abrechnungsjahres liegt. Bei weniger als 12 Abschlägen im Jahr
    wird anteilig gerundet.
    """
    tag = letzte_zahlung.day if letzte_zahlung else 1
    bezahlt_monat = (letzte_zahlung.year, letzte_zahlung.month) if letzte_zahlung else None
    anzahl = 0
    jahr, monat = heute.year, heute.month
    while True:
        faellig = date(jahr, monat, min(tag, calendar.monthrange(jahr, monat)[1]))
        if faellig >= ende:
            break
        # Laufender Monat: offen, solange darin noch nichts gebucht ist (auch wenn
        # der Fälligkeitstag schon vorbei ist – die Buchung kann nachkommen).
        if (jahr, monat) != bezahlt_monat:
            anzahl += 1
        monat += 1
        if monat > 12:
            jahr, monat = jahr + 1, 1
    if abschlaege_pro_jahr < 12:
        anzahl = math.ceil(anzahl * abschlaege_pro_jahr / 12)
    return anzahl


def abrechnung(
    kosten_prognose: float,
    zahlungen: list[float],
    abschlag_konfig: float,
    abschlaege_pro_jahr: int,
    heute: date | None = None,
    ende: date | None = None,
    zahlungsdaten: list[date] | None = None,
) -> Abrechnung:
    gezahlt = sum(zahlungen)
    anzahl = len(zahlungen)
    abschlag_aktuell = abschlag_konfig if abschlag_konfig > 0 else (zahlungen[-1] if zahlungen else 0.0)
    offen = max(0, abschlaege_pro_jahr - anzahl)
    if heute is not None and ende is not None:
        letzte = max(zahlungsdaten) if zahlungsdaten else None
        offen = min(offen, offene_raten_kalender(heute, ende, letzte, abschlaege_pro_jahr))
    saldo = kosten_prognose - (gezahlt + offen * abschlag_aktuell)
    empfehlung_rest = math.ceil(max(0.0, kosten_prognose - gezahlt) / offen) if offen else None
    empfehlung_folgejahr = math.ceil(kosten_prognose / abschlaege_pro_jahr)
    return Abrechnung(
        kosten_prognose=round(kosten_prognose, 2),
        gezahlt=round(gezahlt, 2),
        anzahl_gezahlt=anzahl,
        abschlag_aktuell=round(abschlag_aktuell, 2),
        offene_abschlaege=offen,
        saldo=round(saldo, 2),
        empfehlung_rest=empfehlung_rest,
        empfehlung_folgejahr=empfehlung_folgejahr,
    )


def monatsverlauf(
    ist_werte: dict[date, float], rest_je_tag: dict[date, float], start: date, ende: date
) -> list[dict]:
    """Monatssummen (Ist / Prognose) und kumulierte Werte für das Diagramm."""
    monate: dict[tuple[int, int], dict] = {}
    for t in tage(start, ende):
        m = monate.setdefault((t.year, t.month), {"monat": f"{t.year}-{t.month:02d}", "ist": 0.0, "prognose": 0.0})
        if t in rest_je_tag:
            m["prognose"] += rest_je_tag[t]
        else:
            m["ist"] += ist_werte.get(t, 0.0)
    kumuliert = 0.0
    liste = []
    for m in monate.values():
        kumuliert += m["ist"] + m["prognose"]
        liste.append(
            {
                "monat": m["monat"],
                "ist": round(m["ist"], 3),
                "prognose": round(m["prognose"], 3),
                "kumuliert": round(kumuliert, 3),
            }
        )
    return liste
