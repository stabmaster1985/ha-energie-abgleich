"""Führt für eine Sparte Daten aus Home Assistant und Firefly zusammen."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from . import prognose as p
from .eedc import EEDCTarife
from .firefly import Firefly, klassifizieren
from .homeassistant import HomeAssistant
from .konfiguration import Konfiguration, Sparte

LOG = logging.getLogger(__name__)

EINHEITEN = {"energy": "kWh", "volume": "m³"}

# Faktor auf m³, wenn die Rohwerte als diese Einheit gelesen werden sollen
VOLUMEN_FAKTOR = {"m³": 1.0, "L": 0.001}


def _buchung_dict(b) -> dict:
    return {"datum": b.datum.isoformat(), "betrag": b.betrag, "beschreibung": b.beschreibung}


async def sparte_berechnen(
    sparte: Sparte,
    konfig: Konfiguration,
    ha: HomeAssistant,
    firefly: Firefly | None,
    erkannte_zaehler: dict[str, str | None],
    eedc: EEDCTarife | None = None,
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
    if not meta:
        ergebnis["fehler"] = (
            f"Für „{zaehler}“ gibt es in Home Assistant keine Langzeitstatistik. "
            + ("Der Zähler ist in den Add-on-Optionen eingetragen – Schreibweise prüfen "
               "(Entwicklerwerkzeuge → Statistiken zeigt die gültigen IDs)."
               if sparte.zaehler else
               "Er stammt aus dem Energie-Dashboard; bitte dort prüfen, ob der Zähler noch existiert.")
        )
        return ergebnis
    einheit_zaehler = meta.get("statistics_unit_of_measurement") or ""
    vorjahr_start = p.ein_jahr_frueher(start)
    if sparte.zaehler_einheit in VOLUMEN_FAKTOR:
        # Einheit in HA falsch (z. B. „L“, obwohl der Zähler m³ liefert):
        # Rohwerte holen und so lesen, wie in den Optionen angegeben.
        roh = await ha.tagesmengen(zaehler, vorjahr_start, heute, {})
        umrechnung = VOLUMEN_FAKTOR[sparte.zaehler_einheit]
        roh = {t: v * umrechnung for t, v in roh.items()}
        if einheit_zaehler != sparte.zaehler_einheit:
            ergebnis["hinweise"].append(
                f"Zähler ist in Home Assistant mit „{einheit_zaehler}“ angelegt, "
                f"Werte werden laut Optionen als „{sparte.zaehler_einheit}“ gelesen."
            )
        einheit_zaehler = "m³"
    else:
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
    if (
        sparte.name == "wasser"
        and sparte.zaehler_einheit == "auto"
        and einheit_zaehler == "L"
        and len(ber.werte) >= 14
        and mp.gesamt < 5
    ):
        ergebnis["hinweise"].append(
            f"Unplausibel wenig Wasser ({mp.gesamt:.3f} m³/Jahr). Der Zähler ist in Home Assistant "
            "mit „L“ angelegt – liefert er eigentlich m³? Dann in den Optionen "
            "wasser → zaehler_einheit auf „m³“ stellen oder die Einheit in HA korrigieren."
        )

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
    vj_monate = p.vorjahr_je_monat(vj.werte)
    ergebnis["verlauf"] = [
        {**m, "ist": round(m["ist"] * faktor, 2), "prognose": round(m["prognose"] * faktor, 2),
         "kumuliert": round(m["kumuliert"] * faktor, 2),
         "vorjahr": round(vj_monate[m["monat"]] * faktor, 2) if m["monat"] in vj_monate else None}
        for m in p.monatsverlauf(ber.werte, mp.rest_je_tag, start, ende)
    ]
    # --- Preise ----------------------------------------------------------
    tarife: list[p.Tarif] = []
    preisquelle = "Add-on-Optionen"
    if sparte.name == "strom" and sparte.preisquelle == "eedc":
        if eedc is not None:
            tarife = eedc.tarife
            preisquelle = f"EEDC ({eedc.url})"
        else:
            ergebnis["hinweise"].append(
                "EEDC nicht erreichbar – Arbeitspreis aus den Add-on-Optionen verwendet."
            )
    rechnung = p.kosten_tagesgenau(
        {**ber.werte, **mp.rest_je_tag},
        tarife,
        start,
        ende,
        sparte.preis_eur_einheit,
        sparte.grundpreis_eur_monat,
        faktor,
    )
    if tarife and rechnung.tage_ohne_tarif:
        ergebnis["hinweise"].append(
            f"Für {rechnung.tage_ohne_tarif} Tage ist in EEDC kein Tarif hinterlegt – "
            "dort gilt der Preis aus den Add-on-Optionen."
        )
    kosten = rechnung.kosten

    # --- Vorjahr: Verbrauch und Kosten -----------------------------------
    vj_tage = (start - vorjahr_start).days
    vj_abdeckung = len(vj.werte) / vj_tage if vj_tage else 0.0
    vj_rechnung = p.kosten_tagesgenau(
        vj.werte, tarife, vorjahr_start, start,
        sparte.preis_eur_einheit, sparte.grundpreis_eur_monat, faktor,
    )
    vj_menge = sum(vj.werte.values()) * faktor
    vorjahr: dict = {
        "zeitraum": {"start": vorjahr_start.isoformat(),
                     "ende": (start - timedelta(days=1)).isoformat()},
        "abdeckung": round(vj_abdeckung, 3),
        "menge": round(vj_menge, 2),
        "veraenderung": (
            round((mp.gesamt * faktor - vj_menge) / vj_menge, 4)
            if vj_menge > 0 and vj_abdeckung >= p.MIN_ABDECKUNG_VORJAHR else None
        ),
        "kosten": round(vj_rechnung.kosten, 2) if vj.werte else None,
        "preis_hinweis": (
            "zu den damaligen Preisen aus EEDC"
            if tarife and vj_rechnung.tage_ohne_tarif == 0
            else "zu den aktuellen Preisen aus den Optionen"
            if not tarife else
            f"EEDC-Preise, {vj_rechnung.tage_ohne_tarif} Tage zu den Preisen aus den Optionen"
        ),
        "gezahlt": None,
        "anzahl_gezahlt": None,
        "saldo": None,
        "abrechnung": None,
        "abrechnung_buchungen": [],
    }
    if 0 < vj_abdeckung < 0.9:
        ergebnis["hinweise"].append(
            f"Vorjahr nur zu {vj_abdeckung:.0%} mit Zählerdaten abgedeckt – Vorjahreswerte unvollständig."
        )

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
            # Abrechnung des Vorjahres = im laufenden Jahr gebuchte Nachzahlungen
            # (Ausgaben) bzw. Erstattungen (Einnahmen) mit Ausschluss-Stichwort.
            abr = [(b, g) for b, g in k.ignoriert if g == "Ausschluss-Stichwort"]
            if abr:
                vorjahr["abrechnung"] = round(
                    sum(b.betrag if b.typ == "withdrawal" else -b.betrag for b, _ in abr), 2
                )
                vorjahr["abrechnung_buchungen"] = [
                    {**_buchung_dict(b), "typ": b.typ} for b, _ in abr
                ]
            vj_buchungen = await firefly.buchungen(
                sparte.kategorie, vorjahr_start, start - timedelta(days=1)
            )
            vk = klassifizieren(vj_buchungen, konfig.ausschluss_stichworte, None)
            vorjahr["gezahlt"] = round(sum(b.betrag for b in vk.abschlaege), 2)
            vorjahr["anzahl_gezahlt"] = len(vk.abschlaege)
            if vorjahr["kosten"] is not None:
                vorjahr["saldo"] = round(vorjahr["kosten"] - vorjahr["gezahlt"], 2)
        except Exception as e:  # noqa: BLE001 – Fehler anzeigen statt abbrechen
            LOG.warning("Firefly (%s): %s", sparte.kategorie, e)
            ergebnis["hinweise"].append(f"Firefly: {e}")

    a = p.abrechnung(
        kosten,
        [b.betrag for b in abschlaege],
        sparte.abschlag_eur,
        sparte.abschlaege_pro_jahr,
        heute=heute,
        ende=ende,
        zahlungsdaten=[b.datum for b in abschlaege],
    )
    if abschlaege and len(abschlaege) + 2 < (heute - start).days * sparte.abschlaege_pro_jahr / 365:
        ergebnis["hinweise"].append(
            f"Nur {len(abschlaege)} Abschläge in Firefly gefunden – erwartet wären etwa "
            f"{round((heute - start).days * sparte.abschlaege_pro_jahr / 365)}. Unter „Details → "
            "Nicht berücksichtigt“ prüfen, ob Zahlungen fehlen oder falsch kategorisiert sind."
        )
    ergebnis["abrechnung"] = a.__dict__
    ergebnis["preise"] = {
        "quelle": preisquelle,
        "tarife": [
            {
                "name": t.name,
                "gueltig_ab": t.gueltig_ab.isoformat(),
                "gueltig_bis": t.gueltig_bis.isoformat() if t.gueltig_bis else None,
                "preis_eur_einheit": t.preis_eur_einheit,
                "grundpreis_eur_monat": t.grundpreis_eur_monat,
            }
            for t in rechnung.verwendete_tarife
        ]
        or [
            {
                "name": None,
                "gueltig_ab": None,
                "gueltig_bis": None,
                "preis_eur_einheit": sparte.preis_eur_einheit,
                "grundpreis_eur_monat": sparte.grundpreis_eur_monat,
            }
        ],
        "arbeitskosten": round(rechnung.arbeitskosten, 2),
        "grundkosten": round(rechnung.grundkosten, 2),
    }

    ergebnis["vorjahr"] = vorjahr

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
            satz = sparte.einspeiseverguetung_eur_kwh
            if sparte.preisquelle == "eedc" and eedc and eedc.einspeiseverguetung_eur_kwh:
                satz = eedc.einspeiseverguetung_eur_kwh
            ergebnis["einspeisung"] = {
                "zaehler": ez,
                "menge_ist": round(ep.ist, 2),
                "menge_prognose": round(ep.gesamt, 2),
                "verguetung_soll_bisher": round(ep.ist * satz, 2),
                "verguetung_prognose": round(ep.gesamt * satz, 2),
                "satz_eur_kwh": satz,
                "erhalten": round(sum(b.betrag for b in einspeisung_buchungen), 2),
                "buchungen": [_buchung_dict(b) for b in einspeisung_buchungen],
            }

    return ergebnis
