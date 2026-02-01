# Agentic Investment

**Goal:** Automated daily stock trading using the Interactive Brokers API + Python.

## Status
- **Phase:** Planning / API setup
- **Started:** 2026-02-01

## API Choice: `ib_async` + IB Gateway ✅

**Three options exist:**

| Option | Verdict |
|--------|---------|
| **TWS API (ibapi)** — Official IBKR Python library | Low-level, callback-heavy, requires managing threads manually. Steep learning curve. |
| **Client Portal / Web API** — REST-based | Restricted to commercial accounts in practice. Session management is painful (only 1 brokerage session at a time). Not suitable. |
| **`ib_async`** (successor to `ib_insync`) | Clean Pythonic wrapper over TWS protocol. No need for ibapi package — implements the binary protocol internally. Async-ready, Jupyter-friendly, production-ready reconnection logic. Community-maintained, actively developed. |

**Winner: `ib_async`** — it's the de facto standard for Python IBKR trading. Reasons:
- No callback hell — straightforward synchronous or async code
- Automatic state sync with TWS/Gateway
- Handles reconnection, error recovery
- Works with IB Gateway (headless, lighter than TWS — perfect for server deployment)
- `pip install ib_async`, Python 3.10+
- GitHub: https://github.com/ib-api-reloaded/ib_async

**Runtime: IB Gateway** (not TWS) — headless, less memory, designed for API use. TWS is for visual trading; Gateway is the API-first option.

## Architecture

```
Mac Mini (or VPS later)
├── IB Gateway (paper account for dev, live account for prod)
├── Python bot (ib_async)
│   ├── Strategy engine
│   ├── Risk management
│   ├── Position sizing
│   └── Logging / notifications (→ WhatsApp via Cdius)
└── Data store (PostgreSQL)
```

## Environments
- **Dev/Paper:** IBKR paper trading account → IB Gateway paper mode
- **Prod:** Filip's existing IBKR account → IB Gateway live mode

## Strategy: Directional (Momentum / Breakout)
- **Core approach:** Scan the entire market for what's moving — long or short. No fixed universe. If it's hot or cold, it's fair game.
- **Overnight holds:** Allowed
- **Entry signals:** TBD (volume surge, price breaking resistance, relative strength, news catalysts)
- **Exit signals:** TBD (trailing stop, momentum exhaustion, target hit)
- **Short selling:** Yes, enabled

## Capital & Targets
- **Strategy allocation:** $10,000 cash within a larger account (>$25K NLV)
- **Account context:** Filip's existing portfolio (see holdings below) provides >$25K NLV → **no PDT restriction, unlimited day trades, 4× DTBP**
- **Daily target:** 0.2-0.5% of strategy allocation ($20-50/day)
- **Max daily drawdown:** 2% of strategy allocation ($200)
- **Position sizing:** TBD
- **Annualized goal:** 50-125% (before we scale up)

### ⚠️ Pre-Existing Portfolio Protection
Filip's account contains long-term holdings that **must never be touched** by the trading bot. The bot operates alongside these positions.

**Rule: Load `config/starting_portfolio.csv` at startup. These positions are SACRED.**

```csv
# starting_portfolio.csv — snapshot at bot launch
# symbol,shares,note
ABCL,XXX,long-term hold
ACHR,XXX,long-term hold
AMD,XXX,long-term hold
...
```

**Implementation:**
- On startup, load `starting_portfolio.csv` and store as `protected_positions: dict[str, int]`
- Before ANY sell order: `sellable_shares = current_position - protected_shares`
- If `sellable_shares <= 0`: **block the sell entirely**
- If we buy 50 shares of ASML (which Filip already holds 20 of), we can only sell 50 — the original 20 are untouchable
- The DTBP Guard and Execution Manager both enforce this check
- **Daily reconciliation:** Compare IBKR positions vs protected + bot positions. Any mismatch = halt + alert Filip.

## Filip's Current Holdings
ABCL, ACHR, AMD, AMZN, ARM, ASML, AVGO, CAN, CCJ, CPER, CPRI, CRWD, CRWV, EOSE, ETN, FCX, GOOG, HIMS, IAUM (gold ETF), IONQ, IREN, MU, NVDA, OSCR, QQQ, SMCI, SPY, TSM, WPM, WYFI

## Resources
- `ib_async` docs: https://ib-api-reloaded.github.io/ib_async/
- `ib_async` GitHub: https://github.com/ib-api-reloaded/ib_async
- IB Gateway download: https://www.interactivebrokers.com/en/trading/ibgateway-stable.php
- IBKR API docs: https://ibkrcampus.com/ibkr-api-page/twsapi-doc/
- IBKR paper account setup: https://www.interactivebrokers.com/en/trading/paperTrading.php

## Working Agreement
- **Filip provides:** Direction, high-level ideas, examples, final approval
- **Cdius is responsible for:** Formalizing ideas with mathematical/quant rigor, finding all edge cases, thinking through the full strategy before documenting. Don't just transcribe — analyze, model, and structure everything with investment-grade thinking.
- **Rule:** Every parameter, threshold, or strategy element in this doc must have quantitative reasoning behind it — not vibes.

---

## Exit Strategy & Position Management Framework

The core problem: **when do we sell?** Too early = leaving money on the table. Too late = giving back gains. This must be dynamic, not static.

### Exit Decision Variables

Every open position is evaluated continuously against these factors:

#### 1. Expected Move (EM) — "How far should this go?"

Before entering, estimate the expected magnitude of the move. This sets our profit target range.

**EM = f(catalyst_type, sector_beta, stock_volatility, market_regime)**

Components:

**a) Catalyst Strength Score (CSS) ∈ [1, 5]**
| Catalyst Type | Score | Typical Move (1-day) |
|---|---|---|
| Macro/Fed news | 1-2 | 0.5-1.5% sector-wide |
| Analyst upgrade/downgrade | 2 | 1-3% |
| Earnings beat/miss | 3-4 | 3-8% |
| FDA approval / major contract | 4-5 | 5-20%+ |
| M&A / takeover | 5 | 10-50%+ |

**b) Stock Beta (β) relative to sector**
- Measures how much the stock amplifies sector moves
- β > 1.5: aggressive mover → expect larger moves, wider stops
- β ≈ 1.0: moves with sector → standard parameters
- β < 0.7: defensive → tighter targets, quicker exits

**c) Historical Volatility (HV₂₀) — 20-day realized volatility**
- Normalizes expectations per stock
- Expected daily move ≈ HV₂₀ / √252
- Example: HV₂₀ = 40% → expected daily move ≈ 2.5%

**d) Average True Range (ATR₁₄) — 14-period ATR**
- Absolute dollar measure of "normal" movement
- Used for stop placement: stops should be outside normal noise
- Stop distance = k × ATR₁₄, where k depends on strategy (typically 1.5-3.0)

**Combined Expected Move:**
```
EM = CSS_multiplier × β × (HV₂₀ / √252) × market_regime_factor
```

Where:
- CSS_multiplier maps catalyst score to expected move multiple (1→1x, 3→2x, 5→5x of normal daily range)
- market_regime_factor adjusts for current conditions (see below)

#### 2. Market Regime Detection — "Is the market hot?"

The same stock with the same catalyst behaves differently in a bull vs bear market. We must adapt.

**Regime Classification:**

| Regime | Definition | Behavior |
|---|---|---|
| **Strong Bull** | SPY > 20 SMA > 50 SMA, VIX < 15 | Trends extend. Hold longer. Wider trailing stops. |
| **Bull** | SPY > 50 SMA, VIX 15-20 | Normal trend following. Standard parameters. |
| **Choppy/Range** | SPY between 20 & 50 SMA, VIX 20-25 | Trends fail. Take profits quickly. Tighter stops. |
| **Bear** | SPY < 50 SMA, VIX 25-35 | Short bias. Quick exits on longs. Let shorts ride. |
| **Crisis** | VIX > 35, correlation spike | Reduce position sizes 50%. Only high-conviction trades. |

**Regime Factor (RF):**
- Strong Bull: RF = 1.5 (let winners run longer)
- Bull: RF = 1.0 (baseline)
- Choppy: RF = 0.6 (take profits faster)
- Bear: RF = 0.8 for shorts, 0.5 for longs
- Crisis: RF = 0.3 (aggressive profit-taking)

