# Energie-Abgleich

Rechnet für **Strom, Gas und Wasser** hoch, was die Jahresabrechnung voraussichtlich
ergibt – aus den Zählern in Home Assistant und den tatsächlich gezahlten Abschlägen
in **Firefly III**.

## Woher kommen die Daten?

| Was | Quelle |
|---|---|
| Verbrauch | Langzeitstatistik der Zähler, die im **Energie-Dashboard** hinterlegt sind (oder `zaehler` in den Optionen) |
| Zahlungen | Firefly-Buchungen der jeweiligen **Kategorie** (`Strom`, `Gas`, `Gelsenwasser`) im Abrechnungsjahr |
| Preise | Strom: **EEDC** (Tarife mit Gültigkeit), sonst Add-on-Optionen; Gas/Wasser: Add-on-Optionen |

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

## Strompreise aus EEDC

Mit `preisquelle: eedc` (Standard) liest das Add-on die Stromtarife aus EEDC
(*Einstellungen → Strompreise*, Verwendung „allgemein“). Ändert sich der Preis im
Abrechnungsjahr, wird jeder Tag mit dem dann gültigen Tarif gerechnet. Fehlt in EEDC
der Grundpreis oder für einzelne Tage ein Tarif, gelten die Werte aus den Optionen.

EEDC wird im internen Add-on-Netz automatisch gesucht (`http://bc122c22-eedc:8099`).
Klappt das nicht, den **Hostnamen** von der EEDC-Add-on-Seite in `eedc_url` eintragen,
z. B. `http://bc122c22-eedc:8099`. Mit `preisquelle: manuell` wird EEDC nicht abgefragt.

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

## Zählereinheit falsch (Liter statt m³)

Zeigt das Energie-Dashboard z. B. „500 L“, obwohl der Zähler 500 m³ meint, rechnet
Home Assistant beim Umrechnen durch 1000 – der Verbrauch erscheint als fast 0.

- **Schnell:** In den Optionen bei `wasser` (bzw. `gas`) `zaehler_einheit: m³` setzen.
  Die Rohwerte werden dann als m³ gelesen.
- **Sauber:** Einheit des Sensors in HA auf m³ korrigieren und unter
  *Entwicklerwerkzeuge → Statistiken* beim gemeldeten Problem „Einheit ändern“ wählen
  – **ohne** Umrechnen, sonst werden die Altwerte durch 1000 geteilt. Danach
  `zaehler_einheit` wieder auf `auto`.

## Vorjahr

Unter jeder Sparte steht ein Rückblick auf das abgeschlossene Abrechnungsjahr:

- **Verbrauch** aus der Langzeitstatistik (mit Hinweis, falls Zählerdaten fehlen)
- **Kosten berechnet** – Strom mit EEDC zu den damals gültigen Preisen, sonst zu den
  aktuellen Preisen aus den Optionen (steht jeweils dabei)
- **Abschläge gezahlt** aus Firefly im Vorjahreszeitraum
- **Saldo berechnet** = Kosten − Abschläge
- **Laut Abrechnung**: Buchungen im laufenden Jahr mit einem Ausschluss-Stichwort
  (z. B. „Nachzahlung“ als Ausgabe, „Erstattung“ als Einnahme)

Der Vergleich *berechnet ↔ laut Abrechnung* zeigt, wie gut Preise und Zähler stimmen.
Große Abweichungen deuten auf falsche Preise, fehlende Zählerdaten oder einen
abweichenden Zählerstand in der Abrechnung hin.

## Fehlersuche Firefly

| Meldung | Ursache / Lösung |
|---|---|
| *lehnt den Token ab (401) … Token-Länge unter 200* | Nicht der Personal Access Token kopiert (z. B. Client-Secret) oder abgeschnitten. In Firefly unter *Optionen → Profil → OAuth → Personal Access Tokens* einen neuen erstellen und vollständig kopieren. |
| *… Firefly selbst kennt den Token nicht* | Token gelöscht/abgelaufen oder Firefly-`APP_KEY` geändert. Neuen Token erstellen. |
| *… vorgeschalteter Proxy* | Ein Login vor Firefly (z. B. Authelia) blockt. Firefly direkt über IP:Port ansprechen. |
| *leitet … weiter nach …* | Die angezeigte Zieladresse als `firefly_url` eintragen. |
| *Kategorie … nicht gefunden* | Die Meldung listet die vorhandenen Kategorien – Namen übernehmen. |

## Sensoren

Pro Sparte ein Gerät **Energie-Abgleich Strom/Gas/Wasser** mit u. a.:

- `sensor.energie_abgleich_<sparte>_saldo_prognose` – positiv = Nachzahlung, negativ = Erstattung
- `sensor.energie_abgleich_<sparte>_kosten_prognose`
- `sensor.energie_abgleich_<sparte>_gezahlt`
- `sensor.energie_abgleich_<sparte>_abschlag_empfehlung` (restliche Monate)
- `sensor.energie_abgleich_<sparte>_abschlag_empfehlung_folgejahr`
- `sensor.energie_abgleich_<sparte>_verbrauch_bisher` / `_verbrauch_prognose`
- `sensor.energie_abgleich_<sparte>_vorjahr_verbrauch` / `_vorjahr_kosten` / `_vorjahr_saldo` / `_vorjahr_abrechnung`

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
