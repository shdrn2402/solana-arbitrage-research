# Changelog

All notable changes to this project will be documented in this file.

This project is an experimental / research prototype. “Live” execution is **not production-ready**.

## [2.0.0] — 2026-01-15

### Added
- **Operation modes via CLI**: `python run.py [scan|simulate|live]` (default: `scan`).
- **Simulation mode** for on-chain feasibility testing (no real transactions).
- **Diagnostic mode** (via `.env`) to sanity-check Jupiter route capability.
- **Wallet visibility**: prints SOL balance and USDC balance at startup.
- **Readable cycle output**: prints token symbols (e.g. `SOL -> USDC -> RAY -> SOL`) instead of mint addresses.
- **Colored terminal output** (TTY-only) to highlight key values (counts, balances, profit, etc.).
- **Configurable rate limiting** via `QUOTE_DELAY_SECONDS`.
- **Configurable slippage** via `.env` (`SLIPPAGE_BPS`, `MAX_SLIPPAGE_BPS`) with validation/capping.
- **Priority fee config** (`PRIORITY_FEE_LAMPORTS`) and placeholder flag for **Jito** (`USE_JITO`).

### Changed
- **Risk model unified to USDC** for absolute limits and profit thresholds:
  - Primary profitability filter is **absolute profit in USDC** (`MIN_PROFIT_USDC`).
  - Secondary optional filter is **bps** (`MIN_PROFIT_BPS`, can be disabled with `0`).
- **Cycles moved to `config.json`** (20 fixed 3-leg cycles; token universe from config).
- **Jupiter API integration updated** to current working endpoints (`/swap/v1/...`) and improved error handling.
- **Startup flow** loads `.env` early, then configures logging from `LOG_LEVEL`.
- Suppressed noisy `httpx` request logs (keeps warnings/errors visible).

### Fixed
- Multiple stability and safety fixes around configuration consistency and slippage validation.
- Safer execution guards:
  - strict mode check (only `live` can send transactions),
  - single-trade protection flag,
  - mandatory simulation gate before sending.
- Improved logging/diagnostics for rejected opportunities and simulation failures.

### Known limitations (important)
- Current “live” execution is **not a full multi-leg arbitrage** yet:
  - transaction building/simulation is effectively **first-leg only** (proxy),
  - full-cycle atomic execution must be implemented in the upcoming live branch before real trading.

## [Unreleased]
- Live-ready full-cycle (atomic) execution, robust PnL verification, and optional Jito integration.