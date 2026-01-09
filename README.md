# Solana Arbitrage Bot

Off-chain arbitrage research bot for Solana using the Jupiter Aggregator API for studying and evaluating arbitrage opportunities.

## Project Status

This project is an experimental / research prototype.

- Scan mode: experimental
- Simulate mode: experimental
- Live trading: disabled by default

Due to limitations of public aggregator APIs, automated arbitrage
execution is not guaranteed.

This repository focuses on architecture, diagnostics, and research.


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

Main parameters:

```env
# Solana RPC
RPC_URL=https://api.mainnet-beta.solana.com

# Wallet private key (base58)
WALLET_PRIVATE_KEY=your_private_key_here

# Operation mode: scan, simulate, live
MODE=scan

# Risk Management
MAX_POSITION_SIZE_PERCENT=10.0      # Maximum position size (% of balance)
MAX_POSITION_SIZE_ABSOLUTE=1.0      # Maximum position size (SOL)
MIN_PROFIT_BPS=50                   # Minimum profit (basis points)
MAX_SLIPPAGE_BPS=50                 # Maximum slippage (basis points)
MAX_ACTIVE_POSITIONS=1              # Maximum active positions

# Priority Fee
PRIORITY_FEE_LAMPORTS=10000
USE_JITO=false
```

### config.json

Additional settings:

```json
{
  "tokens": {
    "SOL": "So11111111111111111111111111111111111111112",
    "USDC": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "USDT": "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
  },
  "arbitrage": {
    "min_profit_usd": 0.1,
    "max_cycle_length": 3,
    "quote_timeout": 5.0
  }
}
```

## Usage

### SCAN Mode (read-only)

Search for arbitrage opportunities without execution:

```bash
# Option 1: via run.py
MODE=scan python run.py

# Option 2: via module
MODE=scan python -m src.main
```

Output:
- List of found opportunities
- Profit in bps and USD
- Exchange cycles (A → B → A)

### SIMULATE Mode (simulation)

Search for opportunities and simulate transactions without sending:

```bash
# Make sure WALLET_PRIVATE_KEY is set in .env
MODE=simulate python run.py
```

Required:
- Configured `WALLET_PRIVATE_KEY` in .env
- Balance on wallet (for simulation)

### LIVE Mode (real execution)

⚠️ **WARNING**: Live mode sends real transactions to the network!

```bash
# Make sure all settings are verified!
MODE=live python run.py
```

Required:
- Configured `WALLET_PRIVATE_KEY` in .env
- SOL on wallet for trading
- Thorough verification of all limits

## Risk & Capital Management

The bot includes a comprehensive risk management system:

### Automatic Checks

1. **Position size limit**
   - Percentage of balance (`MAX_POSITION_SIZE_PERCENT`)
   - Absolute ceiling (`MAX_POSITION_SIZE_ABSOLUTE`)

2. **Minimum profit**
   - In basis points (`MIN_PROFIT_BPS`)
   - In USD (`min_profit_usd` in config.json)

3. **Slippage control**
   - Maximum slippage (`MAX_SLIPPAGE_BPS`)
   - Bot does not automatically increase slippage

4. **Active positions**
   - Maximum simultaneous positions (`MAX_ACTIVE_POSITIONS`)
   - Tracking of locked balance

5. **Simulation**
   - Mandatory simulation before execution
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
   - Generates exchange cycles (A → B → A, A → B → C → A)
   - Gets quotes via Jupiter Quote API
   - Calculates profit accounting for fees and slippage

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

- Bot searches for simple cycles (2-3 leg)
- Jupiter API has rate limits
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
- This is normal - arbitrage opportunities are rare
- Try increasing `MIN_PROFIT_BPS` or decreasing `min_profit_usd`

## License

This project is created for educational purposes. Use at your own risk.

## Additional Resources

- [Jupiter API Documentation](https://docs.jup.ag/)
- [Solana Documentation](https://docs.solana.com/)
- [Pirate Bootcamp Quest 5](https://github.com/solana-developers/pirate-bootcamp/tree/main/quest-5) (outdated example)
