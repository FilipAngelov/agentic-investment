# Plan: Task 1.2 — Market Regime Detection

## Files to create/modify
- `scanner/regime.py` — full `RegimeDetector` implementation
- `tests/test_regime.py` — comprehensive tests with mocked data

## Class: `RegimeDetector`

```
RegimeDetector()
```

### Internal state
- `_spy_closes: list[float]` — SPY daily close prices
- `_vix_closes: list[float]` — VIX daily close values
- `_timestamps: list[int]` — aligned timestamps
- `_current_regime: RegimeType | None` — cached current regime
- `_previous_regime: RegimeType | None` — for transition detection

### Methods

1. **`async fetch_market_data(ib, days=65)`** — request daily bars for SPY and VIX via `ib_async`. Store closes. Small `asyncio.sleep(0.05)` between requests.

2. **`ingest_spy(closes: list[float], timestamps: list[int])`** — direct injection for testing.

3. **`ingest_vix(closes: list[float], timestamps: list[int])`** — direct injection for testing.

4. **`compute_sma(prices: list[float], period: int) -> float | None`** — simple moving average of last `period` values. Returns None if insufficient data.

5. **`classify_regime() -> RegimeType | None`** — Core classification logic per spec:
   - **Crisis**: VIX > 35 (checked first — overrides all)
   - **Strong Bull**: SPY > 20 SMA > 50 SMA AND VIX < 15
   - **Bull**: SPY > 50 SMA AND VIX 15-20
   - **Choppy**: SPY between 20 SMA and 50 SMA AND VIX 20-25
   - **Bear**: SPY < 50 SMA AND VIX 25-35
   - Falls through to `choppy` as default if no exact match (VIX in gaps between defined ranges, etc.)
   - Returns None if insufficient data (< 50 bars for SPY or no VIX data)

6. **`get_regime_factor(direction: str = "LONG") -> float`** — returns regime factor for current regime. Bear regime returns 0.5 for LONG, 0.8 for SHORT. Uses constants from `config.sectors`.

7. **`detect_regime_change() -> dict | None`** — compares current vs previous regime. Returns `{from_regime, to_regime, timestamp}` if changed, else None. Updates `_previous_regime`.

8. **`async log_regime(db: aiosqlite.Connection) -> None`** — INSERT into `regime_log` table with current timestamp, regime, spy_vs_20sma, spy_vs_50sma, vix, regime_factor.

9. **`get_current_regime() -> RegimeType | None`** — returns `_current_regime`.

10. **`get_spy_vs_sma() -> dict`** — returns `{spy_price, sma_20, sma_50, spy_vs_20sma, spy_vs_50sma}` ratios.

11. **`get_vix() -> float | None`** — returns latest VIX value.

### Module-level wrappers
- `async get_current_regime(detector)` → delegates
- `async get_regime_factor(detector, direction="LONG")` → delegates
- `async detect_regime_change(detector)` → delegates

## Classification Logic Detail

Priority order (first match wins):
1. VIX > 35 → crisis
2. SPY > SMA20 > SMA50 AND VIX < 15 → strong_bull
3. SPY > SMA50 AND VIX < 20 → bull  (captures VIX 15-20 plus any < 15 that didn't match strong_bull due to SMA order)
4. SPY < SMA50 AND VIX >= 25 → bear  (captures VIX 25-35)
5. Otherwise → choppy (default fallback — covers SPY between SMAs, VIX 20-25, and edge cases)

This avoids gaps between VIX ranges and handles all edge cases cleanly.

## Tests (14 tests)

Uses `ingest_spy()` and `ingest_vix()` to inject known data. No IBKR mock needed.

1. `test_classify_strong_bull` — SPY above both SMAs, VIX=12 → strong_bull
2. `test_classify_bull` — SPY above 50 SMA, VIX=17 → bull
3. `test_classify_choppy` — SPY between SMAs, VIX=22 → choppy
4. `test_classify_bear` — SPY below 50 SMA, VIX=30 → bear
5. `test_classify_crisis` — VIX=40 → crisis (regardless of SPY)
6. `test_classify_insufficient_data` — < 50 bars → None
7. `test_regime_factor_strong_bull` — returns 1.5
8. `test_regime_factor_bear_long` — returns 0.5
9. `test_regime_factor_bear_short` — returns 0.8
10. `test_regime_factor_crisis` — returns 0.3
11. `test_detect_regime_change` — transition from bull to bear detected
12. `test_detect_no_change` — same regime → None
13. `test_log_regime` — async, verify row in SQLite
14. `test_spy_vs_sma_ratios` — verify computed SMA ratios

## Key dependencies
- `config.sectors`: `REGIME_*` constants, `REGIME_FACTORS`, `REGIME_BEAR_LONG_FACTOR`, `REGIME_BEAR_SHORT_FACTOR`, `BENCHMARK_SPY`, `BENCHMARK_VIX`
- `data.models`: `RegimeLog`, `RegimeType`
- `data.store`: `SCHEMA_SQL` (for test fixture)
- `aiosqlite` for persistence
- `ib_async` for IBKR (only in `fetch_market_data`)

## Verification
```bash
.venv/bin/python -m pytest tests/test_regime.py -v
.venv/bin/python -m pytest -v  # full suite
```
