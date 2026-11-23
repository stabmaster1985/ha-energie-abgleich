from datetime import date, timedelta

import pytest

from app import prognose as p


def test_abrechnungszeitraum_mitten_im_jahr():
    assert p.abrechnungszeitraum(date(2024, 7, 1), date(2026, 11, 23)) == (
        date(2026, 7, 1),
        date(2027, 7, 1),
    )


def test_abrechnungszeitraum_vor_stichtag():
    assert p.abrechnungszeitraum(date(2026, 12, 1), date(2026, 11, 23)) == (
        date(2025, 12, 1),
        date(2026, 12, 1),
    )


def test_abrechnungszeitraum_29_februar():
    start, ende = p.abrechnungszeitraum(date(2024, 2, 29), date(2026, 3, 10))
    assert start == date(2026, 2, 28) and ende == date(2027, 2, 28)


def test_bereinigen_entfernt_negative_und_spitzen_aber_nicht_nach_luecke():
    start = date(2026, 1, 1)
    werte = {
        date(2026, 1, 1): 0.3,
        date(2026, 1, 2): -5.0,  # Zählertausch
        date(2026, 1, 3): 9.0,  # Spitze -> raus
        # 4. fehlt
        date(2026, 1, 5): 0.6,  # Nachholwert nach Lücke -> bleibt
    }
    b = p.bereinigen(werte, start, date(2026, 1, 6), max_pro_tag=0.5)
    assert set(b.werte) == {date(2026, 1, 1), date(2026, 1, 5)}
    assert [g for _, _, g in b.entfernt][0] == "negativ"
    assert len(b.entfernt) == 2
    assert b.fehlende_tage == 1


def _konstant(start, ende, wert):
    return {t: wert for t in p.tage(start, ende)}


def test_linear():
    start, ende, heute = date(2026, 1, 1), date(2027, 1, 1), date(2026, 7, 2)
    ist = _konstant(start, heute, 10.0)
    mp = p.prognose_menge(ist, {}, start, ende, heute, "auto", "strom")
    assert mp.methode == "linear"
    assert mp.gesamt == pytest.approx(3650.0)


def test_vorjahr_mit_faktor():
    start, ende, heute = date(2026, 1, 1), date(2027, 1, 1), date(2026, 4, 1)
    vj = _konstant(date(2025, 1, 1), date(2026, 1, 1), 10.0)
    ist = _konstant(start, heute, 12.0)  # 20 % mehr als im Vorjahr
    mp = p.prognose_menge(ist, vj, start, ende, heute, "auto", "strom")
    assert mp.methode.startswith("vorjahr")
    assert mp.gesamt == pytest.approx(365 * 12.0)


def test_vorjahr_gefordert_aber_fehlend_faellt_zurueck():
    start, ende, heute = date(2026, 1, 1), date(2027, 1, 1), date(2026, 4, 1)
    mp = p.prognose_menge(_konstant(start, heute, 1.0), {}, start, ende, heute, "vorjahr", "gas")
    assert mp.methode == "profil"
    assert any("Vorjahresdaten" in h for h in mp.hinweise)


def test_gasprofil_rechnet_winter_hoeher_als_linear():
    start, ende, heute = date(2026, 1, 1), date(2027, 1, 1), date(2026, 7, 1)
    # Verbrauch genau nach Profil mit 100 m³/% => Jahr = 10000 m³ (Summe der Anteile = 100)
    ist = {t: p._profil_gewicht(t) * 100 for t in p.tage(start, heute)}
    profil = p.prognose_menge(ist, {}, start, ende, heute, "profil", "gas")
    assert profil.gesamt == pytest.approx(sum(p.GAS_MONATSPROFIL.values()) * 100, rel=1e-6)
    linear = p.prognose_menge(ist, {}, start, ende, heute, "linear", "gas")
    # Im ersten Halbjahr wird mehr verbraucht -> linear überschätzt hier
    assert linear.gesamt > profil.gesamt


def test_abrechnung_nachzahlung_und_empfehlung():
    a = p.abrechnung(kosten_prognose=1200.0, zahlungen=[80.0] * 10, abschlag_konfig=0, abschlaege_pro_jahr=12)
    assert a.gezahlt == 800.0
    assert a.offene_abschlaege == 2
    assert a.abschlag_aktuell == 80.0
    assert a.saldo == pytest.approx(240.0)
    assert a.empfehlung_rest == 200
    assert a.empfehlung_folgejahr == 100


def test_abrechnung_guthaben_ohne_offene_raten():
    a = p.abrechnung(900.0, [100.0] * 12, 0, 12)
    assert a.saldo == pytest.approx(-300.0)
    assert a.empfehlung_rest is None
    assert a.empfehlung_folgejahr == 75


