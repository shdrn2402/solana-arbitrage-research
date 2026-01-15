# Solana Arbitrage Bot (v2.0.0)

Off-chain arbitrage research bot for Solana using the Jupiter Aggregator API for studying and evaluating arbitrage opportunities.

## Project Status

This project is an experimental / research prototype developed iteratively.

### ✅ Stage 1 — Scan (completed)
- **Stable Jupiter API integration** — reliable quote retrieval via public API
- **Arbitrage path discovery** — cycles and tokens loaded from config.json
- **Configurable token universe** — adjustable tokens and cycle depth via config.json
- **Quota-optimized scanning** — rate-limited execution (60 requests/minute) with configurable delays
- **Repository default: 20 fixed 3-leg cycles** — loaded from config.json (14 USDC-based, 6 SOL-based)
- **Repository default tokens: SOL, USDC, JUP, BONK, WIF, RAY** — 6 tokens configured in config.json
- **Read-only mode** — no on-chain execution or fund usage

### 🛠️ Stage 2 — Simulation (in progress)
- **On-chain transaction simulation** via RPC `simulateTransaction` (no real transactions sent)
- **⚠️ First-leg proxy only** — current implementation simulates only the first leg of the cycle, not full multi-leg arbitrage
- **Priority fee & latency experiments**
- **Execution feasibility analysis**

### 🧪 Stage 3 — Live Execution (experimental)
- **Real transaction submission** to Solana network
- **⚠️ First-leg proxy only** — current implementation executes only the first leg of the cycle, not full multi-leg arbitrage
- **⚠️ Not full-cycle arbitrage** — atomic multi-leg transactions not yet implemented
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
# Solana RPC
RPC_URL=https://api.mainnet-beta.solana.com

# Operation Mode
# Note: Mode is now specified via command line argument (default: scan)
# Usage: python run.py [scan|simulate|live]

# Wallet private key (base58, required for simulate/live modes)
WALLET_PRIVATE_KEY=your_private_key_here

# Risk Management (all absolute limits in USDC)
MAX_POSITION_SIZE_PERCENT=10.0  # Maximum position size (% of balance)
MAX_POSITION_SIZE_ABSOLUTE=1.0  # in SOL
MIN_PROFIT_USDC=0.03  # PRIMARY: minimum profit in USDC
MIN_PROFIT_BPS=0  # SECONDARY: optional filter (set to 0 to disable)
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

**⚠️ Important:** Current implementation simulates only the first leg of the cycle as a proxy, not full multi-leg arbitrage. This is because Jupiter API doesn't support multi-leg swaps directly, and building atomic multi-leg transactions is not yet implemented.

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

1. **Opportunity search** (`arbitrage_finder.py`)
   - Loads cycles and tokens from `config.json` (repository default: 20 fixed 3-leg cycles, 6 tokens)
   - Evaluates 3-leg exchange cycles (A → B → C → A format)
   - Rate-limited to 60 requests/minute (configurable via `QUOTE_DELAY_SECONDS`)
   - Gets quotes via Jupiter Quote API (burst mode: all legs of a cycle queried quickly, then delay)
   - Calculates profit accounting for fees and slippage
   - Filters by PRIMARY: `MIN_PROFIT_USDC` (always applied) and SECONDARY: `MIN_PROFIT_BPS` (optional, set to 0 to disable)
   - Execution time: ~60 seconds per full pass + overhead (20 cycles × 3 requests = 60 requests with 1.0s delay)

2. **Risk check** (`risk_manager.py`)
   - Checks all limits
   - Validates position size
   - Controls active positions

3. **Simulation** (`solana_client.py`)
   - Simulates transaction via RPC `simulateTransaction` before sending
   - ⚠️ **Current limitation**: Only simulates first leg of cycle (first-leg proxy)
   - Validates results (first leg output only)

4. **Execution** (`trader.py`)
   - Builds transaction via Jupiter Swap API (first leg only)
   - ⚠️ **Current limitation**: Only executes first leg of cycle, not full multi-leg arbitrage
   - Sends to network
   - Waits for confirmation

## Important Notes

### Security

- ⚠️ **Never publish your private key**
- ⚠️ **Start with scan/simulate mode**
- ⚠️ **Verify all limits before live mode**
- ⚠️ **Use test wallet for experiments**

### Limitations

- **⚠️ Simulation/live are not full-cycle arbitrage yet**: Current implementation is effectively first-leg proxy. The bot simulates/executes only the first swap (A → B) of the cycle (A → B → C → A), not the full multi-leg arbitrage. This is because Jupiter API doesn't support multi-leg swaps directly, and atomic multi-leg transactions are not yet implemented.
- Cycles and tokens are loaded from `config.json` (repository default: 20 fixed 3-leg cycles, 6 tokens: SOL, USDC, JUP, BONK, WIF, RAY)
- Jupiter API rate limit: 60 requests/minute (configurable via `QUOTE_DELAY_SECONDS`)
- Scan execution time: ~60 seconds per full pass + overhead (20 cycles × 3 requests = 60 requests with 1.0s delay)
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
