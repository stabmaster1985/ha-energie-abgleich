"""Einstiegspunkt: periodische Berechnung + Web-Oberfläche (Ingress)."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path

import aiohttp
from aiohttp import web

from . import konfiguration
from .ausgabe import Ausgabe, mqtt_zugang
from .berechnung import sparte_berechnen
from .firefly import Firefly
from .homeassistant import HomeAssistant

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
LOG = logging.getLogger("energie_abgleich")

STATIC = Path(__file__).parent / "static"
PORT = int(os.environ.get("PORT", "8099"))


class Dienst:
    def __init__(self) -> None:
        self.konfig = konfiguration.laden()
        self.status: dict = {"sparten": [], "aktualisiert": None, "fehler": None, "laeuft": False}
        self._sperre = asyncio.Lock()
        self._sitzung: aiohttp.ClientSession | None = None
        self.ha: HomeAssistant | None = None
        self.firefly: Firefly | None = None
        self.ausgabe: Ausgabe | None = None

    async def starten(self) -> None:
        self._sitzung = aiohttp.ClientSession()
        self.ha = HomeAssistant(self._sitzung)
        if self.konfig.firefly_url and self.konfig.firefly_token:
            self.firefly = Firefly(self._sitzung, self.konfig.firefly_url, self.konfig.firefly_token)
        self.ausgabe = Ausgabe(self.ha, await mqtt_zugang(self._sitzung))

    async def stoppen(self) -> None:
        if self.ausgabe:
            self.ausgabe.stoppen()
        if self.ha:
            await self.ha.schliessen()
        if self._sitzung:
            await self._sitzung.close()

    async def aktualisieren(self) -> None:
        if self._sperre.locked():
            return
        async with self._sperre:
            assert self.ha is not None and self.ausgabe is not None
            self.status["laeuft"] = True
            try:
                await self.ha.verbinden()
                erkannt = await self.ha.energie_zaehler()
                sparten = []
                for sparte in self.konfig.sparten:
                    try:
                        ergebnis = await sparte_berechnen(
                            sparte, self.konfig, self.ha, self.firefly, erkannt
                        )
                    except Exception as e:  # noqa: BLE001
                        LOG.exception("Fehler bei %s", sparte.titel)
                        ergebnis = {
                            "sparte": sparte.name,
                            "titel": sparte.titel,
                            "einheit": sparte.einheit,
                            "fehler": str(e) or e.__class__.__name__,
                            "hinweise": [],
                        }
                    sparten.append(ergebnis)
                    await self.ausgabe.veroeffentlichen(
                        sparte.name, sparte.titel, sparte.einheit, ergebnis
                    )
                self.status.update(sparten=sparten, fehler=None)
            except Exception as e:  # noqa: BLE001
                LOG.exception("Aktualisierung fehlgeschlagen")
                self.status["fehler"] = str(e) or e.__class__.__name__
                await self.ha.schliessen()
            finally:
                self.status["aktualisiert"] = datetime.now().isoformat(timespec="seconds")
                self.status["laeuft"] = False

    async def schleife(self) -> None:
        while True:
            await self.aktualisieren()
            await asyncio.sleep(self.konfig.aktualisierung_minuten * 60)


async def index(_: web.Request) -> web.FileResponse:
    return web.FileResponse(STATIC / "index.html")


def app_bauen(dienst: Dienst) -> web.Application:
    app = web.Application()

    async def status(_: web.Request) -> web.Response:
        return web.json_response(dienst.status)

    async def aktualisieren(_: web.Request) -> web.Response:
        asyncio.create_task(dienst.aktualisieren())
        return web.json_response({"ok": True})

    app.router.add_get("/", index)
    app.router.add_get("/api/status", status)
    app.router.add_post("/api/aktualisieren", aktualisieren)
    return app


async def main() -> None:
    dienst = Dienst()
    await dienst.starten()
    runner = web.AppRunner(app_bauen(dienst))
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    LOG.info("Web-Oberfläche auf Port %s", PORT)
    try:
        await dienst.schleife()
    finally:
        await runner.cleanup()
        await dienst.stoppen()


if __name__ == "__main__":
    asyncio.run(main())
