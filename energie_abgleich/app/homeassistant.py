"""Zugriff auf Home Assistant über die WebSocket-API (via Supervisor-Proxy)."""

from __future__ import annotations

import itertools
import logging
import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

import aiohttp

LOG = logging.getLogger(__name__)

SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
# Für Tests außerhalb des Add-ons: HA_URL=http://ha:8123 HA_TOKEN=<long-lived token>
HA_URL = os.environ.get("HA_URL", "http://supervisor/core").rstrip("/")
HA_TOKEN = os.environ.get("HA_TOKEN", SUPERVISOR_TOKEN)


class HAFehler(Exception):
    pass


def zaehler_aus_energie_prefs(prefs: dict) -> dict[str, str | None]:
    """Ermittelt die Statistik-IDs der Zähler aus der Energie-Dashboard-Konfiguration.

    Unterstützt das ältere Format (grid.flow_from/flow_to) und das neuere mit
    stat_energy_from/stat_energy_to direkt an der Netzquelle.
    """
    ergebnis: dict[str, str | None] = {"strom": None, "einspeisung": None, "gas": None, "wasser": None}
    for quelle in prefs.get("energy_sources", []):
        typ = quelle.get("type")
        if typ == "grid":
            von = [f.get("stat_energy_from") for f in quelle.get("flow_from", [])] or [quelle.get("stat_energy_from")]
            nach = [f.get("stat_energy_to") for f in quelle.get("flow_to", [])] or [quelle.get("stat_energy_to")]
            ergebnis["strom"] = ergebnis["strom"] or next((v for v in von if v), None)
            ergebnis["einspeisung"] = ergebnis["einspeisung"] or next((n for n in nach if n), None)
        elif typ in ("gas", "water"):
            schluessel = "gas" if typ == "gas" else "wasser"
            ergebnis[schluessel] = ergebnis[schluessel] or quelle.get("stat_energy_from")
    return ergebnis


def tageswerte(antwort: list[dict], zeitzone: ZoneInfo) -> dict[date, float]:
    """Wandelt die Antwort von recorder/statistics_during_period (period=day) um."""
    werte: dict[date, float] = {}
    for eintrag in antwort:
        beginn = eintrag["start"]
        if isinstance(beginn, (int, float)):
            zeitpunkt = datetime.fromtimestamp(beginn / 1000, tz=zeitzone)
        else:
            zeitpunkt = datetime.fromisoformat(beginn).astimezone(zeitzone)
        if eintrag.get("change") is not None:
            werte[zeitpunkt.date()] = float(eintrag["change"])
    return werte


class HomeAssistant:
    def __init__(self, sitzung: aiohttp.ClientSession):
        self._s = sitzung
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._ids = itertools.count(1)
        self.zeitzone = ZoneInfo("Europe/Berlin")

    @property
    def kopf(self) -> dict:
        return {"Authorization": f"Bearer {HA_TOKEN}"}

    async def verbinden(self) -> None:
        if self._ws is not None and not self._ws.closed:
            return
        ws_url = HA_URL.replace("http", "ws", 1) + "/websocket"
        self._ws = await self._s.ws_connect(ws_url, heartbeat=30)
        msg = await self._ws.receive_json()
        if msg.get("type") != "auth_required":
            raise HAFehler(f"Unerwartete Begrüßung: {msg}")
        await self._ws.send_json({"type": "auth", "access_token": HA_TOKEN})
        msg = await self._ws.receive_json()
        if msg.get("type") != "auth_ok":
            raise HAFehler("Anmeldung bei Home Assistant fehlgeschlagen.")
        konfig = await self.befehl({"type": "get_config"})
        if konfig.get("time_zone"):
            self.zeitzone = ZoneInfo(konfig["time_zone"])

    async def schliessen(self) -> None:
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

    async def befehl(self, nachricht: dict):
        if self._ws is None or self._ws.closed:
            await self.verbinden()
        assert self._ws is not None
        nid = next(self._ids)
        await self._ws.send_json({"id": nid, **nachricht})
        while True:
            msg = await self._ws.receive_json(timeout=60)
            if msg.get("id") != nid:
                continue
            if not msg.get("success", False):
                raise HAFehler(f"{nachricht['type']}: {msg.get('error')}")
            return msg.get("result")

    async def energie_zaehler(self) -> dict[str, str | None]:
        try:
            prefs = await self.befehl({"type": "energy/get_prefs"})
        except HAFehler as e:
            LOG.warning("Energie-Dashboard nicht lesbar: %s", e)
            return {}
        return zaehler_aus_energie_prefs(prefs or {})

    async def metadaten(self, statistik_ids: list[str]) -> dict[str, dict]:
        antwort = await self.befehl(
            {"type": "recorder/get_statistics_metadata", "statistic_ids": statistik_ids}
        )
        return {m["statistic_id"]: m for m in antwort or []}

    async def tagesmengen(
        self, statistik_id: str, start: date, ende: date, einheiten: dict[str, str]
    ) -> dict[date, float]:
        """Tägliche Verbrauchsänderung [start, ende) in Ortszeit."""
        beginn = datetime.combine(start, datetime.min.time(), tzinfo=self.zeitzone)
        schluss = datetime.combine(ende, datetime.min.time(), tzinfo=self.zeitzone)
        antwort = await self.befehl(
            {
                "type": "recorder/statistics_during_period",
                "start_time": beginn.isoformat(),
                "end_time": schluss.isoformat(),
                "statistic_ids": [statistik_id],
                "period": "day",
                "types": ["change"],
                "units": einheiten,
            }
        )
        return tageswerte((antwort or {}).get(statistik_id, []), self.zeitzone)

    async def zustand_setzen(self, entity_id: str, zustand, attribute: dict) -> None:
        """Fallback ohne MQTT: Sensor direkt über die REST-API setzen."""
        async with self._s.post(
            f"{HA_URL}/api/states/{entity_id}",
            headers=self.kopf,
            json={"state": zustand, "attributes": attribute},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as r:
            if r.status >= 400:
                LOG.warning("Zustand %s nicht gesetzt: HTTP %s", entity_id, r.status)

    def heute(self) -> date:
        return datetime.now(self.zeitzone).date()