def test_monatsverlauf_summiert():
    start, ende, heute = date(2026, 1, 1), date(2026, 3, 1), date(2026, 2, 1)
    ist = _konstant(start, heute, 1.0)
    rest = {t: 2.0 for t in p.tage(heute, ende)}
    v = p.monatsverlauf(ist, rest, start, ende)
    assert v[0] == {"monat": "2026-01", "ist": 31.0, "prognose": 0.0, "kumuliert": 31.0}
    assert v[1]["prognose"] == 56.0 and v[1]["kumuliert"] == 87.0


def test_ein_jahr_frueher():
    assert p.ein_jahr_frueher(date(2028, 2, 29)) == date(2027, 2, 28)
    assert p.ein_jahr_frueher(date(2026, 5, 5)) == date(2025, 5, 5)
    assert date(2026, 1, 1) - timedelta(days=1) == date(2025, 12, 31)


def test_supervisor_token_aus_s6_datei(tmp_path, monkeypatch):
    from app import homeassistant as ha

    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    (tmp_path / "SUPERVISOR_TOKEN").write_text("geheim\n")
    monkeypatch.setattr(ha, "S6_UMGEBUNG", tmp_path)
    assert ha.supervisor_token() == "geheim"
    monkeypatch.setenv("SUPERVISOR_TOKEN", "aus-env")
    assert ha.supervisor_token() == "aus-env"


def test_kosten_tagesgenau_mit_preisaenderung():
    start, ende = date(2026, 1, 1), date(2027, 1, 1)
    menge = {t: 10.0 for t in p.tage(start, ende)}
    tarife = [
        p.Tarif(date(2025, 1, 1), date(2026, 6, 30), 0.30, 12.0),
        p.Tarif(date(2026, 7, 1), None, 0.40, None),  # ohne Grundpreis -> Ersatz
    ]
    r = p.kosten_tagesgenau(menge, tarife, start, ende, 0.99, 10.0)
    tage_h1 = (date(2026, 7, 1) - start).days
    assert r.arbeitskosten == pytest.approx(tage_h1 * 10 * 0.30 + (365 - tage_h1) * 10 * 0.40)
    assert r.grundkosten == pytest.approx(tage_h1 * 144 / 365 + (365 - tage_h1) * 120 / 365)
    assert r.tage_ohne_tarif == 0 and len(r.verwendete_tarife) == 2


def test_kosten_tagesgenau_ohne_tarife_wie_pauschal():
    start, ende = date(2026, 1, 1), date(2027, 1, 1)
    menge = {t: 2.0 for t in p.tage(start, ende)}
    r = p.kosten_tagesgenau(menge, [], start, ende, 0.30, 12.0)
    assert r.kosten == pytest.approx(p.kosten(730, 0.30, 12.0))
    assert r.tage_ohne_tarif == 365


def test_eedc_tarife_aus_antwort():
    from app.eedc import standard_urls, tarife_aus_antwort

    daten = [
        {"netzbezug_arbeitspreis_cent_kwh": 31.5, "einspeiseverguetung_cent_kwh": 7.86,
         "grundpreis_euro_monat": 14.9, "gueltig_ab": "2026-01-01", "gueltig_bis": None,
         "anbieter": "Stadtwerke", "tarifname": "Fix24", "verwendung": "allgemein"},
        {"netzbezug_arbeitspreis_cent_kwh": 25.0, "einspeiseverguetung_cent_kwh": 0,
         "gueltig_ab": "2026-01-01", "verwendung": "wallbox"},
    ]
    t = tarife_aus_antwort(daten)
    assert len(t) == 1
    assert t[0].preis_eur_einheit == pytest.approx(0.315)
    assert t[0].einspeiseverguetung_eur_kwh == pytest.approx(0.0786)
    assert t[0].name == "Stadtwerke · Fix24"
    assert standard_urls()[0] == "http://bc122c22-eedc:8099"


def test_offene_raten_nach_kalender():
    ende = date(2027, 1, 1)
    # 23.11., November schon bezahlt -> nur Dezember offen
    assert p.offene_raten_kalender(date(2026, 11, 23), ende, date(2026, 11, 15), 12) == 1
    # November noch nicht gebucht -> November + Dezember
    assert p.offene_raten_kalender(date(2026, 11, 23), ende, date(2026, 10, 15), 12) == 2
    # Abrechnungsjahr bis 1.3.: Dez, Jan, Feb
    assert p.offene_raten_kalender(date(2026, 11, 23), date(2027, 3, 1), date(2026, 11, 1), 12) == 3


def test_abrechnung_kalender_begrenzt_offene_raten():
    # Wie im Screenshot: nur 1 Zahlung gefunden, aber im November -> nur Dezember offen
    a = p.abrechnung(1333.78, [80.10], 110.0, 12, heute=date(2026, 11, 23),
                     ende=date(2027, 1, 1), zahlungsdaten=[date(2026, 11, 3)])
    assert a.offene_abschlaege == 1
    assert a.saldo == pytest.approx(1333.78 - 80.10 - 110.0)
