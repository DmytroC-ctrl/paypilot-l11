"""Unit-тести для app/engines/fx.py.

Константи policy підміняються на контрольовані значення, тому тести перевіряють
логіку fx.py (курс, спред, ліміт, часткове застосування ліміту, комісії),
а не поточні бізнес-значення з policy.py.

Підмінені значення:
    1 USD = 0.5 EUR, 1 GBP = 2.0 EUR, 1 EUR = 1 EUR
    basic: ліміт 1000 EUR, спред 2.0 %
    pro:   ліміт 5000 EUR, спред 0.5 %
"""
import pytest

from app.engines import fx, policy

RATES = {"EUR": 1.0, "USD": 0.5, "GBP": 2.0}
ALLOWANCE = {"basic": 1000.0, "pro": 5000.0}
SPREAD = {"basic": 2.0, "pro": 0.5}
TRANSFER_FEES = {"sepa": (0.0, 0.0), "swift": (5.0, 0.1), "instant": (0.0, 1.0)}


@pytest.fixture(autouse=True)
def stub_policy(monkeypatch):
    monkeypatch.setattr(policy, "RATES_TO_EUR", RATES)
    monkeypatch.setattr(policy, "TIERS", ("basic", "pro"))
    monkeypatch.setattr(policy, "FX_FREE_MONTHLY_ALLOWANCE_EUR", ALLOWANCE)
    monkeypatch.setattr(policy, "FX_SPREAD_PCT", SPREAD)
    monkeypatch.setattr(policy, "TRANSFER_FEES", TRANSFER_FEES)
    monkeypatch.setattr(policy, "to_eur", lambda amount, cur: amount * RATES[cur])


# --------------------------------------------------------------------------
# mid_rate
# --------------------------------------------------------------------------

class TestMidRate:
    def test_to_eur(self):
        assert fx.mid_rate("USD", "EUR") == pytest.approx(0.5)

    def test_from_eur(self):
        assert fx.mid_rate("EUR", "USD") == pytest.approx(2.0)

    def test_cross_rate(self):
        assert fx.mid_rate("GBP", "USD") == pytest.approx(4.0)

    def test_same_currency_is_one(self):
        assert fx.mid_rate("USD", "USD") == pytest.approx(1.0)

    def test_inverse_rates_multiply_to_one(self):
        assert fx.mid_rate("GBP", "USD") * fx.mid_rate("USD", "GBP") == pytest.approx(1.0)

    def test_unknown_source_currency(self):
        with pytest.raises(ValueError, match="Unknown currency: XXX"):
            fx.mid_rate("XXX", "EUR")

    def test_unknown_target_currency(self):
        with pytest.raises(ValueError, match="Unknown currency: YYY"):
            fx.mid_rate("EUR", "YYY")

    def test_currency_codes_are_case_sensitive(self):
        with pytest.raises(ValueError, match="Unknown currency: usd"):
            fx.mid_rate("usd", "EUR")


# --------------------------------------------------------------------------
# quote: валідація
# --------------------------------------------------------------------------

class TestQuoteValidation:
    def test_unknown_tier(self):
        with pytest.raises(ValueError, match="Unknown tier: gold"):
            fx.quote(100, "USD", "EUR", "gold")

    def test_unknown_currency(self):
        with pytest.raises(ValueError, match="Unknown currency: XXX"):
            fx.quote(100, "XXX", "EUR", "basic")

    def test_unknown_tier_checked_before_currency(self):
        with pytest.raises(ValueError, match="Unknown tier"):
            fx.quote(100, "XXX", "EUR", "gold")


# --------------------------------------------------------------------------
# quote: в межах безкоштовного ліміту
# --------------------------------------------------------------------------

class TestQuoteWithinAllowance:
    def test_no_spread(self):
        q = fx.quote(1000, "USD", "EUR", "basic")  # 500 EUR < 1000 EUR
        assert q.allowance_applied is True
        assert q.spread_pct == 0.0
        assert q.spread_amount == 0.0
        assert q.gross_amount == pytest.approx(500.0)
        assert q.final_amount == pytest.approx(500.0)

    def test_exactly_at_limit_is_within(self):
        q = fx.quote(2000, "USD", "EUR", "basic")  # рівно 1000 EUR
        assert q.allowance_applied is True
        assert q.spread_amount == 0.0

    def test_exactly_at_limit_with_prior_usage_is_within(self):
        q = fx.quote(1000, "USD", "EUR", "basic", allowance_used_eur=500.0)  # 500 + 500
        assert q.allowance_applied is True
        assert q.spread_amount == 0.0

    def test_zero_amount(self):
        q = fx.quote(0, "USD", "EUR", "basic")
        assert q.allowance_applied is True
        assert q.gross_amount == 0.0
        assert q.final_amount == 0.0

    def test_override_ignored_when_within(self):
        q = fx.quote(1000, "USD", "EUR", "basic", spread_pct_override=9.0)
        assert q.spread_pct == 0.0
        assert q.spread_amount == 0.0

    def test_higher_tier_has_larger_allowance(self):
        # 3000 USD = 1500 EUR: поза лімітом basic, але в межах pro
        assert fx.quote(3000, "USD", "EUR", "basic").allowance_applied is False
        assert fx.quote(3000, "USD", "EUR", "pro").allowance_applied is True


