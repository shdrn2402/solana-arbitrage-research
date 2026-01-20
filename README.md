# Solana Arbitrage Bot (v2.0.0)

Off-chain arbitrage research bot for Solana using the Jupiter Aggregator API for studying and evaluating arbitrage opportunities.

## Project Status

This project is an experimental / research prototype developed iteratively.

### ✅ Stage 1 — Scan (completed)
- **Stable Jupiter API integration** — reliable quote retrieval via public API
- **2-swap cross-AMM execution plans** — execution plans with DEX pairs as key identifier
- **DEX-pair identification** — Plan USDC→SOL→USDC with Ray→Orca ≠ Plan USDC→SOL→USDC with Orca→Ray
- **Hard 1-hop enforcement** — strictly 1-hop per leg (no multi-hop routes)
- **Hard liquidity skip** — rejects plans with anomalously weak liquidity (>5% price impact)
- **Configurable token universe** — adjustable tokens and execution plans via config.json
- **Quota-optimized scanning** — rate-limited execution (60 requests/minute) with configurable delays
- **Repository default: 2-swap execution plans** — USDC→SOL→USDC and SOL→USDC→SOL
- **Repository default tokens: SOL, USDC** — 2 tokens for cross-AMM arbitrage
- **Read-only mode** — no on-chain execution or fund usage

### 🛠️ Stage 2 — Simulation (in progress)
- **Inline arbitrage loop** — infinite inline cycle: quote-filter → immediate simulate (no batches)
- **Atomic VersionedTransaction v0** — full 2-swap atomic execution (all-or-nothing)
- **On-chain transaction simulation** via RPC `simulateTransaction` (no real transactions sent)
- **useSharedAccounts=False** — hard requirement for 2-swap (no retry with True)
- **Negative cache** — TTL-based cache for size overflow and runtime 6024 errors
- **Priority fee & latency experiments**
- **Execution feasibility analysis**

### 🧪 Stage 3 — Live Execution (experimental)
- **Atomic VersionedTransaction v0** — full 2-swap atomic execution (all-or-nothing)
- **Real transaction submission** to Solana network
- **Mandatory simulation** before execution
- **useSharedAccounts=False** — hard requirement for 2-swap
- **Optional Jito integration**
- **Research-only, not production-ready**

## Features

- ✅ **Off-chain architecture** - bot operates outside the blockchain
- ✅ **Jupiter API integration** - uses current Jupiter Aggregator API
- ✅ **Risk & Capital Management** - comprehensive risk and capital management system
- ✅ **Three operation modes**: scan, simulate, live
- ✅ **Mandatory simulation** before execution
- ✅ **Configurable limits** via .env and config.json
- ✅ **Priority fee support** and optional Jito

## Requirements

- Python 3.9+
- Solana wallet with private key (for simulate/live modes)
- Access to Solana RPC (can use public or your own)

## Installation

1. Clone the repository or copy project files

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Configure settings:
```bash
cp env.example .env
# Edit .env file
```

4. For `simulate` and `live` modes, add wallet private key:
   - Get private key from your wallet (base58 format)
   - Add to `.env`: `WALLET_PRIVATE_KEY=your_base58_private_key`
   - ⚠️ **WARNING**: Never publish your private key!

5. Configure `config.json` if needed (optional)

## Configuration

### .env file

Main parameters (see `env.example` for complete list):

```env
# Solana RPC Configuration
# RPC URL selection by mode:
# - scan: uses RPC_URL_SCAN (free/public RPC, doesn't consume trial credits)
# - simulate/live: uses RPC_URL_PRIMARY (trial/premium RPC) with automatic failover to RPC_URL_FALLBACK
RPC_URL_SCAN=https://api.mainnet-beta.solana.com  # For scan mode (free/public)
RPC_URL_PRIMARY=  # Required for simulate/live modes (trial/premium RPC)
RPC_URL_FALLBACK=  # Optional fallback for simulate/live modes

# Deprecated: RPC_URL (kept for backward compatibility)
RPC_URL=https://api.mainnet-beta.solana.com

# Operation Mode
# Note: Mode is now specified via command line argument (default: scan)
# Usage: python run.py [scan|simulate|live]

# Wallet private key (base58, required for simulate/live modes)
WALLET_PRIVATE_KEY=your_private_key_here

# Risk Management (all absolute limits in USDC)
MAX_POSITION_SIZE_PERCENT=10.0  # Maximum position size (% of balance)
MAX_POSITION_SIZE_ABSOLUTE=1.0  # in SOL
# For first night run: use softer thresholds to see regular simulate_success
# After first night: raise thresholds based on hit-rate statistics
MIN_PROFIT_USDC=0.03  # PRIMARY: minimum profit in USDC (softened for first night, was 0.1)
MIN_PROFIT_BPS=5  # SECONDARY: optional filter in basis points (softened for first night, was 50, set to 0 to disable)
MAX_SLIPPAGE_BPS=200  # Maximum allowed slippage (risk limit)
SLIPPAGE_BPS=100  # Actual slippage used in Jupiter API requests
MAX_ACTIVE_POSITIONS=1

# Arbitrage Configuration
QUOTE_DELAY_SECONDS=1.0  # Delay between requests (for 60 req/min limit)

# Priority Fee
PRIORITY_FEE_LAMPORTS=10000
USE_JITO=false

# Logging
LOG_LEVEL=INFO
```

