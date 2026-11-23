"""Führt für eine Sparte Daten aus Home Assistant und Firefly zusammen."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from . import prognose as p
from .firefly import Firefly, klassifizieren
from .homeassistant import HomeAssistant
from .konfiguration import Konfiguration, Sparte

LOG = logging.getLogger(__name__)

EINHEITEN = {"energy": "kWh", "volume": "m³"}


def _buchung_dict(b) -> dict:
    return {"datum": b.datum.isoformat(), "betrag": b.betrag, "beschreibung": b.beschreibung}


async def sparte_berechnen(
    sparte: Sparte,
    konfig: Konfiguration,
    ha: HomeAssistant,
    firefly: Firefly | None,
    erkannte_zaehler: dict[str, str | None],
) -> dict:
    heute = ha.heute()
    start, ende = p.abrechnungszeitraum(sparte.abrechnungsbeginn, heute)
    ergebnis: dict = {
        "sparte": sparte.name,
        "titel": sparte.titel,
        "einheit": sparte.einheit,
        "zeitraum": {"start": start.isoformat(), "ende": (ende - timedelta(days=1)).isoformat()},
        "hinweise": [],
        "fehler": None,
        "aktualisiert": datetime.now(ha.zeitzone).isoformat(timespec="seconds"),
    }

    # --- Zähler ---------------------------------------------------------
    zaehler = sparte.zaehler or erkannte_zaehler.get(sparte.name)
    if not zaehler:
        ergebnis["fehler"] = (
            "Kein Zähler gefunden. Bitte im Energie-Dashboard hinterlegen "
            "oder in den Add-on-Optionen eintragen."
        )
        return ergebnis
    ergebnis["zaehler"] = zaehler

    meta = (await ha.metadaten([zaehler])).get(zaehler, {})
    einheit_zaehler = meta.get("statistics_unit_of_measurement") or ""
    vorjahr_start = p.ein_jahr_frueher(start)
    roh = await ha.tagesmengen(zaehler, vorjahr_start, heute, EINHEITEN)
    vorjahr_roh = {t: v for t, v in roh.items() if t < start}
    ist_roh = {t: v for t, v in roh.items() if t >= start}

    ber = p.bereinigen(ist_roh, start, heute, sparte.max_pro_tag)
    vj = p.bereinigen(vorjahr_roh, vorjahr_start, start, sparte.max_pro_tag)
    if ber.entfernt:
        ergebnis["hinweise"].append(
            f"{len(ber.entfernt)} unplausible Tageswerte ignoriert ("
            + ", ".join(f"{t:%d.%m.}: {w:g} – {g}" for t, w, g in ber.entfernt[:5])
            + (" …" if len(ber.entfernt) > 5 else "")
            + ")."
        )
    if ber.fehlende_tage:
        ergebnis["hinweise"].append(f"{ber.fehlende_tage} Tage ohne Zählerdaten.")
    ergebnis["datenqualitaet"] = {
        "fehlende_tage": ber.fehlende_tage,
        "entfernte_werte": [
            {"datum": t.isoformat(), "wert": w, "grund": g} for t, w, g in ber.entfernt
        ],
    }

    mp = p.prognose_menge(
        ber.werte, vj.werte, start, ende, heute, sparte.prognose_methode, sparte.name
    )
    ergebnis["hinweise"].extend(mp.hinweise)

    # Gas in m³ -> kWh umrechnen
    faktor = 1.0
    abrechnungseinheit = sparte.einheit
    ist_volumen = meta.get("unit_class") == "volume" or einheit_zaehler in ("m³", "ft³", "CCF", "L")
    if sparte.name == "gas" and ist_volumen:
        faktor = sparte.brennwert * sparte.zustandszahl
        ergebnis["hinweise"].append(
            f"Gas in kWh umgerechnet: m³ × {sparte.brennwert:g} × {sparte.zustandszahl:g}."
        )
    ergebnis["menge"] = {
        "ist": round(mp.ist * faktor, 2),
        "rest": round(mp.rest * faktor, 2),
        "gesamt": round(mp.gesamt * faktor, 2),
        "methode": mp.methode,
        "einheit": abrechnungseinheit,
        "zaehler_einheit": einheit_zaehler,
    }
    ergebnis["verlauf"] = [
        {**m, "ist": round(m["ist"] * faktor, 2), "prognose": round(m["prognose"] * faktor, 2),
         "kumuliert": round(m["kumuliert"] * faktor, 2)}
        for m in p.monatsverlauf(ber.werte, mp.rest_je_tag, start, ende)
    ]
    kosten = p.kosten(mp.gesamt * faktor, sparte.preis_eur_einheit, sparte.grundpreis_eur_monat)

    # --- Zahlungen ------------------------------------------------------
    abschlaege: list = []
    einspeisung_buchungen: list = []
    if firefly is None:
        ergebnis["hinweise"].append("Firefly nicht konfiguriert – Zahlungen fehlen.")
    else:
        try:
            buchungen = await firefly.buchungen(sparte.kategorie, start, ende - timedelta(days=1))
            k = klassifizieren(
                buchungen,
                konfig.ausschluss_stichworte,
                sparte.einspeise_stichwort if sparte.einspeisung_aktiv else None,
            )
            abschlaege, einspeisung_buchungen = k.abschlaege, k.einspeisung
            ergebnis["buchungen"] = {
                "abschlaege": [_buchung_dict(b) for b in k.abschlaege],
                "ignoriert": [{**_buchung_dict(b), "grund": g} for b, g in k.ignoriert],
            }
        except Exception as e:  # noqa: BLE001 – Fehler anzeigen statt abbrechen
            LOG.warning("Firefly (%s): %s", sparte.kategorie, e)
            ergebnis["hinweise"].append(f"Firefly: {e}")

    a = p.abrechnung(
        kosten, [b.betrag for b in abschlaege], sparte.abschlag_eur, sparte.abschlaege_pro_jahr
    )
    ergebnis["abrechnung"] = a.__dict__
    ergebnis["preise"] = {
        "preis_eur_einheit": sparte.preis_eur_einheit,
        "grundpreis_eur_jahr": round(sparte.grundpreis_eur_monat * 12, 2),
    }

    # --- Einspeisung (nur Strom) -----------------------------------------
    if sparte.name == "strom" and sparte.einspeisung_aktiv:
        ez = sparte.einspeise_zaehler or erkannte_zaehler.get("einspeisung")
        if not ez:
            ergebnis["hinweise"].append("Einspeisung aktiv, aber kein Einspeisezähler gefunden.")
        else:
            e_roh = await ha.tagesmengen(ez, vorjahr_start, heute, EINHEITEN)
            e_ist = p.bereinigen({t: v for t, v in e_roh.items() if t >= start}, start, heute)
            e_vj = p.bereinigen({t: v for t, v in e_roh.items() if t < start}, vorjahr_start, start)
            ep = p.prognose_menge(e_ist.werte, e_vj.werte, start, ende, heute, "auto", "einspeisung")
            ergebnis["einspeisung"] = {
                "zaehler": ez,
                "menge_ist": round(ep.ist, 2),
                "menge_prognose": round(ep.gesamt, 2),
                "verguetung_soll_bisher": round(ep.ist * sparte.einspeiseverguetung_eur_kwh, 2),
                "verguetung_prognose": round(ep.gesamt * sparte.einspeiseverguetung_eur_kwh, 2),
                "erhalten": round(sum(b.betrag for b in einspeisung_buchungen), 2),
                "buchungen": [_buchung_dict(b) for b in einspeisung_buchungen],
            }

    return ergebnis