#### 3. Trailing Stop Framework — "How much pullback before we exit?"

Static stops are lazy. Our stops must adapt to:
- How far the position is in profit
- The stock's volatility
- The market regime

**Adaptive Trailing Stop Algorithm:**

```
initial_stop = entry_price - (k₁ × ATR₁₄)     # for longs
                                                  # k₁ = 2.0 (default)

# As position moves in profit, tighten the stop:
if unrealized_profit > 1 × ATR₁₄:
    trailing_stop = max(trailing_stop, current_price - (k₂ × ATR₁₄))
    # k₂ = 1.5 (tighter than initial)

if unrealized_profit > 2 × ATR₁₄:
    trailing_stop = max(trailing_stop, current_price - (k₃ × ATR₁₄))
    # k₃ = 1.0 (lock in most of the gain)

if unrealized_profit > 3 × ATR₁₄:
    trailing_stop = max(trailing_stop, current_price - (k₄ × ATR₁₄))
    # k₄ = 0.75 (tight — protecting a big winner)
```

**Regime adjustment:** multiply all k values by RF
- Strong bull → wider stops (k × 1.5), let trends breathe
- Choppy → tighter stops (k × 0.6), grab profit before reversal

#### 4. Sector-Relative Strength — "Is this stock leading or lagging?"

Don't just look at the stock in isolation. Compare it to its sector.

**Relative Strength Index (stock vs sector ETF):**
```
RS = (stock_return_N_days / sector_ETF_return_N_days)
```

- RS > 1.2: stock is leading the sector → strong signal, hold longer
- RS ≈ 1.0: moving with sector → standard exit rules
- RS < 0.8: lagging the sector → weakening, tighten stops or exit early

**Sector momentum persistence:**
- If sector is in top 3 by momentum AND accelerating → extend hold period
- If sector momentum is decelerating → prepare to exit even if stock is still up

#### 5. Volume Confirmation — "Is the move real?"

Volume validates price. A breakout on low volume is suspicious.

**Volume Rules:**
```
relative_volume = current_volume / avg_volume_20day

# Entry confirmation:
- relative_volume > 2.0: strong conviction → full position
- relative_volume 1.5-2.0: moderate → 75% position
- relative_volume < 1.5: weak → skip or 50% position

# Exit warning:
- Price rising but volume declining: bearish divergence → tighten stop
- Price falling on high volume: capitulation → exit immediately
- Price at resistance + volume exhaustion: exit or take partial profits
```

#### 6. Partial Exit Strategy — "Don't be binary"

All-or-nothing exits are suboptimal. Scale out:

```
Position entry: 100% at signal

At +1 ATR profit: sell 25% (lock in something)
At +2 ATR profit: sell another 25% (now house money)
Remaining 50%: ride with trailing stop

Exception - strong regime + strong RS:
  - Delay first partial to +1.5 ATR
  - Keep 60% for trailing
```

#### 7. Time-Based Decay — "The trade thesis has an expiration"

If a trade isn't working within a reasonable timeframe, the thesis is likely wrong.

```
# For momentum/breakout trades:
if hours_since_entry > 4 AND profit < 0.5 × ATR₁₄:
    # Thesis isn't playing out — tighten stop to breakeven

if days_since_entry > 3 AND profit < 1 × ATR₁₄:
    # Momentum has stalled — exit at next resistance

if days_since_entry > 5:
    # Re-evaluate: is the catalyst still valid?
    # If not, exit regardless of P&L
```

#### 8. Correlation & Portfolio Heat — "Don't double down unknowingly"

Multiple positions in correlated stocks = hidden concentration risk.

```
portfolio_heat = sum(position_risk for all positions)
# position_risk = position_size × distance_to_stop / equity

max_portfolio_heat = 6%  # of total equity at risk at any time
max_sector_heat = 3%     # max risk in any single sector
max_correlation_cluster = 3  # max positions with correlation > 0.7
```

If adding a new position would breach these limits → skip or reduce size.

---

## Regulatory Constraints: PDT Rule & Day Trading Buying Power (DTBP)

### Pattern Day Trader (PDT) Rule
- **Definition:** 4+ day trades (buy+sell same security same day) within 5 business days on a margin account
- **Requirement:** Account must maintain **≥$25,000 Net Liquidation Value** to day trade freely
- **Our starting capital: $10,000** → we are below PDT threshold

**Implications for $10K account:**
- On **margin account**: limited to 3 day trades per 5 business days. 4th trade gets blocked by IBKR automatically.
- On **cash account**: no PDT rule, but T+1 settlement means funds aren't available until next day after selling. Limits capital recycling.
- **Overnight holds don't count as day trades** — this is why our swing/overnight strategy is advantageous.

**$25K "equity" = Net Liquidation Value (NLV) = cash + stocks + options + futures P&L.** Your invested stocks count. $5K cash + $20K in stocks = $25K NLV = qualified.
⚠️ If your stocks drop and NLV dips below $25K, you lose unlimited day trading until it recovers.

**Our situation: Account NLV is >$25K (existing holdings), so PDT is NOT a constraint. We have unlimited day trades and 4× DTBP. The $10K cash allocation is our strategy budget, but the full account's equity counts for margin/DTBP purposes.**

⚠️ **Risk: If Filip's holdings drop and NLV dips below $25K, we'd lose unlimited day trades. Monitor NLV as part of Risk Controller.**

### Day Trading Buying Power (DTBP)

DTBP is the maximum dollar amount you can use for day trades (positions opened AND closed same day).

```
DTBP = 4 × Maintenance Margin Excess (at previous day's close)
     ≈ 4 × (Net Liquidation Value - Maintenance Margin Requirement)
```

