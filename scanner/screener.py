"""IBKR market scanner: gainers, volume, breakouts."""

from __future__ import annotations

import asyncio
import time

from config.sectors import ETF_TO_SECTOR, SECTOR_ETFS
from scanner.sectors import SectorTracker

# ---------------------------------------------------------------------------
# Scan type constants
# ---------------------------------------------------------------------------

SCAN_TOP_GAINERS = "TOP_PERC_GAIN"
SCAN_TOP_LOSERS = "TOP_PERC_LOSE"
SCAN_MOST_ACTIVE = "MOST_ACTIVE"
SCAN_HOT_BY_VOLUME = "HOT_BY_VOLUME"
SCAN_HIGH_52W = "HIGH_52W_PRICE"
SCAN_LOW_52W = "LOW_52W_PRICE"

ALL_SCANS = [
    SCAN_TOP_GAINERS,
    SCAN_TOP_LOSERS,
    SCAN_MOST_ACTIVE,
    SCAN_HOT_BY_VOLUME,
    SCAN_HIGH_52W,
    SCAN_LOW_52W,
]

# Mapping from scan mode name to IBKR locationCode
_LOCATION_CODES = {
    "regular": "STK.US.MAJOR",
    "pre_market": "STK.US.MAJOR",
    "after_hours": "STK.US.MAJOR",
}


class MarketScanner:
    """Run IBKR market scans and aggregate candidates."""

    def __init__(self, sector_tracker: SectorTracker | None = None) -> None:
        self._results: dict[str, list[dict]] = {}
        self._sector_tracker = sector_tracker
        self._last_scan_time: float = 0.0

    # ------------------------------------------------------------------
    # Core scan methods
    # ------------------------------------------------------------------

    async def run_scan(
        self,
        ib,
        scan_code: str,
        mode: str = "regular",
        max_results: int = 25,
    ) -> list[dict]:
        """Run a single IBKR scanner subscription and return parsed results."""
        from ib_async import ScannerSubscription

        sub = ScannerSubscription(
            instrument="STK",
            locationCode=_LOCATION_CODES.get(mode, "STK.US.MAJOR"),
            scanCode=scan_code,
            numberOfRows=max_results,
            abovePrice=1.0,
            marketCapAbove=1e8,
        )

        # pre_market / after_hours use extended-hours setting pairs
        if mode == "pre_market":
            sub.scannerSettingPairs = "preMarket=true"
        elif mode == "after_hours":
            sub.scannerSettingPairs = "afterHours=true"

        try:
            raw = await ib.reqScannerDataAsync(sub)
        except Exception:
            return []

        results = self._parse_scanner_results(raw or [], scan_code)
        self._results[scan_code] = results
        self._last_scan_time = time.time()
        return results

    async def run_all_scans(
        self,
        ib,
        mode: str = "regular",
        max_results: int = 25,
    ) -> dict[str, list[dict]]:
        """Run all 6 scan types with rate limiting between each."""
        for scan_code in ALL_SCANS:
            await self.run_scan(ib, scan_code, mode, max_results)
            await asyncio.sleep(0.05)
        return dict(self._results)

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get_top_gainers(self) -> list[dict]:
        return self._results.get(SCAN_TOP_GAINERS, [])

    def get_top_losers(self) -> list[dict]:
        return self._results.get(SCAN_TOP_LOSERS, [])

    def get_unusual_volume(self) -> list[dict]:
        return self._results.get(SCAN_HOT_BY_VOLUME, [])

    def get_most_active(self) -> list[dict]:
        return self._results.get(SCAN_MOST_ACTIVE, [])

    def get_new_highs(self) -> list[dict]:
        return self._results.get(SCAN_HIGH_52W, [])

    def get_new_lows(self) -> list[dict]:
        return self._results.get(SCAN_LOW_52W, [])

    def get_scan_results(self, scan_type: str | None = None) -> dict[str, list[dict]] | list[dict]:
        """Return results for one scan type, or all if None."""
        if scan_type is not None:
            return self._results.get(scan_type, [])
        return dict(self._results)

    # ------------------------------------------------------------------
    # Candidate aggregation
    # ------------------------------------------------------------------

    def get_combined_candidates(self, min_scans: int = 2) -> list[dict]:
        """Merge results across scans; return symbols appearing in >= min_scans."""
        symbol_scans: dict[str, set[str]] = {}
        symbol_data: dict[str, dict] = {}

        for scan_code, results in self._results.items():
            for item in results:
                sym = item["symbol"]
                symbol_scans.setdefault(sym, set()).add(scan_code)
                if sym not in symbol_data:
                    symbol_data[sym] = dict(item)

        candidates: list[dict] = []
        for sym, scans in symbol_scans.items():
            if len(scans) >= min_scans:
                entry = dict(symbol_data[sym])
                entry["scan_count"] = len(scans)
                entry["scans"] = sorted(scans)
                entry["sector"] = self.tag_sector(sym)
                candidates.append(entry)

        candidates.sort(key=lambda c: c["scan_count"], reverse=True)
        return candidates

    # ------------------------------------------------------------------
    # Sector tagging
    # ------------------------------------------------------------------

    def tag_sector(self, symbol: str) -> str | None:
        """Cross-reference symbol with sector tracker or ETF map."""
        # Direct ETF match
        if symbol in ETF_TO_SECTOR:
            return ETF_TO_SECTOR[symbol]
        # Check sector tracker for sector info
        if self._sector_tracker is not None:
            for sector, etf in SECTOR_ETFS.items():
                if symbol == etf:
                    return sector
        return None

    # ------------------------------------------------------------------
    # Internal parsing
    # ------------------------------------------------------------------

    def _parse_scanner_results(
        self, raw: list, scan_code: str
    ) -> list[dict]:
        """Normalize ScanData objects into plain dicts."""
        results: list[dict] = []
        for item in raw:
            contract = item.contractDetails.contract
            results.append(
                {
                    "symbol": contract.symbol,
                    "scan_code": scan_code,
                    "rank": item.rank,
                    "distance": item.distance,
                    "benchmark": item.benchmark,
                    "projection": item.projection,
                    "con_id": contract.conId,
                    "exchange": contract.exchange,
                    "sec_type": contract.secType,
                    "industry": item.contractDetails.industry,
                    "category": item.contractDetails.category,
                }
            )
        return results


# ---------------------------------------------------------------------------
# Module-level wrappers
# ---------------------------------------------------------------------------


async def run_all_scans(
    scanner: MarketScanner, ib, mode: str = "regular"
) -> dict[str, list[dict]]:
    return await scanner.run_all_scans(ib, mode)


def get_top_gainers(scanner: MarketScanner) -> list[dict]:
    return scanner.get_top_gainers()


def get_top_losers(scanner: MarketScanner) -> list[dict]:
    return scanner.get_top_losers()


def get_unusual_volume(scanner: MarketScanner) -> list[dict]:
    return scanner.get_unusual_volume()


def get_most_active(scanner: MarketScanner) -> list[dict]:
    return scanner.get_most_active()


def get_new_highs(scanner: MarketScanner) -> list[dict]:
    return scanner.get_new_highs()


def get_new_lows(scanner: MarketScanner) -> list[dict]:
    return scanner.get_new_lows()
