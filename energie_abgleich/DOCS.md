# Energie-Abgleich

Rechnet für **Strom, Gas und Wasser** hoch, was die Jahresabrechnung voraussichtlich
ergibt – aus den Zählern in Home Assistant und den tatsächlich gezahlten Abschlägen
in **Firefly III**.

## Woher kommen die Daten?

| Was | Quelle |
|---|---|
| Verbrauch | Langzeitstatistik der Zähler, die im **Energie-Dashboard** hinterlegt sind (oder `zaehler` in den Optionen) |
| Zahlungen | Firefly-Buchungen der jeweiligen **Kategorie** (`Strom`, `Gas`, `Gelsenwasser`) im Abrechnungsjahr |
| Preise | Add-on-Optionen (Arbeitspreis, Grundpreis) |

## Einrichtung

1. In Firefly unter *Optionen → Profil → OAuth* einen **Personal Access Token** anlegen.
2. In den Add-on-Optionen `firefly_url` und `firefly_token` eintragen.
3. Je Sparte eintragen:
   - `abrechnungsbeginn`: Beginn eines Abrechnungsjahres (Datum der letzten Jahresabrechnung,
     z. B. `2026-03-01`). Es zählen nur Tag und Monat.
   - Arbeits-/Mengenpreis und Grundpreis je Monat (brutto, von der Rechnung).
   - `abschlag_eur`: aktueller Abschlag. `0` = letzter Betrag aus Firefly.
   - Gas: `brennwert` und `zustandszahl` von der letzten Jahresabrechnung.
   - Wasser: `abwasser_eur_m3`, falls Abwasser mit abgerechnet wird; `max_m3_pro_tag` als
     Plausibilitätsgrenze.
4. Add-on starten. Die Oberfläche erscheint als **Energie-Abgleich** in der Seitenleiste.

## Abschläge in Firefly

- Gezählt werden **Ausgaben** der Kategorie im Abrechnungsjahr.
- Buchungen mit einem der `ausschluss_stichworte` in der Beschreibung (z. B. „Nachzahlung“,
  „Erstattung“) werden ignoriert – das sind meist Abrechnungen des Vorjahres.
- In der Oberfläche siehst du unter *Details*, welche Buchungen gezählt und welche
  ignoriert wurden.

## Prognose-Methoden

| Methode | Wann |
|---|---|
| `vorjahr` | Verlauf des Vorjahres, skaliert auf den bisherigen Verbrauch. Automatisch, wenn ≥ 90 % Vorjahresdaten vorhanden sind. |
| `profil` | Gas ohne Vorjahresdaten: typisches Heiz-Monatsprofil (Winter deutlich höher). |
| `linear` | Durchschnitt pro Tag × Resttage. |
| `auto` | wählt wie oben beschrieben. |

Hinweis Strom: Seit der PV-Anlage sinkt der Netzbezug – Vorjahreswerte ohne PV sind nur
bedingt vergleichbar. Der Skalierungsfaktor fängt das teilweise auf; sonst `linear` wählen.

## Sensoren

Pro Sparte ein Gerät **Energie-Abgleich Strom/Gas/Wasser** mit u. a.:

- `sensor.energie_abgleich_<sparte>_saldo_prognose` – positiv = Nachzahlung, negativ = Erstattung
- `sensor.energie_abgleich_<sparte>_kosten_prognose`
- `sensor.energie_abgleich_<sparte>_gezahlt`
- `sensor.energie_abgleich_<sparte>_abschlag_empfehlung` (restliche Monate)
- `sensor.energie_abgleich_<sparte>_abschlag_empfehlung_folgejahr`
- `sensor.energie_abgleich_<sparte>_verbrauch_bisher` / `_verbrauch_prognose`

Hinweise (z. B. Datenlücken) stehen im Attribut `hinweise`.

Mit Mosquitto werden die Sensoren per MQTT-Discovery angelegt; ohne MQTT setzt das Add-on
sie direkt über die REST-API (dann nach einem HA-Neustart erst wieder nach der nächsten
Berechnung vorhanden).

### Beispiel-Automation

```yaml
alias: Warnung hohe Nachzahlung Strom
triggers:
  - trigger: numeric_state
    entity_id: sensor.energie_abgleich_strom_saldo_prognose
    above: 100
actions:
  - action: notify.notify
    data:
      message: >-
        Strom: voraussichtlich {{ states('sensor.energie_abgleich_strom_saldo_prognose') }} €
        Nachzahlung. Empfohlener Abschlag:
        {{ states('sensor.energie_abgleich_strom_abschlag_empfehlung') }} €.
```
