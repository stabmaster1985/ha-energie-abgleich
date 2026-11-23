# Energie-Abgleich – Home Assistant Add-on

Prognose der Jahresabrechnung für **Strom, Gas und Wasser**: Verbrauch aus Home Assistant
(Energie-Dashboard) + gezahlte Abschläge aus **Firefly III** → voraussichtliche Nachzahlung
oder Erstattung und passender Abschlag. Ergänzt [EEDC](https://github.com/supernova1963/eedc-homeassistant),
das die technische PV-Auswertung übernimmt.

## Installation

1. Home Assistant → *Einstellungen → Add-ons → Add-on Store* → ⋮ → *Repositories*
2. `https://github.com/stabmaster1985/ha-energie-abgleich` hinzufügen
3. **Energie-Abgleich** installieren, Optionen ausfüllen, starten

Details: [energie_abgleich/DOCS.md](energie_abgleich/DOCS.md)

## Fahrplan

- [x] Stufe 1: Prognose + Abschlagsempfehlung (HA + Firefly)
- [ ] Stufe 2: Tarife & Preisanpassungen aus Paperless, Einspeisevergütung prüfen
- [ ] Stufe 3: Jahresabrechnung gegen eigene Zählerdaten prüfen

## Entwicklung

```bash
pip install aiohttp paho-mqtt pytest pytest-asyncio
pytest
```
