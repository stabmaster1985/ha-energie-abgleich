# Changelog

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
