# Plan: Task 1.1 — Sector Tracker ✅ COMPLETED

## Overview
Implement `scanner/sectors.py` — the sector momentum tracker that monitors 11 SPDR sector ETFs, calculates momentum scores, ranks sectors, detects rotation, persists snapshots to SQLite, and exposes async query functions for other modules.

## Files to modify
- `scanner/sectors.py` — full implementation
- `tests/test_sectors.py` — comprehensive tests with mocked IBKR data

## Design

### Class: `SectorTracker`

Stateful tracker that holds historical close prices in memory and computes momentum/rotation metrics on demand.

**Constructor:** `SectorTracker(db_path=None)` — optional DB path override for testing.

**Key data structures:**
- `_history: dict[str, list[float]]` — per-ETF daily close prices (most recent last), keyed by ETF symbol (XLK, etc.)
- `_timestamps: list[int]` — aligned timestamps for history entries
- `_latest_prices: dict[str, float]` — most recent price per ETF
- `_latest_volumes: dict[str, int]` — most recent volume per ETF

### IBKR Integration

**`async fetch_sector_bars(ib: IB, days: int = 65) -> None`**
- For each of the 11 sector ETFs + SPY, request daily bars via `ib.reqHistoricalDataAsync()`
- Store close prices in `_history`, update `_latest_prices`
- Uses `Stock(symbol, 'ARCA')` contracts (sector ETFs trade on ARCA)
- Rate-limited: small sleep between requests to respect IBKR's 50 req/sec limit

### Momentum Scoring

**`compute_momentum(symbol: str) -> float | None`**
- Rate of change (ROC) over 3 timeframes: 5d, 20d, 60d
- ROC_n = (close_today / close_n_days_ago - 1) × 100
- Composite: `momentum = 0.5 * ROC_5 + 0.3 * ROC_20 + 0.2 * ROC_60`
- Weights emphasize recent momentum (recency bias for trading)
- Returns None if insufficient history

### Relative Strength vs SPY

**`compute_relative_strength(symbol: str) -> float | None`**
- RS = sector_return_20d / spy_return_20d
- Returns None if insufficient history or SPY data missing

### Sector Rankings

**`get_sector_rankings() -> list[dict]`**
- Returns all 11 sectors sorted by momentum_score descending
- Each dict: `{sector: str, etf: str, price: float, change_pct: float, momentum_score: float, relative_strength: float, rank: int}`

**`get_sector_momentum(sector_name: str) -> dict | None`**
- Returns momentum data for a single sector by name (e.g., "Technology")

### Rotation Detection

**`detect_rotation() -> list[dict]`**
- Compare current 5d momentum rankings vs 20d momentum rankings
- Sectors that moved up 3+ ranks = "money flowing in"
- Sectors that moved down 3+ ranks = "money flowing out"
- Returns list of `{sector, etf, direction: "inflow"|"outflow", rank_change: int}`

### Acceleration

**`is_sector_accelerating(sector_name: str) -> bool`**
- True if ROC_5 > ROC_20 (short-term momentum exceeds medium-term)
- Used by exit framework: "If sector momentum is decelerating → prepare to exit"

### Persistence

**`async save_snapshots(db: aiosqlite.Connection) -> None`**
- Inserts a `SectorSnapshot` row for each sector with current timestamp
- Uses `INSERT OR REPLACE` on the UNIQUE(sector, timestamp) constraint
- Populates: price, change_pct, relative_strength, momentum_score

### Module-level convenience functions

These are thin wrappers that other modules import:
- `async get_sector_rankings(tracker: SectorTracker) -> list[dict]`
- `async get_sector_momentum(tracker: SectorTracker, sector: str) -> dict | None`
- `is_sector_accelerating(tracker: SectorTracker, sector: str) -> bool`

(These simply delegate to the tracker instance methods — the async wrappers exist so future DB lookups can be added without API changes.)

## Tests (`tests/test_sectors.py`)

All tests use mocked price data — no IBKR connection needed.

1. **test_compute_momentum_basic** — feed known prices, verify ROC calculation
2. **test_compute_momentum_insufficient_history** — returns None with < 5 bars
3. **test_compute_relative_strength** — verify RS = sector_return / spy_return
4. **test_get_sector_rankings_sorted** — verify ranking order by momentum
5. **test_get_sector_rankings_all_sectors** — all 11 present
6. **test_detect_rotation_inflow** — sector moving up 3+ ranks detected
7. **test_detect_rotation_no_change** — stable rankings return empty
8. **test_is_sector_accelerating_true** — ROC_5 > ROC_20
9. **test_is_sector_accelerating_false** — ROC_5 < ROC_20
10. **test_save_snapshots** — verify rows written to SQLite
11. **test_get_sector_momentum_single** — returns correct sector data
12. **test_get_sector_momentum_unknown** — returns None for invalid sector
13. **test_change_pct_calculation** — verify day-over-day change percentage

## Verification
```bash
.venv/bin/python -m pytest tests/test_sectors.py -v
.venv/bin/python -m pytest -v  # full suite
```
