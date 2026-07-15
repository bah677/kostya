"""Тесты ангельского взноса: слоты и weighted random."""

from __future__ import annotations

import random

import pytest

from bot.services.angel_pool_service import (
    AngelPoolCandidate,
    compute_extension_slots,
    min_amount_for_currency,
    parse_donation_amount,
    pick_angel_pool_winners,
)


class TestParseDonationAmount:
    def test_integers(self):
        assert parse_donation_amount("7000") == 7000.0
        assert parse_donation_amount("7 000") == 7000.0
        assert parse_donation_amount("7000₽") == 7000.0

    def test_decimal(self):
        assert parse_donation_amount("100.50") == 100.5
        assert parse_donation_amount("100,50") == 100.5

    def test_invalid(self):
        assert parse_donation_amount("") is None
        assert parse_donation_amount("abc") is None


class TestComputeExtensionSlots:
    def test_ceil_division(self):
        assert compute_extension_slots(7000, 7000) == 1
        assert compute_extension_slots(7001, 7000) == 2
        assert compute_extension_slots(14000, 7000) == 2
        assert compute_extension_slots(14001, 7000) == 3

    def test_usd(self):
        assert compute_extension_slots(100, 50) == 2
        assert compute_extension_slots(250, 100) == 3


class TestMinAmount:
    def test_limits(self):
        assert min_amount_for_currency("RUB") == 7000
        assert min_amount_for_currency("USD") == 100


class TestPresetAmounts:
    def test_rub_presets(self):
        from bot.services.angel_pool_service import (
            PRESET_AMOUNTS_RUB,
            preset_amounts_for_currency,
        )

        assert preset_amounts_for_currency("RUB") == PRESET_AMOUNTS_RUB
        assert PRESET_AMOUNTS_RUB == (7000, 14000, 35000)

    def test_usd_presets(self):
        from bot.services.angel_pool_service import (
            PRESET_AMOUNTS_USD,
            preset_amounts_for_currency,
        )

        assert preset_amounts_for_currency("USD") == PRESET_AMOUNTS_USD
        assert PRESET_AMOUNTS_USD == (100, 200, 500)


class TestPickWinners:
    def test_no_duplicates_in_one_draw(self):
        candidates = [AngelPoolCandidate(i) for i in range(1, 11)]
        rng = random.Random(42)
        winners = pick_angel_pool_winners(
            candidates, 5, {}, rng=rng
        )
        assert len(winners) == 5
        assert len(set(winners)) == 5

    def test_prior_wins_reduce_repeat_chance(self):
        """При фиксированном seed пользователь с прошлыми победами реже попадает в топ."""
        candidates = [
            AngelPoolCandidate(1),
            AngelPoolCandidate(2),
        ]
        rng = random.Random(0)
        wins_many = {1: 10, 2: 0}
        results = []
        for _ in range(200):
            w = pick_angel_pool_winners(
                candidates, 1, wins_many, rng=random.Random()
            )
            if w:
                results.append(w[0])
        count_user2 = results.count(2)
        assert count_user2 > 100

    def test_fewer_candidates_than_slots(self):
        candidates = [AngelPoolCandidate(1), AngelPoolCandidate(2)]
        winners = pick_angel_pool_winners(candidates, 10, {})
        assert winners == [1, 2] or winners == [2, 1]
        assert len(winners) == 2

    def test_empty(self):
        assert pick_angel_pool_winners([], 3, {}) == []
