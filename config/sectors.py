"""Sector definitions, ETF mappings, GICS classification."""

# 11 GICS sectors mapped to their SPDR Select Sector ETFs
SECTOR_ETFS: dict[str, str] = {
    "Technology": "XLK",
    "Financials": "XLF",
    "Energy": "XLE",
    "Health Care": "XLV",
    "Industrials": "XLI",
    "Communication Services": "XLC",
    "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP",
    "Utilities": "XLU",
    "Real Estate": "XLRE",
    "Materials": "XLB",
}

# Reverse lookup: ETF symbol → sector name
ETF_TO_SECTOR: dict[str, str] = {v: k for k, v in SECTOR_ETFS.items()}

# All sector ETF symbols for easy iteration
SECTOR_ETF_SYMBOLS: list[str] = list(SECTOR_ETFS.values())

# Broad market benchmarks
BENCHMARK_SPY = "SPY"
BENCHMARK_VIX = "VIX"

# Market regime definitions (from design doc)
REGIME_STRONG_BULL = "strong_bull"
REGIME_BULL = "bull"
REGIME_CHOPPY = "choppy"
REGIME_BEAR = "bear"
REGIME_CRISIS = "crisis"

# Regime factors — multiplied into trailing stop k-values and Expected Move
REGIME_FACTORS: dict[str, float] = {
    REGIME_STRONG_BULL: 1.5,
    REGIME_BULL: 1.0,
    REGIME_CHOPPY: 0.6,
    REGIME_BEAR: 0.8,  # for shorts; longs use 0.5
    REGIME_CRISIS: 0.3,
}

# Bear regime has split factors
REGIME_BEAR_LONG_FACTOR: float = 0.5
REGIME_BEAR_SHORT_FACTOR: float = 0.8