Or use `env.example` as template:
```bash
cp env.example .env
# Edit .env with your values
```

### config.json

Additional settings:

```json
{
  "tokens": {
    "SOL": "So11111111111111111111111111111111111111112",
    "USDC": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "JUP": "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
    "BONK": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
    "WIF": "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",
    "RAY": "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R"
  },
  "arbitrage": {
    "min_profit_usdc": 0.1,
    "max_cycle_length": 4,
    "max_cycles": 100,
    "quote_timeout": 5.0
  },
  "cycles": []
}
```

**Note:** The `cycles` is empty in this example for brevity — use the repository config.json to get the full 20-cycle list.

## Usage

### Scan Mode

Read-only mode for analyzing potential arbitrage paths without executing transactions:

```bash
# Default mode (scan is used if no argument provided)
python run.py

# Or explicitly specify scan mode
python run.py scan
```

**Configuration:**
- Cycles loaded from `config.json`: repository default is 20 fixed 3-leg cycles (A → B → C → A format)
- Tokens loaded from `config.json`: repository default is 6 tokens (SOL, USDC, JUP, BONK, WIF, RAY)
- Requests per pass: 20 cycles × 3 legs = 60 quote requests
- Rate-limited to 60 requests/minute (configurable via `QUOTE_DELAY_SECONDS`)
- Execution time: ~60 seconds per full pass + overhead (with `QUOTE_DELAY_SECONDS=1.0`)

**Output** (if available):
- List of found opportunities
- Profit in bps and USD
- Exchange cycles

### Simulate Mode

Simulation mode uses Solana RPC `simulateTransaction` to test transaction execution without sending real transactions.
Intended for research and testing purposes:

```bash
# Make sure WALLET_PRIVATE_KEY is set in .env
python run.py simulate
```

**Implementation:** Uses managed inline arbitrage loop with full 2-swap atomic execution. The bot runs in controlled iterations, allowing periodic balance/price refresh and proper error handling:

1. **Main loop** (`run_nonstop`): Iterates continuously, refreshing balances and SOL price on schedule
2. **Arbitrage iteration** (`inline_arbitrage_one_iteration`): One pass through all execution plans:
   - Gets quotes for both legs with enforced 1-hop (rate-limited: 1 req/sec)
   - Validates cross-AMM requirement (DEX1 ≠ DEX2) - **unified invariant across all paths**
   - Gets swap-instructions in burst mode (no rate limit for candidate-phase)
   - Builds atomic VersionedTransaction v0 for full 2-swap execution (already signed)
   - Simulates inline (no batches, no queues)
   - Creates `PreparedBundle` with fully signed VT ready for immediate live execution
   - Logs successful simulations
3. **Periodic maintenance**:
   - Summary logs every 60 seconds (configurable via `INLINE_SUMMARY_EVERY_SEC`)
   - Negative cache cleanup every 60 seconds (configurable via `NEGATIVE_CACHE_CLEANUP_EVERY_SEC`)
   - Idle sleep when no fundable plans (`LOOP_IDLE_SLEEP_SEC`)
   - Exponential backoff on anomalies (`FAIL_BACKOFF_BASE_SEC`, `FAIL_BACKOFF_MAX_SEC`)

**Proof→Action guarantee (live mode):**
- `PreparedBundle` contains the exact signed VT that was successfully simulated
- Live execution uses the bundle VT directly (no rebuild)
- **Expiry rebuild exception**: Only allowed when `blocks_remaining <= EXPIRY_REBUILD_HEADROOM_BLOCKS` (default: 150 blocks ~30s)
- This ensures we execute exactly what we simulated (except for expiry protection)

**Logging improvements:**
- Reduced spam: balance updates and swap-instructions logs moved to DEBUG level
- Aggregated summary: periodic statistics on candidates, successes, errors, and skip reasons
- Expected vs unexpected errors: timeout/429 errors logged without traceback

**Clear loop semantics:**
- `had_fundable_plans`: At least one plan with balance > 0
- `did_any_quote_call`: At least one Jupiter quote was actually called
- Idle logic: No fundable plans → long sleep; Fundable but no quotes → backoff (anomaly); Quotes called → short sleep

