"""Tests for backtest/param_space.py."""

import pytest

from backtest.engine import BacktestConfig
from backtest.param_space import ParamRange, ParamSpace
from config.settings import RiskConfig


class TestParamRange:
    def test_basic_creation(self):
        r = ParamRange("stop_k1", 1.5, 3.0, 0.25)
        assert r.name == "stop_k1"
        assert r.param_type == "float"

    def test_int_type(self):
        r = ParamRange("time_decay_days", 2, 5, 1, param_type="int")
        assert r.param_type == "int"


class TestParamSpace:
    def test_max_params_enforced(self):
        ranges = [ParamRange(f"p{i}", 0, 1) for i in range(11)]
        with pytest.raises(ValueError, match="Too many"):
            ParamSpace(ranges)

    def test_default_has_8_params(self):
        ps = ParamSpace.default()
        assert len(ps.ranges) == 8

    def test_sample_random_count(self):
        ps = ParamSpace.default()
        samples = ps.sample_random(10, seed=1)
        assert len(samples) == 10

    def test_sample_random_within_bounds(self):
        ps = ParamSpace.default()
        samples = ps.sample_random(20, seed=42)
        for combo in samples:
            for r in ps.ranges:
                assert r.low <= combo[r.name] <= r.high, f"{r.name}={combo[r.name]} out of [{r.low}, {r.high}]"

    def test_sample_random_deterministic(self):
        ps = ParamSpace.default()
        a = ps.sample_random(5, seed=99)
        b = ps.sample_random(5, seed=99)
        assert a == b

    def test_sample_random_int_types(self):
        ps = ParamSpace.default()
        samples = ps.sample_random(10, seed=1)
        for combo in samples:
            assert isinstance(combo["time_decay_days"], int)
            assert isinstance(combo["universe_size"], int)

    def test_sample_grid(self):
        ps = ParamSpace([
            ParamRange("a", 1.0, 2.0, 0.5),
            ParamRange("b", 10, 20, 10, param_type="int"),
        ])
        grid = ps.sample_grid()
        # a: [1.0, 1.5, 2.0] = 3, b: [10, 20] = 2 → 6 combos
        assert len(grid) == 6

    def test_apply_modifies_risk_config(self):
        ps = ParamSpace.default()
        base = BacktestConfig(risk_config=RiskConfig())
        params = {"stop_k1": 2.5, "risk_per_trade_pct": 1.5}
        new_cfg = ps.apply(params, base)
        assert new_cfg.risk_config.stop_k1 == 2.5
        assert new_cfg.risk_config.risk_per_trade_pct == 1.5
        # Base unchanged
        assert base.risk_config.stop_k1 == 2.0

    def test_apply_universe_size(self):
        ps = ParamSpace.default()
        base = BacktestConfig(universe_size=50)
        new_cfg = ps.apply({"universe_size": 70}, base)
        assert new_cfg.universe_size == 70

    def test_apply_min_composite_score(self):
        import signals.scoring as scoring_mod
        original = scoring_mod.MIN_COMPOSITE_SCORE
        try:
            ps = ParamSpace.default()
            base = BacktestConfig()
            ps.apply({"min_composite_score": 0.42}, base)
            assert scoring_mod.MIN_COMPOSITE_SCORE == 0.42
        finally:
            scoring_mod.MIN_COMPOSITE_SCORE = original