For a $25K+ account with no overnight positions: DTBP ≈ 4× margin excess.
Our account: >$25K NLV (existing holdings) → full 4× DTBP available, but overnight positions (Filip's holdings) reduce margin excess → reduces DTBP.

**DTBP examples:**

```
Example A — $50K, no overnight positions:
  NLV: $50,000 | Margin Excess: $50,000
  DTBP = 4 × $50K = $200,000
  Day trade $80K of XYZ → DTBP remaining: $120K
  Close XYZ same day → DTBP replenished to $200K ✅

Example B — $50K, holding $30K overnight in AAPL:
  NLV: $50,000 | Reg T margin on AAPL: $15K (50%)
  Margin Excess: $50K - $15K = $35,000
  DTBP = 4 × $35K = $140,000
  ↑ Overnight positions eat into next day's DTBP!

Example C — DTBP violation:
  DTBP: $100,000
  Day trade #1: $60K (open) → remaining: $40K
  Day trade #2: $50K (open) → EXCEEDS DTBP → MARGIN CALL 🚨
  Must deposit funds within 5 days or face restrictions

Example D — $10K account (under PDT):
  NLV: $10,000 | Buying Power: 2× = $20,000
  Max 3 day trades per rolling 5 business days
  Best strategy: overnight holds (don't count as day trades)
```

**Critical rules:**
- DTBP is consumed by **simultaneously open** day-trade positions
- Closing a day trade **frees** that DTBP — you can reuse it same day
- Total open day-trade exposure **at any moment** cannot exceed DTBP
- DTBP **recycles**: buy $80K, sell, buy $80K again = still only $80K consumed (not $160K)
- DTBP is **mark-to-market**: if positions move against you, equity drops → margin excess shrinks → DTBP shrinks in real-time, even mid-day (silent DTBP collapse)
- You can be fine to hold something overnight but blocked from opening it intraday (different leverage rules)

**PDT vs DTBP — the mental model:**
| Rule | What it limits | When it applies |
|---|---|---|
| PDT | Number of day trades | Only if equity < $25K |
| DTBP | Size of intraday exposure | Always, even if equity is $1M |

**Key rules:**
- DTBP is calculated at previous day's close (4:15 PM ET)
- Each day trade consumes DTBP equal to the cost basis of the position
- DTBP replenishes when day trades are closed (intraday)
- Overnight positions reduce next day's DTBP (they eat into margin excess)
- If you exceed DTBP → **day trade margin call** → must deposit funds or face 90-day restriction
- Non-marginable stocks (some volatile/low-float) consume 100% cash even against DTBP

**IBKR API fields to monitor:**
| API Field | What It Tells Us |
|---|---|
| `BuyingPower` | Overall buying power (Reg T) |
| `DayTradesRemaining` | Number of day trades left in rolling 5-day window (-1 = unlimited if PDT with $25K+) |
| `DayTradesRemainingT+1` through `T+4` | Day trades available on each future day |
| `AvailableFunds` | Available funds after margin requirements |
| `ExcessLiquidity` | Cushion above maintenance margin |
| `InitMarginReq` / `MaintMarginReq` | Current margin requirements |
| `NetLiquidation` | Total account value |
| `SMA` (Special Memorandum Account) | Reg T buying power indicator |

### DTBP Compliance Module (MUST BUILD)

**`execution/dtbp_guard.py`** — Pre-trade compliance check that runs before every order.

```python
class DTBPGuard:
    """
    Prevents DTBP violations by tracking intraday round trips
    and margin consumption in real-time.
    """

    def pre_trade_check(self, proposed_order) -> bool:
        """
        Before any order, verify:
        1. If this would be a day trade (closing a same-day position),
           check DayTradesRemaining > 0 (if account < $25K)
        2. If opening a new position intended for day trade,
           check remaining DTBP >= order cost basis
        3. If holding overnight, check Reg T margin (50% initial)
           won't exceed available funds
        4. Verify ExcessLiquidity stays positive after trade
        """

    def get_account_state(self) -> dict:
        """
        Query IBKR API via ib_async for:
        - accountSummary: NetLiquidation, BuyingPower, 
          DayTradesRemaining, AvailableFunds, ExcessLiquidity,
          InitMarginReq, MaintMarginReq, SMA
        - Current positions and their open timestamps
        """

    def is_day_trade(self, symbol) -> bool:
        """
        Check if selling this symbol would constitute a day trade
        (was it opened today?)
        """

    def remaining_dtbp(self) -> float:
        """
        Calculate remaining DTBP:
        DTBP_remaining = DTBP_start_of_day - sum(day_trade_cost_bases)
        
        IMPORTANT: DTBP is calculated by IBKR against the ENTIRE account
        (Filip's long-term holdings + our strategy positions). We must
        mirror this — query IBKR's actual BuyingPower/MarginExcess fields,
        don't try to calculate from our $10K slice alone.
        
        Filip's existing holdings consume margin and reduce DTBP.
        Our job is to track what's LEFT after those holdings are accounted for.
        """

    def margin_impact(self, proposed_order) -> dict:
        """
        Estimate margin impact of proposed order against the FULL account:
        - Initial margin required (IBKR calculates on entire portfolio)
        - Effect on ExcessLiquidity (account-wide, not strategy-only)
        - Effect on overnight Reg T requirement (all positions, ours + Filip's)
        
        The $10K strategy allocation is our SELF-IMPOSED budget limit.
        The margin/DTBP math uses the whole account because that's what IBKR does.
        """

    def daily_reset(self):
        """
        At market open, refresh DTBP from IBKR account summary.
        Log previous day's usage for analytics.
        """
```

**Two layers of limits:**

| Layer | What it checks | Source of truth |
|---|---|---|
| **IBKR account limits** (DTBP, margin, ExcessLiquidity) | Full account: Filip's holdings + our trades | IBKR API fields (BuyingPower, ExcessLiquidity, etc.) |
| **Strategy budget limits** (max $10K deployed, per-trade risk) | Our trades only | Internal tracking in bot |

Both must pass before any order is submitted. IBKR doesn't know about our $10K budget — that's our own guardrail. And we can't calculate DTBP/margin from just our slice — Filip's holdings are eating margin too.

**Hard rules the guard enforces:**
1. **NEVER exceed DTBP** — use IBKR's actual account-wide DTBP, not a local estimate
2. **NEVER sell protected shares** — check `starting_portfolio.csv` before every sell order
3. **NEVER exceed $10K strategy allocation** — sum of our open position cost bases ≤ $10K
4. **NEVER use margin** — only trade with available cash. Total position value (Filip's holdings + our trades) must NEVER exceed account cash balance. We use a margin account for the benefits (no settlement wait, unlimited day trades) but treat it like a cash account in terms of leverage. Margin buying power exists but we do not touch it.
5. **Always maintain ExcessLiquidity > 5%** — account-wide, queried from IBKR
5. **Overnight positions: verify Reg T margin (50%)** on entire portfolio won't create end-of-day margin call
6. **Alert Filip** via WhatsApp if NLV approaching $25K or if any margin usage detected
7. **Daily reconciliation:** bot positions + protected positions must equal IBKR total. Mismatch = halt.

**Integration:** Every order in `execution/orders.py` must pass through `DTBPGuard.pre_trade_check()` before submission. No exceptions. This is not optional — it's the law.

---

### Putting It All Together — Exit Decision Matrix

For each open position, every 5 minutes during market hours:

```python
def should_exit(position, market_state):
    # Hard exits (immediate)
    if position.loss > max_loss_per_trade:          return EXIT_STOP
    if daily_drawdown > max_daily_drawdown:          return EXIT_ALL
    if position.trailing_stop_hit:                   return EXIT_STOP

    # Soft exits (next bar)
    if volume_divergence(position):                  return TIGHTEN_STOP
    if sector_momentum_decelerating(position):       return TIGHTEN_STOP
    if relative_strength_weakening(position):        return PARTIAL_EXIT
    if time_decay_exceeded(position):                return EXIT_MARKET
    if profit_target_hit(position):                  return PARTIAL_EXIT

    return HOLD
```

---

## Agent Architecture

The system is not a single monolithic bot — it's a **multi-agent system** where each agent has a clear role, runs independently, and communicates through a shared state/message bus.

### Agent 1: Market Sentinel 🛰️
**Role:** Always-on market awareness. Knows what's happening across all sectors in real-time.

**Tasks:**
- Monitor all 10+ sector ETFs for momentum shifts (pre-market, intraday, after-hours)
- Track sector rotation — money flowing out of one sector into another
- Detect regime changes (bull → choppy → bear) using SPY/VIX/breadth indicators
- Maintain a live "market state" object that all other agents read
- Run on a loop: every 1 minute during market hours, every 15 minutes pre/post market

**Inputs:** Price feeds (IBKR), VIX, sector ETFs, market breadth data
**Outputs:** Market regime classification, sector momentum rankings, sector heat map

---

### Agent 2: News & Catalyst Hunter 📰
**Role:** Find and classify market-moving news before it's priced in.

**Tasks:**
- Scrape/poll news sources per sector (see ontology doc)
- Monitor SEC filings (8-K, 13F, insider transactions)
- Track earnings calendar — flag stocks reporting in next 24-48h
- Monitor FDA calendar, FOMC dates, EIA reports, etc.
- NLP classification: score each news item as bullish/bearish/neutral + magnitude
- Link news to specific stocks and sectors
- Detect "news clusters" — multiple catalysts hitting same sector = amplified move

**Inputs:** News APIs, SEC EDGAR, earnings calendars, RSS feeds, X/Twitter feeds
**Outputs:** Catalyst alerts with stock, sector, direction, estimated magnitude, urgency

---

### Agent 3: Stock Screener 🔍
**Role:** Find the specific stocks that are moving or about to move.

**Tasks:**
- Run IBKR market scanner for top gainers/losers, unusual volume, new highs/lows
- Cross-reference scanner results with Agent 2's catalyst data
- Calculate relative strength (stock vs sector, stock vs SPY)
- Score each candidate: volume conviction × catalyst strength × technical setup
- Filter: min market cap, min volume, marginable, shortable (check borrow)
- Maintain a ranked watchlist that updates continuously

**Inputs:** IBKR scanner API, Agent 1's sector data, Agent 2's catalyst data
**Outputs:** Ranked candidate list with composite scores

---

### Agent 4: Signal Generator 🎯
**Role:** Decide exactly when to enter and in which direction.

**Tasks:**
- For each candidate from Agent 3, run technical analysis:
  - Breakout detection (price crossing resistance/support)
  - Momentum indicators (RSI, MACD, rate of change)
  - Volume confirmation (is the move supported?)
  - Multi-timeframe confirmation (5m, 15m, daily alignment)
- Calculate Expected Move (EM) using catalyst type, beta, volatility, regime
- Set precise entry price, stop loss, profit targets
- Score signal confidence: only pass signals above threshold
- Determine direction: LONG or SHORT

**Inputs:** Agent 3's candidate list, price data, Agent 1's regime data
**Outputs:** Trade signals: {stock, direction, entry, stop, target, confidence, reasoning}

---

### Agent 5: Execution Manager ⚡
**Role:** Execute trades safely and manage open positions.

**Tasks:**
- **Pre-trade compliance:** Run every order through DTBP Guard
  - Check DayTradesRemaining (if <$25K account)
  - Verify DTBP capacity
  - Check portfolio heat / sector concentration
  - Verify margin impact
- **Position sizing:** Calculate shares based on risk per trade (1% of equity / distance to stop)
- **Order placement:** Submit orders via ib_async (limit preferred, market if fast-moving)
- **Stop management:** Place initial stop, then run adaptive trailing stop algorithm
- **Partial exits:** Scale out at +1 ATR, +2 ATR per framework
- **Time decay:** Tighten/exit positions that aren't working within expected timeframe

**Inputs:** Agent 4's signals, IBKR account state, DTBP Guard
**Outputs:** Executed trades, position updates, order confirmations

---

### Agent 6: Risk Controller 🛡️
**Role:** Portfolio-level risk management. Has VETO power over all other agents.

**Tasks:**
- Monitor real-time P&L across all positions
- Enforce daily drawdown limit (2% = $200 on $10K) — kill switch if breached
- Track portfolio heat: total risk, per-sector risk, correlation clusters
- Monitor DTBP in real-time — alert at 80% usage
- Detect silent DTBP collapse (equity dropping mid-day)
- Monitor NLV vs $25K threshold — alert when close
- Can force-close positions or halt all trading
- End-of-day: verify overnight margin (Reg T 50%) won't cause margin call

**Inputs:** IBKR account data, all open positions, Agent 1's regime data
**Outputs:** Risk alerts, trade vetoes, forced closes, daily risk report

---

### Agent 7: Portfolio Analyst 📊
**Role:** Track performance, learn from results, improve the system.

**Tasks:**
- Log every trade with full context: signal, score, catalyst, entry/exit, P&L
- Calculate running metrics: win rate, avg win/loss, Sharpe ratio, max drawdown
- Per-sector performance analysis
- Signal quality analysis: which signal types actually make money?
- Identify patterns: best time of day, best sector, best regime for our strategy
- Weekly/monthly reports
- Recommend parameter adjustments based on data

**Inputs:** Trade log, historical performance data
**Outputs:** Performance reports, parameter tuning recommendations

---

### Agent 8: Notification Agent 📱
**Role:** Keep Filip informed without overwhelming him.

**Tasks:**
- Trade alerts → WhatsApp (entry, exit, P&L)
- Daily summary: trades taken, P&L, win rate, notable events
- Risk alerts: drawdown warnings, DTBP warnings, NLV warnings
- Interesting opportunities spotted (for manual review)
- Weekly performance digest

**Inputs:** All other agents' outputs
**Outputs:** WhatsApp messages via Cdius

---

### Agent Communication & Orchestration

```
┌─────────────────────────────────────────────────┐
│              ORCHESTRATOR (main.py)              │
│         Schedules agents, manages state          │
├─────────────────────────────────────────────────┤
│                                                   │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐       │
│  │ Market   │  │ News &   │  │  Stock   │       │
│  │ Sentinel │→ │ Catalyst │→ │ Screener │       │
│  │   🛰️    │  │ Hunter📰│  │   🔍    │       │
│  └──────────┘  └──────────┘  └────┬─────┘       │
│                                    │              │
│                              ┌─────▼─────┐       │
│                              │  Signal   │       │
│                              │ Generator │       │
│                              │    🎯    │       │
│                              └─────┬─────┘       │
│                                    │              │
│  ┌──────────┐              ┌───────▼──────┐      │
│  │   Risk   │◄─── VETO ──►│  Execution   │      │
│  │Controller│              │  Manager ⚡  │      │
│  │   🛡️    │              └──────────────┘      │
│  └──────────┘                                     │
│                                                   │
│  ┌──────────┐              ┌──────────────┐      │
│  │Portfolio │              │ Notification │      │
│  │ Analyst  │              │   Agent 📱   │      │
│  │   📊    │              └──────────────┘      │
│  └──────────┘                                     │
└─────────────────────────────────────────────────┘
```

### Agent 9: Cdius (Supervisor) 🧙‍♂️
**Role:** High-level oversight, human interface, strategic decisions.

**Tasks:**
- Receive daily/weekly performance reports from Agent 7
- Answer Filip's questions about positions, performance, strategy
- Review and approve strategy parameter changes
- Manual override capability (force close, halt trading, adjust risk limits)
- Alert Filip on unusual situations (big drawdown, market crash, opportunity)
- Periodic strategy reviews: "what's working, what isn't, what should we change?"
- Maintain project documentation and knowledge base

**Not in the trading loop** — too slow for execution decisions. Supervisory role only.

**Inputs:** All agent reports, Filip's questions
**Outputs:** Strategic decisions, parameter adjustments, WhatsApp communication

---

### Why NOT Google Agent Development Kit (or any LLM agent framework)

**Evaluated:** Google ADK, LangChain agents, CrewAI, AutoGen

**Decision: Pure async Python. No agent framework.**

**Reasoning:**

1. **Speed kills (literally).** When a breakout happens, we need milliseconds. An LLM API call takes 1-5 seconds. Putting LLM reasoning in the trading loop = missed entries, late exits, lost money.

2. **Most agents are deterministic.** Market Sentinel = math. Screener = API + filtering. Signals = technical indicators. Execution = order logic. Risk = threshold checks. None of these benefit from "reasoning" — they benefit from being fast and correct.

3. **Cost.** Running 8 agents through LLM APIs every 1-5 minutes during market hours = thousands of API calls/day. At $0.01-0.15 per call = $50-500/day in API costs. On a $10K account targeting $20-50/day profit, that's absurd.

4. **Reliability.** LLMs hallucinate. A hallucinated signal or a misclassified risk level could blow up the account. Deterministic code is predictable and testable.

5. **Debuggability.** When a trade goes wrong, we need to trace exactly why. "The LLM decided to buy" is not debuggable. "RSI crossed 70, volume > 2x avg, sector RS > 1.2, score = 8.3 > threshold 7.0" is.

**Where LLMs DO add value (use surgically):**
| Component | Why LLM helps |
|---|---|
| News sentiment classification | Natural language understanding is genuinely hard to do with rules |
| Catalyst magnitude estimation | Context-dependent judgment |
| Weekly strategy review (Cdius) | Pattern recognition across many trades, natural language explanation |
| Anomaly explanation | "Why did we lose 1.5% today?" requires narrative reasoning |

**Architecture principle:** Python for speed, LLMs for judgment. Never mix them in the hot path.

```
HOT PATH (pure Python, <100ms):
  Market data → Screen → Signal → Risk check → Execute

WARM PATH (LLM-assisted, async, non-blocking):
  News → Sentiment classification → Catalyst scoring

COLD PATH (LLM, on-demand):
  Cdius supervision, strategy reviews, Filip's questions
```

---

**Data flow:** Sentinel → News/Screener → Signals → Risk check → Execute → Log → Analyze → Notify

**Shared state (Redis or in-memory):**
- Market regime + sector rankings (Agent 1 writes, all read)
- Catalyst queue (Agent 2 writes, Agents 3-4 read)
- Candidate watchlist (Agent 3 writes, Agent 4 reads)
- Open positions (Agent 5 writes, Agent 6 monitors)
- Account state / DTBP (Agent 6 maintains)
- Trade log (Agent 5 writes, Agent 7 analyzes)

**Timing:**
| Agent | Frequency |
|---|---|
| Market Sentinel | Every 1 min (market hours), 15 min (pre/post) |
| News & Catalyst Hunter | Every 2 min (market hours), 30 min (overnight) |
| Stock Screener | Every 5 min (market hours) |
| Signal Generator | On-demand (when screener outputs new candidates) |
| Execution Manager | Immediate (on signal), then every 30 sec for position management |
| Risk Controller | Every 30 sec (continuous) |
| Portfolio Analyst | End of day + on-demand |
| Notification Agent | Event-driven |

---

## Implementation Plan

### Phase 0: Infrastructure
- [ ] Set up IBKR paper trading account
- [ ] Install IB Gateway on Mac mini (paper mode)
- [ ] Create Python project repo (`agentic-investment`)
- [ ] `pip install ib_async` + project scaffolding
- [ ] Basic connection test — connect to Gateway, fetch account info

### Phase 1: Market Scanner — "What's Moving?"
The brain of the system. Instead of a fixed stock universe, we scan for momentum in real-time.

**Module: `scanner/`**

**1a. Sector Tracker (`scanner/sectors.py`)**
- Track all 11 GICS sectors + key sub-industries
- Data source: sector ETFs as proxies (XLK, XLF, XLE, XLV, XLI, XLC, XLY, XLP, XLU, XLRE, XLB)
- Monitor: daily % change, volume vs avg, relative strength vs SPY
- Output: ranked list of hot/cold sectors with momentum scores

**1b. News & Catalyst Engine (`scanner/news.py`)**
- Sources to evaluate:
  - IBKR news feed (built into API)
  - SEC filings (8-K, earnings) via EDGAR API (free)
  - Financial news APIs (Benzinga, Alpha Vantage, or free alternatives)
  - Pre-market/after-hours movers (IBKR scanner)
- NLP classification: categorize news as bullish/bearish/neutral
- Link catalysts to specific stocks and sectors

**1c. Stock Screener (`scanner/screener.py`)**
- Use IBKR's built-in market scanner API (supports pre-market/intraday/post-market)
- Scan criteria:
  - Top % gainers/losers (momentum)
  - Unusual volume (>2x average)
  - New 52-week highs/lows
  - Stocks breaking above resistance / below support
  - High relative volume in hot/cold sectors (from 1a)
- Filter: min market cap ($500M+?), min avg volume, optionable
- Output: ranked candidate list with scores

### Phase 2: Signal Generator — "When to Enter?"
Takes scanner output and generates actionable trade signals.

**Module: `signals/`**

**2a. Technical Analysis (`signals/technical.py`)**
- Breakout detection: price crossing key levels (daily highs, resistance, moving averages)
- Momentum indicators: RSI, MACD, rate of change
- Volume confirmation: entry only when volume supports the move
- Multi-timeframe: confirm on both intraday (5m/15m) and daily

**2b. Signal Scoring (`signals/scoring.py`)**
- Composite score combining:
  - Sector momentum (from Phase 1a)
  - News sentiment (from Phase 1b)
  - Technical strength (from Phase 2a)
  - Volume conviction
- Threshold: only trade signals above minimum score
- Output: BUY/SHORT signal with confidence level, target, stop

### Phase 3: Execution Engine — "How to Trade?"
Handles order management, position sizing, and risk.

**Module: `execution/`**

**3a. Position Sizing (`execution/sizing.py`)**
- Risk per trade: max 1% of equity ($100 on $10K)
- Size = risk amount / distance to stop loss
- Max positions: 5-10 concurrent
- Max sector exposure: 30% of equity

**3b. Order Manager (`execution/orders.py`)**
- Entry: limit orders (avoid slippage) or market if fast-moving
- Stop loss: automatic, placed immediately after entry
- Take profit: trailing stop or fixed target (configurable per signal strength)
- Short selling: check borrow availability via IBKR API before entry

**3c. Risk Manager (`execution/risk.py`)**
- Daily drawdown limit: 2% ($200) → stop trading for the day
- Per-trade loss limit: 1% of equity
- Correlation check: avoid piling into the same sector
- Kill switch: manual override to flatten all positions

### Phase 4: Portfolio & Monitoring
Track everything, learn, improve.

**Module: `portfolio/`**

**4a. Position Tracker (`portfolio/tracker.py`)**
- Real-time P&L per position and total
- Entry/exit logging with full context (signal, score, news catalyst)

**4b. Notifications (`portfolio/notify.py`)**
- Trade alerts → WhatsApp (via Cdius)
- Daily summary: trades taken, P&L, win rate
- Risk alerts: drawdown warnings

**4c. Performance Analytics (`portfolio/analytics.py`)**
- Win rate, avg win/loss ratio, Sharpe ratio
- Per-sector performance
- Signal quality analysis (which signals actually make money?)
- Backtest comparison

### Phase 5: Iteration & Intelligence
Once the base system works on paper:
- [ ] Backtest the strategy on historical data
- [ ] Tune signal parameters based on paper trading results
- [ ] Add ML layer for signal scoring (optional, later)
- [ ] Go live with small capital, scale up gradually

## Project Structure (proposed)
```
agentic-investment/
├── config/
│   ├── settings.py          # API keys, thresholds, risk params
│   ├── sectors.py           # Sector definitions, ETF mappings
│   └── starting_portfolio.csv  # Filip's pre-existing positions (SACRED, never sell)
├── scanner/
│   ├── sectors.py           # Sector momentum tracker
│   ├── news.py              # News & catalyst engine
│   └── screener.py          # Stock screener / market scanner
├── signals/
│   ├── technical.py         # Technical analysis
│   └── scoring.py           # Composite signal scoring
├── execution/
│   ├── sizing.py            # Position sizing
│   ├── orders.py            # Order management
│   └── risk.py              # Risk management
├── portfolio/
│   ├── tracker.py           # Position tracking
│   ├── notify.py            # Notifications (WhatsApp)
│   └── analytics.py         # Performance analytics
├── data/
│   ├── store.py             # Data persistence (PostgreSQL/asyncpg)
│   └── models.py            # Data models
├── main.py                  # Entry point / scheduler
├── backtest.py              # Backtesting harness
└── tests/
```

**Note:** `execution/dtbp_guard.py` is added to the execution module — see DTBP section above.
```

---

## Backtesting Framework

### Objective

Validate the strategy on historical data before risking capital. Backtesting answers: "Would this system have made money in the past?" — with full awareness that past performance ≠ future results, but no backtest = no deployment.

### Data Requirements

| Data Type | Source | Granularity | History Needed | Reason |
|---|---|---|---|---|
| OHLCV (price bars) | IBKR historical data API | 1-min, 5-min, daily | 2 years minimum (500+ trading days) | Technical indicators, signal generation, regime detection |
| Sector ETF prices | IBKR / Yahoo Finance | Daily + intraday 5-min | 2 years | Sector momentum, relative strength, regime classification |
| VIX | CBOE via IBKR | Daily close + intraday | 2 years | Market regime detection |
| Average volume (20-day) | Derived from OHLCV | Daily | Rolling 20-day | Volume confirmation signals |
| Corporate events | Earnings Whispers / SEC EDGAR | Event-level | 2 years | Catalyst backtesting |
| Short interest | IBKR / FINRA | Bi-monthly | 1 year | Short squeeze risk modeling |

**Minimum backtest period:** 2024-01-01 to 2025-12-31 (covers bull run, corrections, rate cut cycle, VIX spikes). Ideally extend to 2022-01-01 to include a real bear market.

**Universe construction:** Backtest on the top 200 stocks by dollar volume each month (reconstituted monthly to avoid survivorship bias). Do NOT use today's hot list retroactively.

### Backtest Architecture

```python
class Backtester:
    """
    Event-driven backtester. NOT vectorized — must simulate order fills,
    slippage, and agent timing realistically.
    """
    
    def __init__(self, start_date, end_date, initial_capital=10_000):
        self.clock = MarketClock(start_date, end_date)
        self.data_feed = HistoricalDataFeed(...)  # replays bars in order
        self.portfolio = SimulatedPortfolio(initial_capital)
        self.agents = [...]  # same agent code as live, different data source
        self.fill_model = RealisticFillModel(slippage_bps=5, partial_fill_prob=0.05)
    
    def run(self):
        for bar in self.data_feed:
            self.clock.advance(bar.timestamp)
            # Agents see ONLY data up to current bar (no look-ahead)
            market_state = self.data_feed.get_state(up_to=bar.timestamp)
            signals = self.signal_generator.evaluate(market_state)
            for signal in signals:
                order = self.execution_manager.create_order(signal)
                fill = self.fill_model.simulate(order, bar)
                self.portfolio.apply(fill)
            self.risk_controller.check(self.portfolio)
```

### Key Metrics

| Metric | Target (Paper Graduation) | Formula |
|---|---|---|
| Total Return | > 30% annualized | (final_equity / initial_equity - 1) × (252 / trading_days) |
| Sharpe Ratio | > 1.5 | mean(daily_returns) / std(daily_returns) × √252 |
| Sortino Ratio | > 2.0 | mean(daily_returns) / downside_deviation × √252 |
| Max Drawdown | < 8% | max peak-to-trough decline |
| Win Rate | > 50% | winning_trades / total_trades |
| Profit Factor | > 1.8 | gross_profit / gross_loss |
| Avg Win / Avg Loss | > 1.5 | mean(winning_pnl) / abs(mean(losing_pnl)) |
| Max Consecutive Losses | < 8 | Longest losing streak |
| Daily VaR (95%) | < 1.5% | 5th percentile of daily returns |
| Calmar Ratio | > 3.0 | annualized_return / max_drawdown |
| Trades per Day | 1-5 | Total trades / trading days |

### Bias Prevention

| Bias | Description | Mitigation |
|---|---|---|
| **Survivorship bias** | Testing only on stocks that exist today; delisted/bankrupt stocks excluded | Use reconstituted universe from historical constituent lists. Include delisted tickers. |
| **Look-ahead bias** | Using data not available at decision time (e.g., today's close to make today's decision) | Event-driven engine with strict timestamp gating. Agents receive `data[t-1]` for decisions at time `t`. |
| **Overfitting** | Tuning parameters to fit historical noise | Walk-forward optimization: train on 12 months, test on next 3 months, roll forward. Never optimize on full period. Maximum 10 free parameters. |
| **Transaction cost neglect** | Ignoring commissions, slippage, borrow costs | Model: $0.005/share commission (IBKR tiered), 5 bps slippage per side, 0.5-3% annualized short borrow. |
| **Selection bias** | Cherry-picking the backtest period | Test across multiple regimes: 2022 bear, 2023 recovery, 2024 bull, 2025 mixed. Report worst period prominently. |

### Walk-Forward Optimization Protocol

```
Period 1: Train 2022-01 to 2022-12 → Test 2023-01 to 2023-03
Period 2: Train 2022-04 to 2023-03 → Test 2023-04 to 2023-06
Period 3: Train 2022-07 to 2023-06 → Test 2023-07 to 2023-09
...
Final out-of-sample = concatenation of all test periods
```

Report out-of-sample Sharpe. If in-sample Sharpe > 2× out-of-sample Sharpe, the strategy is overfit. Discard or simplify.

---

## Data Pipeline Architecture

### Data Flow

```
┌──────────────────────────────────────────────────────────┐
│                    DATA SOURCES                           │
│  IBKR API (price/volume)  │  News APIs  │  SEC EDGAR    │
│  Sector ETFs              │  VIX/SPY    │  Earnings Cal  │
└───────────┬──────────────┬─────────────┬─────────────────┘
            │              │             │
            ▼              ▼             ▼
┌──────────────────────────────────────────────────────────┐
│              INGESTION LAYER (data/ingest.py)             │
│  - Rate limiting (IBKR: 50 req/sec hist data)            │
│  - Deduplication                                          │
│  - Schema validation                                      │
│  - Timestamp normalization (all ET)                       │
└───────────────────────┬──────────────────────────────────┘
                        │
                        ▼
┌──────────────────────────────────────────────────────────┐
│              PostgreSQL Database (asyncpg)                  │
│  See schema below                                         │
└───────────────────────┬──────────────────────────────────┘
                        │
                        ▼
┌──────────────────────────────────────────────────────────┐
│              DERIVED DATA LAYER (data/indicators.py)      │
│  SMA, EMA, RSI, MACD, ATR, relative strength, etc.       │
│  Computed on read, cached in memory (Redis or dict)       │
└───────────────────────┬──────────────────────────────────┘
                        │
                        ▼
┌──────────────────────────────────────────────────────────┐
│                    AGENT CONSUMERS                         │
│  Sentinel  │  Screener  │  Signal Gen  │  Risk Ctrl      │
└──────────────────────────────────────────────────────────┘
```

### PostgreSQL Schema

```sql
-- Price data (OHLCV)
CREATE TABLE bars (
    id SERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    timestamp BIGINT NOT NULL,          -- Unix epoch (seconds)
    timeframe TEXT NOT NULL,            -- '1m', '5m', '1d'
    open DOUBLE PRECISION NOT NULL,
    high DOUBLE PRECISION NOT NULL,
    low DOUBLE PRECISION NOT NULL,
    close DOUBLE PRECISION NOT NULL,
    volume BIGINT NOT NULL,
    vwap DOUBLE PRECISION,
    trade_count INTEGER,
    UNIQUE(symbol, timestamp, timeframe)
);
CREATE INDEX idx_bars_sym_ts ON bars(symbol, timestamp);
CREATE INDEX idx_bars_ts ON bars(timestamp);

-- Sector ETF tracking
CREATE TABLE sector_snapshots (
    id SERIAL PRIMARY KEY,
    timestamp BIGINT NOT NULL,
    sector TEXT NOT NULL,               -- 'XLK', 'XLF', etc.
    price DOUBLE PRECISION NOT NULL,
    change_pct DOUBLE PRECISION,
    volume BIGINT,
    relative_strength DOUBLE PRECISION, -- vs SPY
    momentum_score DOUBLE PRECISION,
    UNIQUE(sector, timestamp)
);

-- News & catalysts
CREATE TABLE catalysts (
    id SERIAL PRIMARY KEY,
    timestamp BIGINT NOT NULL,
    symbol TEXT,                         -- NULL if sector-wide
    sector TEXT,
    headline TEXT NOT NULL,
    source TEXT NOT NULL,
    sentiment DOUBLE PRECISION,          -- -1.0 to +1.0
    magnitude INTEGER,                  -- 1-5 (CSS score)
    catalyst_type TEXT,                 -- 'earnings', 'fda', 'upgrade', etc.
    raw_text TEXT,
    llm_analysis TEXT                   -- cached LLM sentiment output
);
CREATE INDEX idx_catalysts_sym ON catalysts(symbol, timestamp);

-- Trade log
CREATE TABLE trades (
    id SERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,            -- 'LONG' or 'SHORT'
    entry_time BIGINT NOT NULL,
    entry_price DOUBLE PRECISION NOT NULL,
    entry_shares INTEGER NOT NULL,
    exit_time BIGINT,
    exit_price DOUBLE PRECISION,
    exit_shares INTEGER,
    pnl DOUBLE PRECISION,
    pnl_pct DOUBLE PRECISION,
    signal_score DOUBLE PRECISION,
    signal_reason TEXT,                 -- JSON: what triggered entry
    exit_reason TEXT,                   -- 'trailing_stop', 'time_decay', etc.
    catalyst_id INTEGER REFERENCES catalysts(id),
    regime TEXT,                        -- market regime at entry
    sector TEXT
);

-- Account snapshots (for equity curve)
CREATE TABLE account_snapshots (
    id SERIAL PRIMARY KEY,
    timestamp BIGINT NOT NULL,
    net_liquidation DOUBLE PRECISION NOT NULL,
    cash DOUBLE PRECISION,
    buying_power DOUBLE PRECISION,
    day_trades_remaining INTEGER,
    daily_pnl DOUBLE PRECISION,
    open_positions INTEGER,
    portfolio_heat DOUBLE PRECISION     -- total % at risk
);

-- Market regime log
CREATE TABLE regime_log (
    id SERIAL PRIMARY KEY,
    timestamp BIGINT NOT NULL,
    regime TEXT NOT NULL,               -- 'strong_bull', 'bull', 'choppy', 'bear', 'crisis'
    spy_vs_20sma DOUBLE PRECISION,
    spy_vs_50sma DOUBLE PRECISION,
    vix DOUBLE PRECISION,
    regime_factor DOUBLE PRECISION
);
```

### Historical Data Requirements for Indicators

| Indicator | Lookback Period | Bars Needed (daily) | Bars Needed (5-min) | Warm-up Note |
|---|---|---|---|---|
| SMA(20) | 20 days | 20 | 1,560 (20 × 78 bars/day) | Need 20 complete periods before first valid value |
| SMA(50) | 50 days | 50 | 3,900 | First valid on day 50 |
| EMA(12), EMA(26) — MACD | 26 days + 9 signal | 35 | 2,730 | MACD signal line needs extra 9 periods |
| RSI(14) | 14 days + smoothing | 28 | 2,184 | Wilder smoothing needs ~2× lookback to stabilize |
| ATR(14) | 14 days | 14 | 1,092 | True range requires previous close |
| HV(20) — 20-day realized vol | 20 days | 21 | — | Daily only; needs 21 closes for 20 returns |
| Relative Strength (20-day) | 20 days | 20 | — | Stock return / sector return |
| Volume average (20-day) | 20 days | 20 | 1,560 | Rolling mean |

**Minimum history to load at startup:** 60 trading days of daily bars (covers all indicators with warm-up). For intraday: 5 days of 1-min bars at minimum.

**On first boot:** Fetch 2 years of daily bars for the current scan universe (~200 tickers × 500 bars = 100K rows). IBKR rate limit: ~50 historical data requests/sec → full fetch in ~1 hour. Cache in PostgreSQL. Incremental updates thereafter.

### Data Retention Policy

| Data Type | Retention | Size Estimate |
|---|---|---|
| Daily OHLCV | Indefinite | ~50 MB/year for 200 tickers |
| 5-min OHLCV | 90 days rolling | ~2 GB for 200 tickers |
| 1-min OHLCV | 5 days rolling | ~1 GB for 200 tickers |
| Catalysts/news | Indefinite | ~100 MB/year |
| Trade log | Indefinite | Negligible |
| Account snapshots | Indefinite | Negligible |

**Total disk:** < 10 GB. PostgreSQL handles this with room to scale to 1000+ tickers or sub-second bars.

### Data Integrity Checks

Run daily at market close:
1. No gaps in daily bars for tracked symbols (weekends/holidays excluded)
2. OHLC validity: `low ≤ open ≤ high`, `low ≤ close ≤ high`
3. Volume > 0 for all market-hours bars
4. Timestamp monotonically increasing per symbol
5. Alert on any symbol with > 1% price change but zero news/catalyst entries (possible missed event)

---

## Paper Trading Graduation Criteria

The system moves from paper to live ONLY when ALL of the following are met. No exceptions. No "it looks good enough."

### Minimum Duration
- **30 trading days** on paper (6 calendar weeks minimum)
- Must include at least **1 VIX > 25 day** (system must see stress)
- Must include at least **1 FOMC day** (regime shift test)
- Must span at least **2 different market regimes** (e.g., bull + choppy)

### Performance Thresholds

| Metric | Minimum | Ideal | Kill (restart paper period) |
|---|---|---|---|
| Sharpe Ratio (annualized) | ≥ 1.2 | ≥ 2.0 | < 0.5 |
| Win Rate | ≥ 48% | ≥ 55% | < 40% |
| Profit Factor | ≥ 1.5 | ≥ 2.0 | < 1.0 (losing money) |
| Max Drawdown | ≤ 6% | ≤ 3% | > 10% |
| Avg Win / Avg Loss | ≥ 1.3 | ≥ 2.0 | < 0.8 |
| Daily PnL Std Dev | ≤ 1.0% of equity | ≤ 0.5% | > 2.0% |
| Max Single-Day Loss | ≤ 2.0% | ≤ 1.0% | > 3.0% |
| Trades per Day | 1-5 avg | 2-4 avg | > 10 (overtrading) or 0 for 5+ days |

### System Reliability Thresholds

| Metric | Requirement |
|---|---|
| Uptime during market hours | ≥ 99% (max 4 min downtime per day) |
| Order fill rate | ≥ 95% (limit orders that execute) |
| DTBP violations | 0 (zero tolerance) |
| PDT violations | 0 (zero tolerance) |
| Risk controller overrides | < 5% of trading days (should rarely fire) |
| Unhandled exceptions | 0 during market hours |
| Missed signals (system lag > 30s) | < 2% of signals |
| Data feed gaps | < 0.1% of expected bars |

### Graduation Protocol

1. Paper trading runs for ≥ 30 days
2. All performance thresholds met (check weekly)
3. All reliability thresholds met
4. Filip reviews trade log and approves signal quality
5. **Week 1 live:** 25% of target position sizes ($2,500 effective capital)
6. **Week 2 live:** 50% if Week 1 metrics hold
7. **Week 3 live:** 75%
8. **Week 4+:** 100% if all metrics sustained
9. **Any week with max drawdown > 4%:** Drop back one tier

### Automatic Paper Reversion Triggers (Live → Paper)

If ANY of these occur in live trading, revert to paper immediately:
- Single-day loss > 3% of equity
- 3 consecutive losing days totaling > 4%
- Sharpe drops below 0.8 over trailing 20 days
- Any DTBP or PDT violation
- Unhandled exception during market hours
- IB Gateway disconnect > 5 minutes without graceful position handling

---

## Edge Cases & Failure Modes

### Infrastructure Failures

| Failure | Impact | Detection | Response |
|---|---|---|---|
| **IB Gateway disconnect** | Can't send orders, can't get prices | `ib_async` connection status callback + heartbeat ping every 10s | 1) Auto-reconnect (ib_async built-in). 2) If disconnected > 60s with open positions: alert Filip immediately. 3) If disconnected > 5 min: assume worst, prepare to flatten on reconnect. 4) Never enter NEW positions during reconnection. |
| **IB Gateway crash** | Total system down | Process monitor (systemd/launchd watchdog) | Auto-restart Gateway. Bot waits for reconnection. If restart fails 3×, alert Filip + halt. |
| **Mac Mini power loss** | Everything dies | UPS with USB monitoring (if available) | On boot: reconcile positions with IBKR (query open orders/positions). Cancel stale orders. Verify stops are in place. |
| **Internet outage** | Same as Gateway disconnect | Ping test to 8.8.8.8 every 30s | Alert via cellular backup if available. Otherwise, IBKR server-side stops protect positions. |
| **PostgreSQL down** | Loss of trade history, indicator state | Daily pg_dump backup + health checks | Restore from backup. Indicator state rebuilds from raw bars. Trade log is also in IBKR's own records. |
| **LLM API down (OpenAI/Anthropic)** | No news sentiment scoring | HTTP timeout + retry with backoff | Degrade gracefully: skip news sentiment, trade on technical signals only. Log degraded mode. |

### Market Events

| Event | Impact | Response |
|---|---|---|
| **Market halt (LULD / circuit breaker)** | Can't trade halted stock, price gaps on resume | Detect via IBKR `securityDefinitionOptionalParameter` or error code on order. Cancel pending orders on halted stock. On resume: re-evaluate — gap may have blown past stop. Use market order to exit if stop was breached. |
| **Flash crash** | Extreme price dislocations, fills at absurd prices | ATR-based stops auto-adjust. If price drops > 5× ATR in < 1 minute, do NOT chase — likely a data issue or will bounce. Flag for manual review. |
| **Overnight gap** | Stock opens far from previous close, past our stop | **This is the #1 risk of overnight holds.** Mitigation: 1) Position size already accounts for gap risk (1% equity risk assumes 2× ATR gap). 2) Pre-market scan: if position gaps > 3× ATR against us, evaluate immediately at open — don't wait for trailing stop logic. 3) Consider stop-limit orders for after-hours/pre-market on highly volatile names. |
| **Short squeeze** | Infinite theoretical loss on short positions | Monitor short interest and borrow rate. If borrow rate > 50% annualized OR short interest > 30% of float: max position size = 0.5% equity (half normal). Hard stop at 3× ATR on all shorts (no exception). If stock gaps > 5× ATR on a short: market order to cover immediately, do not wait. |
| **Earnings after-hours** | Massive gap, usually > 5% | NEVER hold through earnings unless explicitly approved by Filip. Check earnings calendar daily. Auto-exit positions that have earnings within 24 hours (configurable). |
| **Dividend ex-date** | Price drops by dividend amount | Irrelevant for short-term holds. But if short: we PAY the dividend. Check ex-dates for short positions. |
| **Stock split / reverse split** | Price and share count change | IBKR handles this transparently for open positions. But our historical data needs adjustment — use IBKR's adjusted data or apply split factor retroactively. |

### Order Execution Edge Cases

| Scenario | Response |
|---|---|
| **Partial fill** | Track filled vs. unfilled quantity. If < 50% filled after 30 seconds on a limit order, decide: cancel remainder (if momentum fading) or convert to market (if signal still strong). Adjust stop to cover partial position. |
| **Stuck order (pending > 60s)** | Cancel and re-evaluate. Price may have moved. If still valid, resubmit at current market. Never let an order sit stale. |
| **Order rejected (insufficient margin)** | Log rejection reason. Reduce order size by 25% and retry once. If rejected again, skip trade. Alert if this happens > 2× in a day (capital allocation issue). |
| **Order rejected (not shortable)** | Skip trade entirely. Do not attempt workarounds. Log for post-market analysis. |
| **Fill at worse price than expected** | Slippage > 2× ATR from signal price: mark trade as "degraded entry." Tighten stop proportionally — if entry is worse, max acceptable loss stays the same, so stop is closer. |
| **Duplicate order (race condition)** | Use order IDs + deduplification. Before submitting, check open orders for same symbol. If duplicate detected, cancel one immediately. |
| **IBKR rate limit (50 msg/sec)** | Queue orders, process in order of priority (risk exits first, then new entries). Never exceed 45 msg/sec to leave headroom. |

### Data Edge Cases

| Scenario | Response |
|---|---|
| **Stale price data (last update > 60s during market hours)** | Do not trade on stale data. Flag symbol as "data stale." If persistent > 5 min, remove from candidate list. |
| **Price spike / bad tick** | If bar shows > 10× ATR move on single tick, treat as suspect. Cross-reference with bid/ask spread. Ignore if bid-ask gap > 5%. |
| **Volume data delay** | Volume often lags price by seconds. Use price-only signals as primary, volume as confirmation. Don't block entry waiting for volume. |
| **Corporate action (ticker change, merger)** | IBKR sends contract update events. Close position on old contract. Re-evaluate under new ticker/terms. |

### Agent-Specific Failure Modes

| Agent | Failure | Impact | Mitigation |
|---|---|---|---|
| Market Sentinel | Regime misclassification | Wrong position sizing, wrong stop widths | Use multiple confirming indicators (SPY SMA + VIX + breadth). Require 2 of 3 to confirm regime change. Lag regime shifts by 1 day (don't flip on a single bar). |
| News Hunter | Sentiment misclassification | Enter wrong direction | LLM sentiment is supplementary, not primary. No trade is taken on news alone — must have technical confirmation. Confidence threshold: sentiment magnitude ≥ 3 to influence signals. |
| Signal Generator | False signal (meets all criteria but fails) | Loss on trade | This is expected — win rate target is 50-55%, not 100%. Position sizing ensures any single loss ≤ 1% equity. No single false signal is catastrophic. |
| Execution Manager | Fills at wrong price | Larger loss than planned | Slippage budget of 5 bps per side built into all sizing calculations. If actual slippage consistently > 10 bps: switch to more liquid names only. |
| Risk Controller | Fails to fire (bug) | Unlimited losses | Defense in depth: IBKR server-side bracket orders (stop + target) placed on every entry. Even if our code fails, IBKR's stops protect us. This is the last line of defense. |

---

## Cost Analysis

### IBKR Costs

| Item | Cost | Notes |
|---|---|---|
| **Commissions (US equities, tiered)** | $0.0035/share, min $0.35/order, max 1% of trade value | At ~50 shares/trade avg, ~$0.18/trade. ~5 trades/day = ~$0.90/day |
| **Market data — US Securities Snapshot & Futures** | $10/month | Required for real-time US equities |
| **Market data — NASDAQ Level 1** | $1.50/month | Top of book for NASDAQ-listed |
| **Market data — NYSE/AMEX Level 1** | $1.50/month | Top of book for NYSE-listed |
| **Market data — OPRA (US Options)** | $1.50/month (non-pro) | If we want options flow data |
| **Short borrow fees** | Variable: 0.25%-100%+ annualized | Hard-to-borrow stocks can be expensive. Budget 1% annualized on avg short exposure. On $2K avg short: ~$0.05/day |
| **Paper trading** | Free | No market data fees on paper account — uses delayed data. For real-time paper: need live data subscription ($14.50/month). |
| **IB Gateway** | Free | Included with IBKR account |

**IBKR total: ~$15-20/month fixed + ~$0.90/day variable ≈ $35-40/month**

### LLM API Costs (News Sentiment)

| Usage | Model | Calls/Day | Tokens/Call | Cost/Call | Daily Cost |
|---|---|---|---|---|---|
| News sentiment classification | Claude Haiku / GPT-4o-mini | ~50-100 | ~500 input + 100 output | ~$0.0005 | $0.025-0.05 |
| Catalyst magnitude scoring | Claude Sonnet / GPT-4o | ~10-20 | ~1000 input + 200 output | ~$0.005 | $0.05-0.10 |
| Weekly strategy review (Cdius) | Claude Opus / GPT-4 | 2-4/week | ~5000 input + 2000 output | ~$0.10 | $0.06 (amortized) |
| Anomaly explanation | Claude Sonnet | ~1-2/day | ~2000 input + 500 output | ~$0.01 | $0.01-0.02 |

**LLM total: ~$0.15-0.25/day ≈ $4-6/month**

Using cheaper models (Haiku/GPT-4o-mini) for high-volume sentiment work keeps costs negligible. This validates the "no LLM in the hot path" decision — it's cheap because we use it surgically.

### Compute Costs

| Item | Cost | Notes |
|---|---|---|
| **Mac Mini (existing)** | $0 incremental | Already owned. Bot uses < 500 MB RAM, < 5% CPU. |
| **Electricity** | ~$3-5/month | Mac Mini draws ~10-15W under light load, 24/7 |
| **VPS (future, if needed)** | $20-50/month | Hetzner/DigitalOcean for redundancy. Not needed initially. |

### External Data (Optional)

| Service | Cost | Value |
|---|---|---|
| **Benzinga Pro (news feed)** | $99/month | Real-time news. Start without it — use free sources first. |
| **Unusual Whales (options flow)** | $40/month | Options flow data. Nice-to-have, not essential for v1. |
| **SemiAnalysis** | $100/month | Deep semiconductor analysis. Useful for Filip's holdings, not for the bot. |
| **Quiver Quant (alternative data)** | Free tier available | Congressional trades, insider data. |

### Total Monthly Cost

| Category | Minimum (v1) | With Extras |
|---|---|---|
| IBKR fixed | $15 | $15 |
| IBKR variable (commissions) | $20 | $20 |
| LLM APIs | $5 | $8 |
| Compute | $0 | $40 (add VPS) |
| External data | $0 | $140 |
| **Total** | **$40/month** | **$223/month** |

### Break-Even Analysis

At $40/month cost and $10K equity:
- Need 0.4% monthly return just to cover costs
- Daily target of 0.2-0.5% ($20-50) → monthly ~$400-1000
- **Cost is ~4-10% of expected monthly profit** — acceptable
- At $223/month with extras: need ~2.2% monthly return to break even — still easily within target if strategy works

**Conclusion:** Costs are not the constraint. Strategy edge is the constraint. Start with the $40/month stack and add paid data sources only if they demonstrably improve signal quality.

---

## Log
- 2026-02-01: Project created
- 2026-02-01: API decision — `ib_async` + IB Gateway (not Client Portal, not raw ibapi)
- 2026-02-02: Added Backtesting Framework, Data Pipeline Architecture, Paper Trading Graduation Criteria, Edge Cases & Failure Modes, and Cost Analysis sections
