"""Tests for execution/reconciliation.py — daily reconciliation (task 3.8)."""

from execution.reconciliation import Reconciler


def _make_reconciler(protected: dict[str, int] | None = None) -> Reconciler:
    return Reconciler(protected or {})


class TestReconciler:
    def test_perfect_match(self):
        r = _make_reconciler({"AAPL": 100})
        snap = r.run({"GOOG": 50}, {"AAPL": 100, "GOOG": 50})
        assert snap.matches is True
        assert snap.mismatches == []
        assert not r.is_halted

    def test_mismatch_share_count(self):
        r = _make_reconciler({"AAPL": 100})
        snap = r.run({"GOOG": 50}, {"AAPL": 100, "GOOG": 40})
        assert snap.matches is False
        assert any("GOOG" in m for m in snap.mismatches)
        assert r.is_halted

    def test_extra_symbol_in_ibkr(self):
        r = _make_reconciler({"AAPL": 100})
        snap = r.run({}, {"AAPL": 100, "MSFT": 20})
        assert snap.matches is False
        assert any("MSFT" in m for m in snap.mismatches)

    def test_missing_symbol_in_ibkr(self):
        r = _make_reconciler({"AAPL": 100})
        snap = r.run({"GOOG": 50}, {"AAPL": 100})
        assert snap.matches is False
        assert any("GOOG" in m for m in snap.mismatches)

    def test_protected_only_matches(self):
        r = _make_reconciler({"AAPL": 100})
        snap = r.run({}, {"AAPL": 100})
        assert snap.matches is True
        assert not r.is_halted

    def test_bot_only_matches(self):
        r = _make_reconciler({})
        snap = r.run({"GOOG": 50}, {"GOOG": 50})
        assert snap.matches is True

    def test_halt_persists_after_mismatch(self):
        r = _make_reconciler({"AAPL": 100})
        r.run({}, {"AAPL": 50})  # mismatch
        assert r.is_halted
        r.run({}, {"AAPL": 100})  # now matches
        assert r.is_halted  # still halted

    def test_reset_halt(self):
        r = _make_reconciler({"AAPL": 100})
        r.run({}, {"AAPL": 50})
        assert r.is_halted
        r.reset_halt()
        assert not r.is_halted

    def test_history_tracks_runs(self):
        r = _make_reconciler({})
        r.run({}, {})
        r.run({"A": 1}, {"A": 1})
        r.run({"A": 1}, {"A": 2})
        assert len(r.history) == 3
        assert r.history[0].matches is True
        assert r.history[2].matches is False

    def test_empty_positions(self):
        r = _make_reconciler({})
        snap = r.run({}, {})
        assert snap.matches is True
        assert snap.bot_total_symbols == 0
        assert snap.ibkr_total_symbols == 0
        assert snap.protected_total_symbols == 0

    def test_multiple_mismatches(self):
        r = _make_reconciler({"AAPL": 100, "MSFT": 50})
        snap = r.run({}, {"AAPL": 90, "MSFT": 40})
        assert snap.matches is False
        assert len(snap.mismatches) == 2
