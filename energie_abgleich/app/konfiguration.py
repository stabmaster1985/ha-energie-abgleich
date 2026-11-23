"""Liest die Add-on-Optionen (/data/options.json) in typisierte Objekte."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

OPTIONS_PFAD = Path(os.environ.get("OPTIONS_PFAD", "/data/options.json"))

SPARTEN = ("strom", "gas", "wasser")


@dataclass
class Sparte:
    name: str  # strom | gas | wasser
    aktiv: bool
    kategorie: str
    zaehler: str | None
    abrechnungsbeginn: date
    grundpreis_eur_monat: float
    abschlag_eur: float
    abschlaege_pro_jahr: int
    prognose_methode: str
    # Preis je Abrechnungseinheit (Strom/Gas: EUR/kWh, Wasser: EUR/m³)
    preis_eur_einheit: float
    # Gas
    brennwert: float = 11.2
    zustandszahl: float = 0.95
    # Wasser
    max_pro_tag: float | None = None
    # Strom: Einspeisung
    einspeisung_aktiv: bool = False
    einspeise_zaehler: str | None = None
    einspeiseverguetung_eur_kwh: float = 0.0
    einspeise_stichwort: str = "Einspeis"
    # Gas/Wasser: Zählereinheit überschreiben ("auto" = wie in Home Assistant)
    zaehler_einheit: str = "auto"
    # Strom: Preise aus EEDC oder aus den Optionen
    preisquelle: str = "manuell"

    @property
    def titel(self) -> str:
        return {"strom": "Strom", "gas": "Gas", "wasser": "Wasser"}[self.name]

    @property
    def einheit(self) -> str:
        return "m³" if self.name == "wasser" else "kWh"


@dataclass
class Konfiguration:
    firefly_url: str
    firefly_token: str
    aktualisierung_minuten: int
    ausschluss_stichworte: list[str]
    eedc_url: str | None = None
    sparten: list[Sparte] = field(default_factory=list)


def _datum(text: str) -> date:
    return date.fromisoformat(text)


def _leer_zu_none(wert: str | None) -> str | None:
    if wert is None:
        return None
    wert = wert.strip()
    return wert or None


def aus_dict(daten: dict) -> Konfiguration:
    sparten: list[Sparte] = []
    for name in SPARTEN:
        s = daten.get(name) or {}
        if not s.get("aktiv", False):
            continue
        if name == "wasser":
            preis = float(s.get("preis_eur_m3", 0)) + float(s.get("abwasser_eur_m3", 0))
        else:
            preis = float(s.get("arbeitspreis_ct_kwh", 0)) / 100
        sparten.append(
            Sparte(
                name=name,
                aktiv=True,
                kategorie=s.get("kategorie", name.capitalize()),
                zaehler=_leer_zu_none(s.get("zaehler")),
                abrechnungsbeginn=_datum(s.get("abrechnungsbeginn", "2026-01-01")),
                grundpreis_eur_monat=float(s.get("grundpreis_eur_monat", 0)),
                abschlag_eur=float(s.get("abschlag_eur", 0)),
                abschlaege_pro_jahr=int(s.get("abschlaege_pro_jahr", 12)),
                prognose_methode=s.get("prognose_methode", "auto"),
                preis_eur_einheit=preis,
                brennwert=float(s.get("brennwert", 11.2)),
                zustandszahl=float(s.get("zustandszahl", 0.95)),
                max_pro_tag=float(s["max_m3_pro_tag"]) if s.get("max_m3_pro_tag") else None,
                einspeisung_aktiv=bool(s.get("einspeisung_aktiv", False)),
                einspeise_zaehler=_leer_zu_none(s.get("einspeise_zaehler")),
                einspeiseverguetung_eur_kwh=float(s.get("einspeiseverguetung_ct_kwh", 0)) / 100,
                einspeise_stichwort=s.get("einspeise_stichwort", "Einspeis"),
                zaehler_einheit=s.get("zaehler_einheit") or "auto",
                preisquelle=s.get("preisquelle", "eedc" if name == "strom" else "manuell"),
            )
        )
    return Konfiguration(
        firefly_url=daten.get("firefly_url", "").rstrip("/"),
        firefly_token=daten.get("firefly_token", ""),
        aktualisierung_minuten=int(daten.get("aktualisierung_minuten", 60)),
        ausschluss_stichworte=[
            w.strip().lower()
            for w in daten.get("ausschluss_stichworte", "").split(",")
            if w.strip()
        ],
        eedc_url=_leer_zu_none(daten.get("eedc_url")),
        sparten=sparten,
    )


def laden(pfad: Path = OPTIONS_PFAD) -> Konfiguration:
    return aus_dict(json.loads(pfad.read_text(encoding="utf-8")))
