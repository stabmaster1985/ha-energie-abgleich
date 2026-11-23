"""Ende-zu-Ende-Test gegen simulierte Home-Assistant- und Firefly-Server."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from app import homeassistant as ha_mod
from app.ausgabe import Ausgabe
from app.berechnung import sparte_berechnen
from app.eedc import tarife_laden
from app.firefly import Firefly
from app.homeassistant import HomeAssistant
from app.konfiguration import aus_dict

TZ = ZoneInfo("Europe/Berlin")
HEUTE = date(2026, 11, 23)

ZAEHLER = {
    "sensor.netzbezug": ("kWh", "energy", 8.0),
    "sensor.einspeisung": ("kWh", "energy", 5.0),
    "sensor.gaszaehler": ("m³", "volume", 2.0),
    "sensor.wasserzaehler": ("m³", "volume", 0.3),
}


def fake_ha(gesetzte_zustaende: dict) -> web.Application:
    async def websocket(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({"type": "auth_required"})
        msg = await ws.receive_json()
        assert msg["access_token"] == "test-token"
        await ws.send_json({"type": "auth_ok"})
        async for m in ws:
            d = m.json()
            typ, nid = d["type"], d["id"]
            if typ == "get_config":
                res = {"time_zone": "Europe/Berlin"}
            elif typ == "energy/get_prefs":
                res = {"energy_sources": [
                    {"type": "grid", "flow_from": [{"stat_energy_from": "sensor.netzbezug"}],
                     "flow_to": [{"stat_energy_to": "sensor.einspeisung"}]},
                    {"type": "gas", "stat_energy_from": "sensor.gaszaehler"},
                    {"type": "water", "stat_energy_from": "sensor.wasserzaehler"},
                ]}
            elif typ == "recorder/get_statistics_metadata":
                res = [{"statistic_id": s, "statistics_unit_of_measurement": ZAEHLER[s][0],
                        "unit_class": ZAEHLER[s][1]} for s in d["statistic_ids"]]
            elif typ == "recorder/statistics_during_period":
                sid = d["statistic_ids"][0]
                beginn = datetime.fromisoformat(d["start_time"]).date()
                ende = datetime.fromisoformat(d["end_time"]).date()
                eintraege = []
                t = beginn
                while t < ende:
                    wert = ZAEHLER[sid][2]
                    if sid == "sensor.wasserzaehler" and t == date(2026, 3, 3):
                        wert = 55.0  # Ausreißer
                    ts = datetime.combine(t, datetime.min.time(), tzinfo=TZ).timestamp() * 1000
                    eintraege.append({"start": ts, "end": ts + 86400000, "change": wert})
                    t += timedelta(days=1)
                res = {sid: eintraege}
            else:
                await ws.send_json({"id": nid, "type": "result", "success": False,
                                    "error": {"code": "unknown_command"}})
                continue
            await ws.send_json({"id": nid, "type": "result", "success": True, "result": res})
        return ws

    async def state(request):
        gesetzte_zustaende[request.match_info["eid"]] = await request.json()
        return web.json_response({})

    app = web.Application()
    app.router.add_get("/websocket", websocket)
    app.router.add_post("/api/states/{eid}", state)
    return app


def fake_firefly() -> web.Application:
    kategorien = {"1": "Strom", "2": "Gas", "3": "Gelsenwasser"}

    def gruppe(gid, kid, typ, betrag, tag, text):
        return {"id": gid, "attributes": {"transactions": [{
            "category_id": kid, "type": typ, "amount": f"{betrag:.12f}",
            "date": f"{tag.isoformat()}T00:00:00+01:00", "description": text}]}}

    buchungen = {
        "1": [gruppe(f"s{m}", "1", "withdrawal", 90, date(2026, m, 15), "Abschlag Strom") for m in range(1, 12)]
             + [gruppe("s99", "1", "withdrawal", 312.5, date(2026, 2, 1), "Nachzahlung Jahresabrechnung 2025")],
        "2": [gruppe(f"g{m}", "2", "withdrawal", 120, date(2026, m, 1), "Abschlag Gas") for m in range(1, 12)],
        "3": [gruppe(f"w{m}", "3", "withdrawal", 35, date(2026, m, 1), "Gelsenwasser Abschlag") for m in range(1, 12)]
             + [gruppe("w99", "3", "deposit", 40, date(2026, 2, 10), "Gelsenwasser Erstattung")],
    }

    async def categories(request):
        assert request.headers["Authorization"] == "Bearer ff-token"
        return web.json_response({"data": [{"id": k, "attributes": {"name": n}} for k, n in kategorien.items()],
                                  "meta": {"pagination": {"total_pages": 1}}})

    async def transactions(request):
        alle = buchungen[request.match_info["kid"]]
        seite = int(request.query.get("page", 1))
        teile = [alle[:6], alle[6:]]  # zwei Seiten
        return web.json_response({"data": teile[seite - 1], "meta": {"pagination": {"total_pages": 2}}})

    app = web.Application()
    app.router.add_get("/api/v1/categories", categories)
    app.router.add_get("/api/v1/categories/{kid}/transactions", transactions)
    return app


def fake_eedc() -> web.Application:
    async def strompreise(_):
        return web.json_response([
            {"id": 2, "anlage_id": 1, "netzbezug_arbeitspreis_cent_kwh": 40.0, "einspeiseverguetung_cent_kwh": 8.0,
             "grundpreis_euro_monat": 12.0, "gueltig_ab": "2026-07-01", "gueltig_bis": None,
             "verwendung": "allgemein", "anbieter": "Testwerk", "tarifname": "Neu"},
            {"id": 1, "anlage_id": 1, "netzbezug_arbeitspreis_cent_kwh": 30.0, "einspeiseverguetung_cent_kwh": 8.0,
             "grundpreis_euro_monat": 12.0, "gueltig_ab": "2025-01-01", "gueltig_bis": "2026-06-30",
             "verwendung": "allgemein", "anbieter": "Testwerk", "tarifname": "Alt"},
        ])

    app = web.Application()
    app.router.add_get("/api/strompreise/", strompreise)
    return app


OPTIONEN = {
    "firefly_url": "",
    "firefly_token": "ff-token",
    "aktualisierung_minuten": 60,
    "ausschluss_stichworte": "Nachzahlung,Jahresabrechnung,Erstattung",
    "strom": {"aktiv": True, "kategorie": "Strom", "abrechnungsbeginn": "2025-01-01",
              "arbeitspreis_ct_kwh": 30.0, "grundpreis_eur_monat": 12.0, "abschlag_eur": 0,
              "abschlaege_pro_jahr": 12, "prognose_methode": "auto", "einspeisung_aktiv": True,
              "einspeiseverguetung_ct_kwh": 8.0, "einspeise_stichwort": "Einspeis"},
    "gas": {"aktiv": True, "kategorie": "Gas", "abrechnungsbeginn": "2026-01-01",
            "arbeitspreis_ct_kwh": 10.0, "grundpreis_eur_monat": 15.0, "abschlag_eur": 0,
            "abschlaege_pro_jahr": 12, "prognose_methode": "auto", "brennwert": 11.0, "zustandszahl": 1.0},
    "wasser": {"aktiv": True, "kategorie": "Gelsenwasser", "abrechnungsbeginn": "2026-01-01",
               "preis_eur_m3": 2.0, "abwasser_eur_m3": 0.0, "grundpreis_eur_monat": 8.0, "abschlag_eur": 0,
               "abschlaege_pro_jahr": 12, "prognose_methode": "auto", "max_m3_pro_tag": 2.0},
}


@pytest.mark.asyncio
async def test_alle_sparten(monkeypatch):
    zustaende: dict = {}
    async with TestServer(fake_ha(zustaende)) as ha_srv, TestServer(fake_firefly()) as ff_srv, \
            TestServer(fake_eedc()) as eedc_srv:
        monkeypatch.setattr(ha_mod, "HA_URL", str(ha_srv.make_url("")).rstrip("/"))
        monkeypatch.setattr(ha_mod, "HA_TOKEN", "test-token")
        konfig = aus_dict({**OPTIONEN, "firefly_url": str(ff_srv.make_url(""))})

        async with aiohttp.ClientSession() as s:
            ha = HomeAssistant(s)
            monkeypatch.setattr(ha, "heute", lambda: HEUTE)
            await ha.verbinden()
            erkannt = await ha.energie_zaehler()
            assert erkannt == {"strom": "sensor.netzbezug", "einspeisung": "sensor.einspeisung",
                               "gas": "sensor.gaszaehler", "wasser": "sensor.wasserzaehler"}
            ff = Firefly(s, konfig.firefly_url, konfig.firefly_token)
            eedc = await tarife_laden(s, str(eedc_srv.make_url("")), HEUTE)
            ergebnisse = {sp.name: await sparte_berechnen(sp, konfig, ha, ff, erkannt, eedc) for sp in konfig.sparten}

            strom = ergebnisse["strom"]
            assert strom["fehler"] is None
            # Vorjahr vollständig -> Vorjahresmethode, 365 Tage × 8 kWh
            assert strom["menge"]["methode"].startswith("vorjahr")
            assert strom["menge"]["gesamt"] == pytest.approx(365 * 8)
            assert strom["abrechnung"]["anzahl_gezahlt"] == 11  # Nachzahlung ignoriert
            # Preiswechsel aus EEDC zum 1.7.: 181 Tage à 30 ct, 184 Tage à 40 ct
            kosten = 181 * 8 * 0.30 + 184 * 8 * 0.40 + 12 * 12
            assert strom["preise"]["quelle"].startswith("EEDC")
            assert len(strom["preise"]["tarife"]) == 2
            assert strom["abrechnung"]["kosten_prognose"] == pytest.approx(kosten)
            assert strom["abrechnung"]["saldo"] == pytest.approx(kosten - 12 * 90)
            assert strom["einspeisung"]["menge_prognose"] == pytest.approx(365 * 5)
            assert strom["einspeisung"]["erhalten"] == 0

            gas = ergebnisse["gas"]
            # m³ -> kWh mit Brennwert 11 × Zustandszahl 1
            assert gas["menge"]["einheit"] == "kWh"
            assert gas["menge"]["ist"] == pytest.approx((HEUTE - date(2026, 1, 1)).days * 2 * 11)
            assert gas["menge"]["methode"].startswith("vorjahr")

            wasser = ergebnisse["wasser"]
            assert len(wasser["datenqualitaet"]["entfernte_werte"]) == 1
            assert wasser["abrechnung"]["anzahl_gezahlt"] == 11
            assert any("Erstattung" in b["beschreibung"] for b in wasser["buchungen"]["ignoriert"])

            # Ausgabe ohne MQTT -> REST-Fallback
            ausgabe = Ausgabe(ha, None)
            await ausgabe.veroeffentlichen("strom", "Strom", "kWh", strom)
            assert zustaende["sensor.energie_abgleich_strom_saldo_prognose"]["state"] == strom["abrechnung"]["saldo"]
            assert "sensor.energie_abgleich_strom_einspeisung_prognose" in zustaende
            await ha.schliessen()


@pytest.mark.asyncio
async def test_kategorien_nach_fehlschlag_und_diagnose():
    """Ein fehlgeschlagener erster Abruf darf den Zwischenspeicher nicht leer zurücklassen."""
    aufrufe = {"n": 0}

    async def categories(_):
        aufrufe["n"] += 1
        if aufrufe["n"] == 1:
            return web.Response(status=500)
        return web.json_response({"data": [{"id": "7", "attributes": {"name": " strom "}},
                                           {"id": "8", "attributes": {"name": "Gelsenwasser"}}],
                                  "meta": {"pagination": {"total_pages": 1}}})

    app = web.Application()
    app.router.add_get("/api/v1/categories", categories)
    async with TestServer(app) as srv, aiohttp.ClientSession() as s:
        from app.firefly import FireflyFehler

        ff = Firefly(s, str(srv.make_url("")), "t")
        with pytest.raises(FireflyFehler, match="500"):
            await ff.kategorie_id("Strom")
        assert await ff.kategorie_id("Strom") == "7"  # Groß/Klein + Leerzeichen egal
        assert await ff.kategorie_id("Gelsenwasser") == "8"
        with pytest.raises(FireflyFehler, match="„Gelsenwasser“, „ strom “"):
            await ff.kategorie_id("Gas")
