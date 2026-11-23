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
