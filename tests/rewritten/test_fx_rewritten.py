"""Переписані вручну тести (L11, крок 5).

Кожен тест повторює правило відхиленого згенерованого тесту (журнал рев'ю, розділ 5),
але з реальними тірами й числами з таблиці тарифів. policy тут не імпортується й не
підміняється: очікувані значення записано числами (tier1 1.5 % / 500 EUR, tier2 0.9 % /
1 000 EUR, tier3 0.5 % / 5 000 EUR; курси USD 0.92, GBP 1.17, PLN 0.23, CHF 1.05,
UAH 0.021; збори internal 0/0, sepa 0.35/0, swift 15/0.3 %, card 0/1.2 %).
"""
import pytest

from app.engines import fx


# R-01 <- G-13, G-14: рівно на ліміті свого тіру спред не береться
@pytest.mark.parametrize("tier, used, amount", [
    ("tier1", 0, 500),
    ("tier2", 0, 1000),
    ("tier3", 0, 5000),
    ("tier3", 4000, 1000),
])
def test_amount_exactly_at_tier_allowance_is_spread_free(tier, used, amount):
    q = fx.quote(amount, "EUR", "EUR", tier, allowance_used_eur=used)
    assert q.allowance_applied is True
    assert q.spread_pct == 0.0
    assert q.spread_amount == 0.0
    assert q.final_amount == pytest.approx(amount)


# R-02 <- G-19: на один цент вище за ліміт береться спред тіру
@pytest.mark.parametrize("tier, limit, spread", [
    ("tier1", 500, 1.5),
    ("tier2", 1000, 0.9),
    ("tier3", 5000, 0.5),
])
def test_one_cent_over_allowance_pays_tier_spread(tier, limit, spread):
    amount = limit + 0.01
    q = fx.quote(amount, "EUR", "EUR", tier)
    assert q.allowance_applied is False
    assert q.spread_pct == spread
    assert q.spread_amount == pytest.approx(amount * spread / 100)


# R-03 <- G-17, G-18, G-21, G-35: у кожного тіру свій спред і свій безкоштовний ліміт
@pytest.mark.parametrize("tier, spread, allowance", [
    ("tier1", 1.5, 500),
    ("tier2", 0.9, 1000),
    ("tier3", 0.5, 5000),
])
def test_each_tier_has_own_spread_and_allowance(tier, spread, allowance):
    q = fx.quote(10000, "EUR", "EUR", tier)  # 10 000 EUR вище за ліміт будь-якого тіру
    assert q.spread_pct == spread
    assert q.allowance_total_eur == allowance
    assert q.spread_amount == pytest.approx(10000 * spread / 100)
    assert q.final_amount == pytest.approx(10000 * (1 - spread / 100))


# R-04 <- G-24: крос-курс GBP -> USD поза лімітом
def test_cross_currency_gbp_to_usd_over_allowance():
    q = fx.quote(1000, "GBP", "USD", "tier2")  # 1000 GBP = 1170 EUR > 1000 EUR
    gross = 1000 * 1.17 / 0.92
    assert q.mid_rate == pytest.approx(1.17 / 0.92)
    assert q.allowance_applied is False
    assert q.gross_amount == pytest.approx(gross)
    assert q.spread_amount == pytest.approx(gross * 0.9 / 100)
    assert q.final_amount == pytest.approx(gross * (1 - 0.009))


# R-05 <- G-29, G-30: ліміт вичерпано (рівно або з перевищенням), частковий режим бере спред з усієї суми
@pytest.mark.parametrize("used", [1000, 1500])
def test_partial_allowance_exhausted_charges_whole_amount(used):
    q = fx.quote(500, "EUR", "EUR", "tier2",
                 allowance_used_eur=used, partial_allowance=True)
    assert q.allowance_applied is False
    assert q.spread_amount == pytest.approx(500 * 0.9 / 100)  # 4.5
    assert q.final_amount == pytest.approx(500 - 4.5)


# R-06 <- G-32: частковий ліміт разом з override
def test_partial_allowance_with_override_charges_excess_at_override():
    # використано 800, лишається 200 EUR, обкладається 300 EUR із 500 (60 %)
    q = fx.quote(500, "EUR", "EUR", "tier2", allowance_used_eur=800,
                 spread_pct_override=5.0, partial_allowance=True)
    assert q.spread_pct == 5.0
    assert q.spread_amount == pytest.approx(500 * 0.6 * 5.0 / 100)  # 15.0
    assert q.final_amount == pytest.approx(485.0)


# R-07 <- G-31: прапорець часткового ліміту нічого не змінює в межах ліміту
def test_partial_flag_has_no_effect_within_allowance():
    q = fx.quote(500, "EUR", "USD", "tier2", partial_allowance=True)
    assert q.allowance_applied is True
    assert q.spread_amount == 0.0
    assert q.final_amount == pytest.approx(500 / 0.92)


# R-08 <- G-01, G-02, G-03: mid_rate за реальною таблицею курсів
@pytest.mark.parametrize("src, dst, expected", [
    ("USD", "EUR", 0.92),
    ("GBP", "EUR", 1.17),
    ("PLN", "EUR", 0.23),
    ("CHF", "EUR", 1.05),
    ("UAH", "EUR", 0.021),
    ("EUR", "USD", 1 / 0.92),
    ("GBP", "USD", 1.17 / 0.92),
])
def test_mid_rate_follows_tariff_table(src, dst, expected):
    assert fx.mid_rate(src, dst) == pytest.approx(expected)


# R-09 <- G-41, G-43, G-44: загальний збір переказу за реальною таблицею
@pytest.mark.parametrize("kind, amount, total", [
    ("swift", 1000.0, 18.0),    # 15 + 0.3 % * 1000
    ("swift", 0.0, 15.0),       # лише фіксована частина
    ("sepa", 10000.0, 0.35),    # 0.35 фіксовано, 0 %
    ("sepa", 0.0, 0.35),
    ("card", 250.0, 3.0),       # 1.2 % * 250
    ("internal", 5000.0, 0.0),  # безкоштовно
])
def test_transfer_fee_total_matches_tariff(kind, amount, total):
    assert fx.transfer_fee(amount, kind)["total_fee_eur"] == total


# R-10 <- G-41, G-42: складники збору в результаті
@pytest.mark.parametrize("kind, flat, pct", [
    ("swift", 15.0, 0.3),
    ("sepa", 0.35, 0.0),
    ("card", 0.0, 1.2),
    ("internal", 0.0, 0.0),
])
def test_transfer_fee_reports_flat_and_percent_components(kind, flat, pct):
    r = fx.transfer_fee(100.0, kind)
    assert r["flat_fee_eur"] == flat
    assert r["percent_fee"] == pct


# R-11 <- G-45: загальний збір округлюється до двох знаків
def test_transfer_fee_total_rounded_to_two_places():
    # 111.11 * 1.2 / 100 = 1.33332
    assert fx.transfer_fee(111.11, "card")["total_fee_eur"] == 1.33