Required:
- Configured `WALLET_PRIVATE_KEY` in .env
- Balance on wallet (for simulation)

### Live Mode

⚠️ **WARNING**: Live mode sends real transactions to the network!

⚠️ **CRITICAL LIMITATION**: Live mode is **NOT full-cycle arbitrage**. Current implementation executes only the first leg of the cycle as a proxy. This is because:
- Jupiter API doesn't support multi-leg swaps directly
- Atomic multi-leg transactions are not yet implemented
- The bot simulates/executes only the first swap (A → B) of the cycle (A → B → C → A)

Live mode attempts to execute the first leg of arbitrage transactions on-chain.
This is experimental and not guaranteed to function due to aggregator API limitations.

```bash
# Make sure all settings are verified!
python run.py live
```

Required:
- Configured `WALLET_PRIVATE_KEY` in .env
- SOL on wallet for trading
- Thorough verification of all limits

### Diagnostic Mode

Runs a single direct quote request to verify that the aggregator API
can build swap routes, then exits immediately.

```bash
# Enable diagnostic behavior via env
DIAGNOSTIC_MODE=true python run.py scan
```

## Risk & Capital Management

The bot includes a comprehensive risk management system:

### Automatic Checks

1. **Position size limit**
   - Percentage of balance (`MAX_POSITION_SIZE_PERCENT`)
   - Absolute ceiling (`MAX_POSITION_SIZE_ABSOLUTE`)

2. **Minimum profit**
   - PRIMARY: In USDC (`MIN_PROFIT_USDC`) - always enforced
   - SECONDARY: In basis points (`MIN_PROFIT_BPS`) - optional filter (set to 0 to disable)

3. **Slippage control**
   - Maximum slippage (`MAX_SLIPPAGE_BPS`)
   - Bot does not automatically increase slippage

4. **Active positions**
   - Maximum simultaneous positions (`MAX_ACTIVE_POSITIONS`)
   - Tracking of locked balance

5. **Simulation**
   - Simulation support before execution
   - Validation of simulation results

### Balance Management

- Bot reads current balance from network
- Accounts for funds in active positions
- Never trades on 100% of balance

## Architecture

```
src/
├── main.py              # Entry point
├── jupiter_client.py     # Jupiter API client
├── solana_client.py      # Solana RPC client
├── risk_manager.py       # Risk & Capital Management
├── arbitrage_finder.py   # Arbitrage opportunity finder
└── trader.py             # Trading orchestrator
```

## How It Works

### Scan Mode (Read-only)

1. **Opportunity search** (`arbitrage_finder.py`)
   - Loads execution plans from `config.json` (repository default: 2 execution plans: USDC→SOL→USDC, SOL→USDC→SOL)
   - Evaluates 2-swap cross-AMM execution plans (A → B → A format)
   - Rate-limited to 60 requests/minute (configurable via `QUOTE_DELAY_SECONDS`)
   - Gets quotes via Jupiter Quote API with `onlyDirectRoutes=True` (enforced 1-hop per leg)
   - Hard 1-hop enforcement: validates `routePlan` contains exactly 1 hop matching leg mints
   - Hard liquidity skip: rejects plans with >5% price impact (anomalously weak liquidity)
   - Calculates profit accounting for fees and slippage
   - Filters by PRIMARY: `MIN_PROFIT_USDC` (always applied) and SECONDARY: `MIN_PROFIT_BPS` (optional, set to 0 to disable)
   - DEX-pair identification: extracts DEX from each quote and identifies plan by (cycle_mints, dex1, dex2, direction)
   - **Hard gate**: Rejects plans where DEX1 == DEX2 or DEX1/DEX2 == "Unknown" (cross-AMM invariant - unified across scan/stream/recheck/inline)

### Simulate/Live Modes (Managed Inline Arbitrage Loop)

1. **Main loop** (`main.py::run_nonstop`)
   - Controlled iterations (not infinite loop)
   - Periodic balance refresh (SOL every `BALANCE_REFRESH_SOL_EVERY_SEC`, USDC every `BALANCE_REFRESH_USDC_EVERY_SEC`)
   - Periodic SOL price refresh (every `SOL_PRICE_REFRESH_EVERY_SEC`)
   - Idle sleep when no work (`LOOP_IDLE_SLEEP_SEC`)
   - Exponential backoff on errors (`FAIL_BACKOFF_BASE_SEC`, `FAIL_BACKOFF_MAX_SEC`)
   - Periodic summary logs (`INLINE_SUMMARY_EVERY_SEC`)
   - Periodic negative cache cleanup (`NEGATIVE_CACHE_CLEANUP_EVERY_SEC`)

