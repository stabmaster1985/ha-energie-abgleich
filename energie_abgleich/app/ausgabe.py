"""Ergebnisse als Sensoren nach Home Assistant bringen (MQTT-Discovery oder REST)."""

from __future__ import annotations

import json
import logging
import os

import aiohttp
import paho.mqtt.client as mqtt

from .homeassistant import HomeAssistant

LOG = logging.getLogger(__name__)

PRAEFIX = "energie_abgleich"

# schluessel -> (Name, Einheit oder "{einheit}", device_class, icon)
SENSOREN = {
    "verbrauch_bisher": ("Verbrauch bisher", "{einheit}", None, "mdi:counter"),
    "verbrauch_prognose": ("Verbrauch Prognose", "{einheit}", None, "mdi:chart-line"),
    "kosten_prognose": ("Kosten Prognose", "EUR", "monetary", "mdi:cash"),
    "gezahlt": ("Abschläge gezahlt", "EUR", "monetary", "mdi:cash-check"),
    "saldo_prognose": ("Saldo Prognose", "EUR", "monetary", "mdi:scale-balance"),
    "abschlag_empfehlung": ("Abschlag Empfehlung", "EUR", "monetary", "mdi:cash-sync"),
    "abschlag_empfehlung_folgejahr": ("Abschlag Empfehlung Folgejahr", "EUR", "monetary", "mdi:calendar-arrow-right"),
    "einspeisung_prognose": ("Einspeisevergütung Prognose", "EUR", "monetary", "mdi:solar-power"),
    "einspeisung_erhalten": ("Einspeisevergütung erhalten", "EUR", "monetary", "mdi:solar-power-variant"),
}


def sensor_werte(ergebnis: dict) -> dict:
    """Flacht ein Sparten-Ergebnis auf die Sensorwerte ab."""
    a = ergebnis.get("abrechnung") or {}
    m = ergebnis.get("menge") or {}
    e = ergebnis.get("einspeisung") or {}
    werte = {
        "verbrauch_bisher": m.get("ist"),
        "verbrauch_prognose": m.get("gesamt"),
        "kosten_prognose": a.get("kosten_prognose"),
        "gezahlt": a.get("gezahlt"),
        "saldo_prognose": a.get("saldo"),
        "abschlag_empfehlung": a.get("empfehlung_rest"),
        "abschlag_empfehlung_folgejahr": a.get("empfehlung_folgejahr"),
    }
    if e:
        werte["einspeisung_prognose"] = e.get("verguetung_prognose")
        werte["einspeisung_erhalten"] = e.get("erhalten")
    return werte


def sensor_attribute(ergebnis: dict) -> dict:
    a = ergebnis.get("abrechnung") or {}
    return {
        "zeitraum_start": ergebnis.get("zeitraum", {}).get("start"),
        "zeitraum_ende": ergebnis.get("zeitraum", {}).get("ende"),
        "methode": (ergebnis.get("menge") or {}).get("methode"),
        "offene_abschlaege": a.get("offene_abschlaege"),
        "abschlag_aktuell": a.get("abschlag_aktuell"),
        "hinweise": ergebnis.get("hinweise", []),
        "fehler": ergebnis.get("fehler"),
        "aktualisiert": ergebnis.get("aktualisiert"),
    }


async def mqtt_zugang(sitzung: aiohttp.ClientSession) -> dict | None:
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return None
    try:
        async with sitzung.get(
            "http://supervisor/services/mqtt",
            headers={"Authorization": f"Bearer {token}"},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as r:
            if r.status != 200:
                return None
            return (await r.json()).get("data")
    except aiohttp.ClientError:
        return None


class Ausgabe:
    def __init__(self, ha: HomeAssistant, mqtt_daten: dict | None):
        self._ha = ha
        self._client: mqtt.Client | None = None
        self._angekuendigt: set[str] = set()
        if mqtt_daten:
            c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=PRAEFIX)
            if mqtt_daten.get("username"):
                c.username_pw_set(mqtt_daten["username"], mqtt_daten.get("password"))
            if mqtt_daten.get("ssl"):
                c.tls_set()
            c.will_set(f"{PRAEFIX}/status", "offline", retain=True)
            c.connect_async(mqtt_daten["host"], int(mqtt_daten.get("port", 1883)))
            c.loop_start()
            c.publish(f"{PRAEFIX}/status", "online", retain=True)
            self._client = c
            LOG.info("MQTT aktiv (%s:%s)", mqtt_daten["host"], mqtt_daten.get("port"))
        else:
            LOG.info("Kein MQTT gefunden – Sensoren werden über die REST-API gesetzt.")

    def _ankuendigen(self, sparte: str, titel: str, einheit: str, schluessel: str) -> None:
        name, e, klasse, icon = SENSOREN[schluessel]
        uid = f"{PRAEFIX}_{sparte}_{schluessel}"
        if uid in self._angekuendigt or self._client is None:
            return
        konfig = {
            "name": name,
            "unique_id": uid,
            "default_entity_id": f"sensor.{uid}",
            "state_topic": f"{PRAEFIX}/{sparte}/zustand",
            "value_template": f"{{{{ value_json.{schluessel} }}}}",
            "json_attributes_topic": f"{PRAEFIX}/{sparte}/attribute",
            "availability_topic": f"{PRAEFIX}/status",
            "unit_of_measurement": e.format(einheit=einheit),
            "icon": icon,
            "device": {
                "identifiers": [f"{PRAEFIX}_{sparte}"],
                "name": f"Energie-Abgleich {titel}",
                "manufacturer": "ha-energie-abgleich",
                "model": "Abrechnungsprognose",
            },
        }
        if klasse:
            konfig["device_class"] = klasse
        self._client.publish(
            f"homeassistant/sensor/{uid}/config", json.dumps(konfig), retain=True
        )
        self._angekuendigt.add(uid)

    async def veroeffentlichen(self, sparte: str, titel: str, einheit: str, ergebnis: dict) -> None:
        werte = sensor_werte(ergebnis)
        attribute = sensor_attribute(ergebnis)
        if self._client is not None:
            for schluessel in werte:
                self._ankuendigen(sparte, titel, einheit, schluessel)
            self._client.publish(f"{PRAEFIX}/{sparte}/zustand", json.dumps(werte), retain=True)
            self._client.publish(f"{PRAEFIX}/{sparte}/attribute", json.dumps(attribute), retain=True)
            return
        for schluessel, wert in werte.items():
            name, e, klasse, icon = SENSOREN[schluessel]
            attr = {
                "friendly_name": f"Energie-Abgleich {titel} {name}",
                "unit_of_measurement": e.format(einheit=einheit),
                "icon": icon,
                **attribute,
            }
            if klasse:
                attr["device_class"] = klasse
            await self._ha.zustand_setzen(
                f"sensor.{PRAEFIX}_{sparte}_{schluessel}",
                "unknown" if wert is None else wert,
                attr,
            )

    def stoppen(self) -> None:
        if self._client is not None:
            self._client.publish(f"{PRAEFIX}/status", "offline", retain=True)
            self._client.loop_stop()
            self._client.disconnect()
