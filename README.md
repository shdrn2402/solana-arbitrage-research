# Solana Arbitrage Bot

Off-chain arbitrage research bot for Solana using the Jupiter Aggregator API for studying and evaluating arbitrage opportunities.

## Project Status

This project is an experimental / research prototype developed iteratively.

### ✅ Stage 1 — Scan (completed)
- **Stable Jupiter API integration** — reliable quote retrieval via public API
- **Arbitrage path discovery** — deterministic cycle generation and evaluation
- **Configurable token universe** — adjustable tokens and cycle depth
- **Quota-optimized scanning** — rate-limited execution (60 requests/minute) with configurable delays
- **12 predefined cycles** — doubled coverage while respecting API quotas (~40-45 seconds per scan)
- **Read-only mode** — no on-chain execution or fund usage

### 🛠️ Stage 2 — Simulation (in progress)
- **On-chain transaction simulation** before execution
- **Full cycle validation** using simulated swaps
- **Priority fee & latency experiments**
- **Execution feasibility analysis**

### 🧪 Stage 3 — Live Execution (planned / experimental)
- **Real transaction submission**
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
MODE=scan  # scan, simulate, or live

# Wallet private key (base58, required for simulate/live modes)
WALLET_PRIVATE_KEY=your_private_key_here

# Risk Management (all absolute limits in USDC)
MAX_POSITION_SIZE_PERCENT=10.0
MAX_POSITION_SIZE_ABSOLUTE=1.0  # in SOL
MIN_PROFIT_USDC=0.1  # PRIMARY: minimum profit in USDC
MIN_PROFIT_BPS=50  # SECONDARY: optional filter (set to 0 to disable)
MAX_SLIPPAGE_BPS=50  # Maximum allowed slippage (risk limit)
SLIPPAGE_BPS=50  # Actual slippage used in Jupiter API requests
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
    "BONK": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
  },
  "arbitrage": {
    "min_profit_usdc": 0.1,
    "max_cycle_length": 4,
    "quote_timeout": 5.0
  }
}
```

## Usage

### Scan Mode

Read-only mode for analyzing potential arbitrage paths without executing transactions:

```bash
python run.py scan
```

**Configuration:**
- 12 predefined 3-leg cycles (A → B → C → A format)
- 4 tokens: SOL, USDC, JUP, BONK
- Rate-limited to 60 requests/minute (configurable via `QUOTE_DELAY_SECONDS`)
- Execution time: ~40-45 seconds per scan

**Output** (if available):
- List of found opportunities
- Profit in bps and USD
- Exchange cycles

### Simulate Mode

Simulation mode attempts to execute the trading logic without sending real transactions.
Intended for research and testing purposes:

```bash
# Make sure WALLET_PRIVATE_KEY is set in .env
python run.py simulate
```

Required:
- Configured `WALLET_PRIVATE_KEY` in .env
- Balance on wallet (for simulation)

### Live Mode

⚠️ **WARNING**: Live mode sends real transactions to the network!

Live mode attempts to execute arbitrage transactions on-chain.
Disabled by default and not guaranteed to function due to aggregator API limitations.

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
DIAGNOSTIC_MODE=true python run.py
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
   - Evaluates 12 predefined 3-leg exchange cycles (A → B → C → A format)
   - Uses 4 tokens: SOL, USDC, JUP, BONK
   - Rate-limited to 60 requests/minute (configurable via `QUOTE_DELAY_SECONDS`)
   - Gets quotes via Jupiter Quote API
   - Calculates profit accounting for fees and slippage
   - The project uses 12 predefined 3-leg cycles for quota-optimized scanning
   - Execution time: ~40-45 seconds per scan (rate-limited at 60 req/min)

2. **Risk check** (`risk_manager.py`)
   - Checks all limits
   - Validates position size
   - Controls active positions

3. **Simulation** (`solana_client.py`)
   - Simulates transaction before sending
   - Validates results

4. **Execution** (`trader.py`)
   - Builds transaction via Jupiter Swap API
   - Sends to network
   - Waits for confirmation

## Important Notes

### Security

- ⚠️ **Never publish your private key**
- ⚠️ **Start with scan/simulate mode**
- ⚠️ **Verify all limits before live mode**
- ⚠️ **Use test wallet for experiments**

### Limitations

- Bot searches for 3-leg cycles (A → B → C → A format) using 4 tokens: SOL, USDC, JUP, BONK
- Jupiter API rate limit: 60 requests/minute (configurable via `QUOTE_DELAY_SECONDS`)
- Scan execution time: ~40-45 seconds (12 cycles, 36 requests total)
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
- Try increasing `MIN_PROFIT_BPS` or decreasing `min_profit_usdc`

## License

This project is created for educational purposes. Use at your own risk.

## Additional Resources

- [Jupiter API Documentation](https://docs.jup.ag/)
- [Solana Documentation](https://docs.solana.com/)
- [Pirate Bootcamp Quest 5](https://github.com/solana-developers/pirate-bootcamp/tree/main/quest-5) (outdated example)