# --------------------------------------------------------------------------
# quote: поза лімітом
# --------------------------------------------------------------------------

class TestQuoteOverAllowance:
    def test_tier_spread_applied_to_whole_amount(self):
        q = fx.quote(3000, "USD", "EUR", "basic")  # 1500 EUR > 1000 EUR
        assert q.allowance_applied is False
        assert q.spread_pct == 2.0
        assert q.gross_amount == pytest.approx(1500.0)
        assert q.spread_amount == pytest.approx(30.0)
        assert q.final_amount == pytest.approx(1470.0)

    def test_just_over_limit(self):
        q = fx.quote(2000.02, "USD", "EUR", "basic")  # 1000.01 EUR
        assert q.allowance_applied is False
        assert q.spread_pct == 2.0

    def test_prior_usage_pushes_over_limit(self):
        q = fx.quote(1000, "USD", "EUR", "basic", allowance_used_eur=600.0)  # 600 + 500
        assert q.allowance_applied is False
        assert q.spread_amount == pytest.approx(10.0)  # 500 * 2 %
        assert q.final_amount == pytest.approx(490.0)

    def test_spread_depends_on_tier(self):
        q = fx.quote(20000, "USD", "EUR", "pro")  # 10000 EUR > 5000 EUR
        assert q.spread_pct == 0.5
        assert q.spread_amount == pytest.approx(50.0)

    def test_override_replaces_tier_spread(self):
        q = fx.quote(3000, "USD", "EUR", "basic", spread_pct_override=1.0)
        assert q.spread_pct == 1.0
        assert q.spread_amount == pytest.approx(15.0)
        assert q.final_amount == pytest.approx(1485.0)

    def test_zero_override_is_respected(self):
        # 0.0 — валідний override, а не "не задано"
        q = fx.quote(3000, "USD", "EUR", "basic", spread_pct_override=0.0)
        assert q.spread_pct == 0.0
        assert q.spread_amount == 0.0
        assert q.final_amount == pytest.approx(q.gross_amount)

    def test_cross_currency_conversion(self):
        q = fx.quote(1000, "GBP", "USD", "basic")  # 2000 EUR > 1000 EUR, rate 4
        assert q.mid_rate == pytest.approx(4.0)
        assert q.gross_amount == pytest.approx(4000.0)
        assert q.spread_amount == pytest.approx(80.0)
        assert q.final_amount == pytest.approx(3920.0)

    def test_final_is_gross_minus_spread(self):
        q = fx.quote(12345.67, "GBP", "USD", "basic", allowance_used_eur=123.0)
        assert q.final_amount == pytest.approx(q.gross_amount - q.spread_amount)


# --------------------------------------------------------------------------
# quote: partial_allowance
# --------------------------------------------------------------------------

class TestQuotePartialAllowance:
    def test_only_excess_is_charged(self):
        # used 600, amount 500 EUR: лишається 400, обкладається 100 EUR (20 %)
        q = fx.quote(1000, "USD", "EUR", "basic",
                     allowance_used_eur=600.0, partial_allowance=True)
        assert q.allowance_applied is False
        assert q.spread_pct == 2.0
        assert q.spread_amount == pytest.approx(500.0 * 0.2 * 2.0 / 100.0)
        assert q.final_amount == pytest.approx(498.0)

    def test_partial_is_cheaper_than_full_charge(self):
        kwargs = dict(allowance_used_eur=600.0)
        partial = fx.quote(1000, "USD", "EUR", "basic", partial_allowance=True, **kwargs)
        full = fx.quote(1000, "USD", "EUR", "basic", partial_allowance=False, **kwargs)
        assert partial.spread_amount < full.spread_amount

    def test_without_flag_whole_amount_is_charged(self):
        q = fx.quote(1000, "USD", "EUR", "basic", allowance_used_eur=600.0)
        assert q.spread_amount == pytest.approx(10.0)

    def test_allowance_already_exhausted_charges_everything(self):
        q = fx.quote(1000, "USD", "EUR", "basic",
                     allowance_used_eur=1500.0, partial_allowance=True)
        assert q.spread_amount == pytest.approx(10.0)  # 500 * 2 %

    def test_allowance_exactly_exhausted_charges_everything(self):
        q = fx.quote(1000, "USD", "EUR", "basic",
                     allowance_used_eur=1000.0, partial_allowance=True)
        assert q.allowance_applied is False
        assert q.spread_amount == pytest.approx(10.0)

    def test_flag_has_no_effect_when_within_limit(self):
        q = fx.quote(1000, "USD", "EUR", "basic", partial_allowance=True)
        assert q.allowance_applied is True
        assert q.spread_amount == 0.0
        assert q.final_amount == pytest.approx(500.0)

    def test_partial_with_override(self):
        q = fx.quote(1000, "USD", "EUR", "basic", allowance_used_eur=600.0,
                     spread_pct_override=5.0, partial_allowance=True)
        assert q.spread_pct == 5.0
        assert q.spread_amount == pytest.approx(500.0 * 0.2 * 5.0 / 100.0)

    def test_zero_amount_over_limit_does_not_divide_by_zero(self):
        q = fx.quote(0, "USD", "EUR", "basic",
                     allowance_used_eur=1500.0, partial_allowance=True)
        assert q.allowance_applied is False
        assert q.spread_amount == 0.0
        assert q.final_amount == 0.0