2. **Arbitrage iteration** (`arbitrage_finder.py::inline_arbitrage_one_iteration`)
   - One pass through all execution plans (returns to main loop)
   - For each execution plan:
     - Quote leg1 with `onlyDirectRoutes=True` → enforce 1-hop check → extract DEX1 (rate-limited: 1 req/sec)
     - Quote leg2 with `onlyDirectRoutes=True` → enforce 1-hop check → extract DEX2 (rate-limited: 1 req/sec)
     - **Hard gate**: Reject if DEX1 == DEX2 or DEX1/DEX2 == "Unknown" (cross-AMM invariant - unified across all paths)
     - Edge-gate: profit check (MIN_PROFIT_USDC, MIN_PROFIT_BPS)
     - Get swap-instructions for both legs **in burst mode** (no rate limit for candidate-phase)
     - Build route_signature (cycle_mints, legs_count=2, useSharedAccounts=False, program_ids_fingerprint)
     - Negative-cache check (atomic_size_overflow) → SKIP if cached
     - Build atomic VersionedTransaction v0 (already signed)
     - If size overflow → cache route (TTL 600s) → SKIP
     - Simulate inline (no delays, no batches)
     - On success → create `PreparedBundle` (with signed VT) → call callback
   - Returns statistics: candidates, successes, skips (by reason), errors, had_fundable_plans, did_any_quote_call, did_candidate_flow
   - Rate limiting: quotes are rate-limited (1 req/sec), swap-instructions use burst mode (no limit)
   - Idle logic: No fundable plans → long sleep; Fundable but no quotes → backoff (anomaly); Quotes called → short sleep

3. **Live execution** (`trader.py::execute_prepared_bundle`)
   - Uses `PreparedBundle` with exact signed VT that was simulated (proof→action guarantee)
   - Expiry check: Rebuild ONLY if `blocks_remaining <= EXPIRY_REBUILD_HEADROOM_BLOCKS` (default: 150 blocks ~30s)
   - Otherwise: Use bundle VT directly (no rebuild, no re-fetching swap-instructions)
   - Mandatory simulation of bundle VT (same VT, not new)
   - Send transaction

2. **Risk check** (`risk_manager.py`)
   - Checks all limits
   - Validates position size
   - Controls active positions

3. **Simulation** (`solana_client.py`)
   - Builds atomic VersionedTransaction v0 for full 2-swap execution plan
   - Uses Address Lookup Tables (ALTs) for transaction size optimization
   - Simulates transaction via RPC `simulateTransaction` before sending
   - Validates results (full 2-swap atomic execution)
   - **Negative cache** — TTL-based cache for size overflow and runtime 6024 errors
   - **useSharedAccounts=False** — hard requirement (no retry with True)

4. **Execution** (`trader.py`)
   - Builds atomic VersionedTransaction v0 for full 2-swap execution plan
   - Uses Address Lookup Tables (ALTs) for transaction size optimization
   - **Mandatory simulation** before execution
   - Sends atomic transaction to network (all-or-nothing execution)
   - Waits for confirmation
   - **useSharedAccounts=False** — hard requirement (no retry with True)

## Important Notes

### Security

- ⚠️ **Never publish your private key**
- ⚠️ **Start with scan/simulate mode**
- ⚠️ **Verify all limits before live mode**
- ⚠️ **Use test wallet for experiments**

### Limitations

- ⚠️ **Research prototype** — not production-ready, use at your own risk
- ⚠️ **2-swap only** — currently supports 2-swap cross-AMM arbitrage (not 3-leg cycles)
- Jupiter API rate limit: 60 requests/minute (configurable via `QUOTE_DELAY_SECONDS`)
- RPC latency affects arbitrage success
- MEV bots may outpace your transactions

### Performance

- Python speed is not a bottleneck
- Main limitations: RPC latency, Jupiter API, slot timing
- Priority fee helps speed up transactions

## Troubleshooting

### Error "No wallet private key"
- Make sure `WALLET_PRIVATE_KEY` is set in .env
- Wallet is not required for `scan` mode

### Error "Insufficient balance"
- Check wallet balance
- Reduce `MAX_POSITION_SIZE_ABSOLUTE`

### Error "Simulation failed"
- Check that wallet has sufficient balance
- Make sure RPC is accessible

### No opportunities found
- This is normal behavior. Public aggregator APIs may return no routes even for liquid pairs.
- Try decreasing MIN_PROFIT_USDC (primary filter) or setting MIN_PROFIT_BPS=0 to disable bps filter.

## License

This project is created for educational purposes. Use at your own risk.

## Additional Resources

- [Jupiter API Documentation](https://docs.jup.ag/)
- [Solana Documentation](https://docs.solana.com/)
- [Pirate Bootcamp Quest 5](https://github.com/solana-developers/pirate-bootcamp/tree/main/quest-5) (outdated example)
