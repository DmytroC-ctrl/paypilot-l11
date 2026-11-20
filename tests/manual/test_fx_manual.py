"""Ручний baseline L11 (крок 1). Пишіть тести тут, асистента не відкривайте.

Модуль під тестом: app/engines/fx.py — функція fx.quote(...).
Очікувані значення беріть із таблиці тарифів app/engines/policy.py,
а не з виводу самої функції.

    fx.quote(amount, from_currency, to_currency, tier, allowance_used_eur=0.0)
    tier: "tier1" | "tier2" | "tier3"
    повертає FxQuote: spread_pct, allowance_applied, gross_amount,
                      spread_amount, final_amount, ...
"""
import pytest

from app.engines import fx

def test_usd_to_eur_within_allowance_uses_inverse_rate():
    q = fx.quote(500, "USD", "EUR", "tier2")
    assert q.allowance_applied is True
    assert q.spread_pct == 0.0
    assert q.final_amount == pytest.approx(500 * 0.92)


def test_quote_on_exact_allowance_boundary_is_spread_free():
    q = fx.quote(300, "EUR", "USD", "tier2", allowance_used_eur=700.0)
    assert q.allowance_applied is True
    assert q.spread_amount == 0.0
    assert q.final_amount == pytest.approx(300 / 0.92)


def test_quote_with_used_allowance_exceeding_limit_applies_spread():
    q = fx.quote(400, "EUR", "USD", "tier2", allowance_used_eur=700.0)
    assert q.allowance_applied is False
    assert q.spread_pct == 0.9
    assert q.final_amount == pytest.approx(400 / 0.92 * (1 - 0.009))


def test_partial_allowance_charges_spread_only_on_excess():
    q = fx.quote(500, "EUR", "USD", "tier2", allowance_used_eur=800.0, partial_allowance=True)
    gross = 500 / 0.92
    expected_spread = gross * (300 / 500) * 0.009
    assert q.allowance_applied is False
    assert q.spread_amount == pytest.approx(expected_spread)
    assert q.final_amount == pytest.approx(gross - expected_spread)


def test_spread_pct_override_replaces_default_tier_spread():
    q = fx.quote(1500, "EUR", "USD", "tier2", spread_pct_override=0.25)
    assert q.spread_pct == 0.25
    assert q.final_amount == pytest.approx(1500 / 0.92 * (1 - 0.0025))


def test_unknown_currency_and_tier_raise_value_error():
    with pytest.raises(ValueError, match="Unknown currency"):
        fx.mid_rate("EUR", "UNKNOWN")
    with pytest.raises(ValueError, match="Unknown tier"):
        fx.quote(100, "EUR", "USD", "unknown_tier")


def test_quote_as_dict_rounds_float_values():
    q = fx.quote(100, "EUR", "USD", "tier2")
    data = q.as_dict()
    assert data["mid_rate"] == round(1 / 0.92, 6)
    assert data["final_amount"] == round(100 / 0.92, 6)
    assert data["allowance_applied"] is True