# --------------------------------------------------------------------------
# quote: поля результату
# --------------------------------------------------------------------------

class TestQuoteFields:
    def test_input_echoed(self):
        q = fx.quote(3000, "USD", "GBP", "basic", allowance_used_eur=100.0)
        assert (q.from_currency, q.to_currency) == ("USD", "GBP")
        assert q.amount == 3000
        assert q.tier == "basic"
        assert q.allowance_used_before_eur == 100.0
        assert q.mid_rate == pytest.approx(0.25)

    def test_allowance_total_comes_from_tier(self):
        assert fx.quote(1, "USD", "EUR", "basic").allowance_total_eur == 1000.0
        assert fx.quote(1, "USD", "EUR", "pro").allowance_total_eur == 5000.0


# --------------------------------------------------------------------------
# FxQuote.as_dict
# --------------------------------------------------------------------------

class TestAsDict:
    def make(self, **over):
        base = dict(
            from_currency="USD", to_currency="EUR", amount=1.0, mid_rate=0.5,
            tier="basic", spread_pct=2.0, allowance_total_eur=1000.0,
            allowance_used_before_eur=0.0, allowance_applied=False,
            gross_amount=0.5, spread_amount=0.01, final_amount=0.49)
        base.update(over)
        return fx.FxQuote(**base)

    def test_keys_match_dataclass_fields(self):
        assert set(self.make().as_dict()) == {
            "from_currency", "to_currency", "amount", "mid_rate", "tier",
            "spread_pct", "allowance_total_eur", "allowance_used_before_eur",
            "allowance_applied", "gross_amount", "spread_amount", "final_amount"}

    def test_floats_rounded_to_six_places(self):
        d = self.make(mid_rate=0.123456789, final_amount=1.0000004).as_dict()
        assert d["mid_rate"] == 0.123457
        assert d["final_amount"] == 1.0

    def test_non_floats_untouched(self):
        d = self.make(allowance_applied=True).as_dict()
        assert d["allowance_applied"] is True
        assert d["tier"] == "basic"
        assert d["from_currency"] == "USD"

    def test_int_amount_not_rounded_or_converted(self):
        d = self.make(amount=1234).as_dict()
        assert d["amount"] == 1234
        assert isinstance(d["amount"], int)

    def test_from_real_quote(self):
        d = fx.quote(3000, "USD", "EUR", "basic").as_dict()
        assert d["spread_amount"] == 30.0
        assert d["final_amount"] == 1470.0
        assert d["allowance_applied"] is False


# --------------------------------------------------------------------------
# transfer_fee
# --------------------------------------------------------------------------

class TestTransferFee:
    def test_flat_plus_percent(self):
        r = fx.transfer_fee(1000.0, "swift")  # 5 + 0.1 % * 1000
        assert r["total_fee_eur"] == 6.0
        assert r["flat_fee_eur"] == 5.0
        assert r["percent_fee"] == 0.1

    def test_percent_only(self):
        assert fx.transfer_fee(250.0, "instant")["total_fee_eur"] == 2.5

    def test_free_transfer(self):
        assert fx.transfer_fee(10000.0, "sepa")["total_fee_eur"] == 0.0

    def test_zero_amount_charges_flat_fee_only(self):
        assert fx.transfer_fee(0.0, "swift")["total_fee_eur"] == 5.0

    def test_total_rounded_to_two_places(self):
        r = fx.transfer_fee(333.33, "instant")  # 3.3333
        assert r["total_fee_eur"] == 3.33

    def test_echoes_input(self):
        r = fx.transfer_fee(42.0, "swift")
        assert r["transfer_type"] == "swift"
        assert r["amount_eur"] == 42.0

    def test_result_keys(self):
        assert set(fx.transfer_fee(1.0, "sepa")) == {
            "transfer_type", "amount_eur", "flat_fee_eur",
            "percent_fee", "total_fee_eur"}

    def test_unknown_transfer_type(self):
        with pytest.raises(ValueError, match="Unknown transfer type: carrier_pigeon"):
            fx.transfer_fee(100.0, "carrier_pigeon")
