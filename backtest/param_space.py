"""Parameter space definition and sampling for walk-forward optimization."""

from __future__ import annotations

import math
import random
from copy import deepcopy
from dataclasses import dataclass

from backtest.engine import BacktestConfig
from config.settings import RiskConfig


@dataclass
class ParamRange:
    name: str
    low: float
    high: float
    step: float | None = None  # None = continuous
    param_type: str = "float"  # "float" or "int"


class ParamSpace:
    """Defines optimizable parameter ranges and produces concrete parameter dicts."""

    MAX_FREE_PARAMS = 10

    def __init__(self, ranges: list[ParamRange]) -> None:
        if len(ranges) > self.MAX_FREE_PARAMS:
            raise ValueError(
                f"Too many free parameters: {len(ranges)} > {self.MAX_FREE_PARAMS}"
            )
        self._ranges = list(ranges)

    @property
    def ranges(self) -> list[ParamRange]:
        return list(self._ranges)

    def sample_random(self, n: int, seed: int = 42) -> list[dict]:
        """Sample n random parameter combinations."""
        rng = random.Random(seed)
        samples: list[dict] = []
        for _ in range(n):
            combo: dict = {}
            for r in self._ranges:
                if r.step is not None:
                    steps = int(round((r.high - r.low) / r.step))
                    val = r.low + rng.randint(0, steps) * r.step
                else:
                    val = rng.uniform(r.low, r.high)
                if r.param_type == "int":
                    val = int(round(val))
                else:
                    val = round(val, 4)
                combo[r.name] = val
            samples.append(combo)
        return samples

    def sample_grid(self) -> list[dict]:
        """Generate a full grid of parameter combinations."""
        axes: list[list[tuple[str, float]]] = []
        for r in self._ranges:
            if r.step is not None:
                steps = int(round((r.high - r.low) / r.step))
                vals = [r.low + i * r.step for i in range(steps + 1)]
            else:
                # Continuous: use 5 evenly-spaced points
                vals = [r.low + i * (r.high - r.low) / 4 for i in range(5)]
            if r.param_type == "int":
                vals = [int(round(v)) for v in vals]
            else:
                vals = [round(v, 4) for v in vals]
            axes.append([(r.name, v) for v in vals])

        # Cartesian product
        grid: list[dict] = [{}]
        for axis in axes:
            grid = [{**combo, name: val} for combo in grid for name, val in axis]
        return grid

    def apply(self, params: dict, base_config: BacktestConfig) -> BacktestConfig:
        """Create a new BacktestConfig with the given parameter overrides."""
        import signals.scoring as scoring_mod

        # Separate risk config params from other params
        risk_fields = {f.name for f in RiskConfig.__dataclass_fields__.values()}
        risk_overrides: dict = {}
        config_overrides: dict = {}

        for k, v in params.items():
            if k == "min_composite_score":
                scoring_mod.MIN_COMPOSITE_SCORE = v
            elif k == "universe_size":
                config_overrides[k] = int(v)
            elif k in risk_fields:
                risk_overrides[k] = v

        # Build new RiskConfig from base + overrides
        base_rc = base_config.risk_config
        rc_dict = {
            f.name: getattr(base_rc, f.name)
            for f in RiskConfig.__dataclass_fields__.values()
        }
        rc_dict.update(risk_overrides)
        new_rc = RiskConfig(**rc_dict)

        # Build new BacktestConfig
        cfg_dict = {
            f: getattr(base_config, f)
            for f in BacktestConfig.__dataclass_fields__
        }
        cfg_dict["risk_config"] = new_rc
        cfg_dict.update(config_overrides)
        return BacktestConfig(**cfg_dict)

    @staticmethod
    def default() -> ParamSpace:
        """Default 8-parameter space per design doc."""
        return ParamSpace([
            ParamRange("stop_k1", 1.5, 3.0, 0.25),
            ParamRange("stop_k2", 1.0, 2.0, 0.25),
            ParamRange("partial_exit_1_atr", 0.5, 2.0, 0.25),
            ParamRange("partial_exit_2_atr", 1.5, 3.0, 0.5),
            ParamRange("risk_per_trade_pct", 0.5, 2.0, 0.25),
            ParamRange("time_decay_days", 2, 5, 1, param_type="int"),
            ParamRange("min_composite_score", 0.25, 0.50, 0.05),
            ParamRange("universe_size", 30, 80, 10, param_type="int"),
        ])
