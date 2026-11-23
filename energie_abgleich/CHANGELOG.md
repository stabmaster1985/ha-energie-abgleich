# Changelog

## 0.3.2

- **Fix:** Offene Abschläge wurden als „pro Jahr minus gefundene Zahlungen“ gerechnet.
  Fehlten Zahlungen in Firefly, setzte die Prognose z. B. am 23.11. noch 11 Raten an.
  Jetzt zählen nur Raten, die bis Ende des Abrechnungsjahres noch fällig werden.
- Hinweis, wenn deutlich weniger Abschläge in Firefly gefunden werden als erwartet.
- **Fix:** Die Oberfläche wird nicht mehr vom Browser zwischengespeichert – nach einem
  Update erscheinen neue Funktionen (z. B. der Vorjahres-Block) sofort.
- Klare Fehlermeldung, wenn es für den Zähler keine Langzeitstatistik gibt.

## 0.3.1

- **Neu:** `zaehler_einheit` für Gas und Wasser (`auto` | `m³` | `L`). Für Zähler, die
  m³ liefern, in Home Assistant aber mit „L“ angelegt sind (Verbrauch erschien als ~0).
- Hinweis bei unplausibel geringem Wasserverbrauch mit Zählereinheit „L“.

## 0.3.0

- **Neu: Vorjahr.** Je Sparte ein Rückblick auf das abgeschlossene Abrechnungsjahr:
  Verbrauch (mit Veränderung zur aktuellen Prognose), berechnete Kosten, gezahlte
  Abschläge, berechneter Saldo und – zum Vergleich – die tatsächliche Abrechnung
  (Nachzahlung/Erstattung, die im laufenden Jahr in Firefly gebucht wurde).
- Diagramm zeigt den Vorjahresverbrauch je Monat als Marke.
- Neue Sensoren `vorjahr_verbrauch`, `vorjahr_kosten`, `vorjahr_saldo`, `vorjahr_abrechnung`.

## 0.2.2

- Eigenes Icon und Logo; Seitenleisten-Symbol jetzt eine Waage.
- Firefly-Token wird bereinigt (Leerzeichen, Zeilenumbrüche, mitkopiertes „Bearer“),
  ebenso die URL (mitkopiertes `/api` bzw. `/api/v1`).
- Weiterleitungen (z. B. http → https) werden gemeldet statt verfolgt – beim Folgen
  ginge der Token verloren und Firefly antwortete mit 401.
- Bei 401 nennt die Meldung die Token-Länge und ob Firefly selbst oder ein
  vorgeschalteter Proxy ablehnt.

## 0.2.1

- **Fix:** „Kategorie gibt es in Firefly nicht“, obwohl sie existiert. Schlug der erste
  Abruf der Kategorien fehl, blieb der Zwischenspeicher leer bis zum Neustart. Jetzt wird
  bei unbekanntem Namen neu geladen und erst nach erfolgreichem Abruf gespeichert.
- Kategorienamen werden unabhängig von Groß-/Kleinschreibung und Leerzeichen verglichen.
- Die Fehlermeldung nennt die Kategorien, die Firefly für den Token liefert.

## 0.2.0

- **Fix:** Anmeldung an Home Assistant schlug fehl („Anmeldung bei Home Assistant
  fehlgeschlagen“, „Kein MQTT gefunden“). Der Supervisor-Token wird jetzt auch aus
  `/run/s6/container_environment` gelesen, weil s6-overlay die Umgebung leert.
- **Neu:** Strompreise aus EEDC (`preisquelle: eedc`, Standard). Preisänderungen im
  Abrechnungsjahr werden tagesgenau berücksichtigt; ohne EEDC gilt der Preis aus den Optionen.
- Einspeisevergütungssatz ebenfalls aus EEDC.
- Details zeigen Preisquelle, Tarife sowie Arbeits- und Grundkosten.

## 0.1.0

- Erste Version: Prognose der Jahresabrechnung für Strom, Gas und Wasser.
- Zähler werden automatisch aus dem Energie-Dashboard übernommen.
- Abschläge aus Firefly III (je Kategorie), Ausschluss von Nachzahlungen/Erstattungen.
- Abschlagsempfehlung für die restlichen Monate und das Folgejahr.
- Einspeisevergütung (vorbereitet, standardmäßig aus).
- Sensoren per MQTT-Discovery (Fallback: REST-API), Web-Oberfläche in der Seitenleiste.
