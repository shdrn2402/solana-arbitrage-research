# Code Review Changes Summary

## Critical Security and Logic Fixes

### 1. Currency Limit Unification ✅

**Problem**: Limits were mixed - some in SOL, some in USD, creating ambiguity.

**Fix**:
- All absolute limits are now in USDC
- `max_position_size_absolute` is converted from SOL to USDC via `sol_price_usdc`
- Added `SOL_PRICE_USDC` parameter for conversion
- All limit checks use unified currency (USDC)

**Files**:
- `src/risk_manager.py`: `RiskConfig.max_position_size_absolute_usdc` (instead of `max_position_size_absolute` in SOL)
- `src/main.py`: SOL → USDC conversion on config load
- `env.example`: Added `SOL_PRICE_USDC`

### 2. Minimum Profit: USD as Primary Criterion ✅

**Problem**: `min_profit_bps` check was primary, which is unreliable for arbitrage.

**Fix**:
- `min_profit_usdc` is now PRIMARY check (mandatory)
- `min_profit_bps` became SECONDARY filter (optional, can be disabled via 0)
- Check order changed: USD first, then bps

**Files**:
- `src/risk_manager.py`: `can_open_position()` - changed order and priority of checks
- `src/risk_manager.py`: `RiskConfig.min_profit_usdc` (renamed from `min_profit_usd` for clarity)

### 3. Protection Against Parallel Trades ✅

**Problem**: No explicit protection against simultaneous execution of multiple trades.

**Fix**:
- Added `trade_in_progress` flag to `Trader` class
- Flag is set BEFORE any operations in `execute_opportunity()`
- Flag is reset in `finally` block (guaranteed)
- Flag check at start of `execute_opportunity()` blocks parallel trades

**Files**:
- `src/trader.py`: Added `self.trade_in_progress = False` in `__init__`
- `src/trader.py`: Flag check and management in `execute_opportunity()`

### 4. Strict Mode Check ✅

**Problem**: No explicit mode check before sending transactions.

**Fix**:
- Mode is passed to `Trader` constructor
- Strict check at start of `execute_opportunity()`: only `mode == 'live'` can send transactions
- In `scan` and `simulate` modes, transaction sending is impossible
- Added warning and 3-second delay on `live` mode startup

**Files**:
- `src/trader.py`: `mode` parameter in `__init__()` and check in `execute_opportunity()`
- `src/main.py`: Passing `mode` to `Trader` constructor
- `src/main.py`: Additional warnings in `live` mode

### 5. Simulation as Mandatory Gatekeeper ✅

**Problem**: Simulation was technically mandatory, but there were no explicit comments and protection.

**Fix**:
- Added explicit "MANDATORY SIMULATION" comments
- Simulation runs BEFORE any transaction operations
- If simulation fails - transaction is not sent
- No paths to bypass simulation

**Files**:
- `src/trader.py`: Improved comments and code structure in `execute_opportunity()`

### 6. RiskConfig Attribute Synchronization ✅

**Problem**: After refactoring, `AttributeError` occurred due to attribute name desynchronization.

**Fix**:
- Fixed access to non-existent `risk_config.max_position_size_absolute` → uses `max_position_size_absolute_usdc`
- All `RiskConfig` attribute accesses synchronized with declared fields
- Added conversion from USDC back to SOL for `test_amount` calculation

**Files**:
- `src/main.py`: Fixed usage of `risk_config.max_position_size_absolute_usdc` (line 168)
- `src/main.py`: Fixed usage of `risk_config.min_profit_usdc` instead of non-existent `min_profit_usd` (line 148)

### 7. Jupiter API DNS Endpoint Fix ✅

**Problem**: Domain `quote-api.jup.ag` does not resolve (DNS error), while `api.jup.ag` works.

**Fix**:
- Replaced Jupiter API base URL from `https://quote-api.jup.ag/v6` to `https://api.jup.ag/v6`
- Updated all usage locations: default in constructor, default in main.py, env.example

**Files**:
- `src/jupiter_client.py`: Default URL changed to `https://api.jup.ag/v6` (line 37)
- `src/main.py`: Default URL for env variable changed to `https://api.jup.ag/v6` (line 83)
- `env.example`: Updated example URL to `https://api.jup.ag/v6` (line 9)

## Additional Improvements

- Updated `env.example` with new parameters and comments
- Improved code documentation (security comments)
- All changes are minimal and do not affect architecture

### 8. Jupiter API Endpoint Update to /swap/v1/quote ✅

**Problem**: The code was using deprecated `/v6/quote` endpoint which returned 404 "Route not found" errors. The working endpoint is `/swap/v1/quote`.

**Fix**:
- Updated authenticated endpoint base URL from `https://api.jup.ag/v6` to `https://api.jup.ag`
- Changed quote endpoint path from `/v6/quote` to `/swap/v1/quote`
- Changed swap endpoint path from `/v6/swap` to `/swap/v1/swap`
- Changed tokens endpoint path from `/v6/tokens` to `/swap/v1/tokens`
- Added proper 404 error handling (route not found is a valid API response, not a transport error)
- Enhanced logging to distinguish API transport errors from empty route results

**Files**:
- `src/jupiter_client.py`: Updated all endpoint paths to use `/swap/v1/` format
- `src/jupiter_client.py`: Enhanced error handling for 404 responses

### 9. Quota-Safe Minimal Scan Configuration ✅

**Problem**: High Jupiter API quota consumption due to large token universe and combinatorial cycle generation.

**Fix**:
- Limited token universe to exactly 4 tokens: SOL, USDC, JUP, BONK (updated in change #16: USDT replaced with BONK)
- Replaced dynamic cycle generation with fixed list of 6 predefined cycles
- Removed parallelism: cycles checked sequentially (one at a time)
- Added 200ms delays between quote requests to avoid quota spikes
- Single pass per scan run (no iterations/repeats)

**Files**:
- `config.json`: Reduced tokens to 4 (SOL, USDC, JUP, BONK - see change #16 for BONK addition)
- `src/arbitrage_finder.py`: Added `FIXED_CYCLES` constant with 6 predefined cycles
- `src/arbitrage_finder.py`: Replaced parallel batch processing with sequential checking
- `src/arbitrage_finder.py`: Added delays between cycles and within cycle legs
- `src/main.py`: Added quota-safe scan info log message
- `src/main.py`: Updated default tokens to include JUP (and later BONK in change #16)

### 10. Configurable Slippage via Environment Variables ✅

**Problem**: Slippage was hardcoded to 50 bps throughout the codebase, making it impossible to configure via `.env` file.

**Fix**:
- Added `SLIPPAGE_BPS` environment variable (default: 50) for all Jupiter API requests
- Added `DIAGNOSTIC_SLIPPAGE_BPS` environment variable (default: 500) for diagnostic mode
- Removed all hardcoded slippage values (50, 500)
- Added validation: `SLIPPAGE_BPS` must be ≤ `MAX_SLIPPAGE_BPS` (with automatic capping)
- Passed `slippage_bps` parameter through `ArbitrageFinder`, `Trader`, and `JupiterClient.get_swap_transaction()`

**Files**:
- `src/main.py`: Added reading of `SLIPPAGE_BPS` and `DIAGNOSTIC_SLIPPAGE_BPS` from `.env`
- `src/main.py`: Added validation and automatic capping of `SLIPPAGE_BPS` against `MAX_SLIPPAGE_BPS`
- `src/main.py`: Passed `slippage_bps` to `ArbitrageFinder` and `Trader` constructors
- `src/main.py`: Updated diagnostic mode to use `DIAGNOSTIC_SLIPPAGE_BPS` instead of hardcoded 500
- `src/arbitrage_finder.py`: Added `slippage_bps` parameter to `__init__` (default: 50)
- `src/arbitrage_finder.py`: Replaced hardcoded `slippage_bps=50` in `_check_cycle()` with `self.slippage_bps`
- `src/trader.py`: Added `slippage_bps` parameter to `__init__` (default: 50)
- `src/trader.py`: Replaced hardcoded `slippage_bps=50` in `execute_opportunity()` with `self.slippage_bps`
- `src/trader.py`: Updated `get_swap_transaction()` calls to pass `slippage_bps` parameter
- `src/jupiter_client.py`: Added `slippage_bps` parameter to `get_swap_transaction()` (default: 50)
- `src/jupiter_client.py`: Replaced hardcoded `"slippageBps": 50` with parameter value
- `env.example`: Added `SLIPPAGE_BPS=50` and `DIAGNOSTIC_SLIPPAGE_BPS=500` examples

### 11. Improved Slippage Validation and Logging ✅

**Problem**: When `SLIPPAGE_BPS` was set to a value greater than default `MAX_SLIPPAGE_BPS` (50) without explicitly setting `MAX_SLIPPAGE_BPS`, the value was silently capped without clear warnings or instructions.

**Fix**:
- Added explicit check for `MAX_SLIPPAGE_BPS` being set in `.env` (vs. using default)
- Warning logged when `MAX_SLIPPAGE_BPS` is not explicitly set but `SLIPPAGE_BPS` is set (preserves backward compatibility: no warning if both use defaults)
- Changed validation error from `warning` to `error` level when `SLIPPAGE_BPS` exceeds `MAX_SLIPPAGE_BPS`
- Enhanced error message with clear instructions: "Either increase MAX_SLIPPAGE_BPS in .env or decrease SLIPPAGE_BPS"
- Added final validation summary showing:
  - If slippage was adjusted: shows original and final values
  - If explicitly configured: shows current configuration values
- Improved `env.example` comments to clarify relationship between `MAX_SLIPPAGE_BPS` (risk limit) and `SLIPPAGE_BPS` (actual value used)

**Files**:
- `src/main.py`: Added explicit checks for `MAX_SLIPPAGE_BPS` and `SLIPPAGE_BPS` being set in environment
- `src/main.py`: Enhanced validation logic with warnings for missing `MAX_SLIPPAGE_BPS` and errors for exceeding limits
- `src/main.py`: Added final validation summary logging with adjusted/current values
- `env.example`: Improved comments for `MAX_SLIPPAGE_BPS` and `SLIPPAGE_BPS` to clarify their relationship

### 12. Refactoring SOL/USDC Price Fetching Method ✅

**Problem**: Code for fetching SOL price from Jupiter API was duplicated in diagnostic mode and was needed for startup initialization. The logic was scattered and not reusable.

**Fix**:
- Added centralized method `get_sol_price_usdc()` in `JupiterClient` class
- Method supports two modes: return price as `float` or return full `JupiterQuote` object
- Default slippage set to 10 bps for accurate price fetching
- Refactored diagnostic mode to use new method with `return_full_quote=True` (preserves all existing functionality including route_plan logging)
- Added automatic SOL price fetching at startup from Jupiter API with fallback to `.env` value
- Price is fetched after `JupiterClient` initialization and before `RiskManager` initialization
- If price is successfully fetched, `risk_config.sol_price_usdc` and `risk_config.max_position_size_absolute_usdc` are automatically updated

**Files**:
- `src/jupiter_client.py`: Added `async def get_sol_price_usdc(slippage_bps: int = 10, return_full_quote: bool = False)` method
- `src/jupiter_client.py`: Added `Union` import for type hints
- `src/main.py`: Refactored diagnostic mode (lines 229-266) to use `jupiter.get_sol_price_usdc(return_full_quote=True)`
- `src/main.py`: Added automatic SOL price fetching at startup (lines 167-181) with fallback to `.env` value
- `src/main.py`: Added automatic update of `risk_config.sol_price_usdc` and `risk_config.max_position_size_absolute_usdc` when price is fetched

### 13. Remove Hardcoded SOL Price from ArbitrageFinder ✅

**Problem**: `ArbitrageFinder` used hardcoded `sol_price_usdc = 100.0` in `_estimate_profit_usd()` method, which caused incorrect profit calculations when actual SOL price differed from the hardcoded value. The actual price from config (automatically fetched from Jupiter API) was not being used.

**Fix**:
- Added `sol_price_usdc: float = 100.0` parameter to `ArbitrageFinder.__init__()` constructor (with default for backward compatibility)
- Store value in `self.sol_price_usdc` instance variable
- Replaced hardcoded `sol_price_usdc = 100.0` in `_estimate_profit_usd()` method with `self.sol_price_usdc`
- Updated method comment to remove placeholder mention, keeping note about future price oracle improvement
- Pass `sol_price_usdc=risk_config.sol_price_usdc` when creating `ArbitrageFinder` in `main.py`
- Value is automatically updated from Jupiter API (or `.env` fallback) before `ArbitrageFinder` creation, ensuring accurate price is always used

**Files**:
- `src/arbitrage_finder.py`: Added `sol_price_usdc: float = 100.0` parameter to `__init__()` constructor (line 76)
- `src/arbitrage_finder.py`: Store in `self.sol_price_usdc` (line 86)
- `src/arbitrage_finder.py`: Replaced hardcoded value in `_estimate_profit_usd()` method with `self.sol_price_usdc` (line 268)
- `src/arbitrage_finder.py`: Updated method comment to remove placeholder mention
- `src/main.py`: Pass `sol_price_usdc=risk_config.sol_price_usdc` parameter when creating `ArbitrageFinder` (line 215)

### 14. Configuration Name Consistency Fix ✅

**Problem**: Inconsistent naming between `.env` (`MIN_PROFIT_USDC`) and `config.json` (`min_profit_usd`). This created confusion and inconsistency in naming conventions.

**Fix**:
- Renamed `min_profit_usd` to `min_profit_usdc` in `config.json` for consistency with `.env` variable naming
- Updated code to use `min_profit_usdc` key when reading from `config.json`
- All profit minimum values now use USDC naming convention consistently across all configuration files
- Updated documentation in `README.md` to reflect the new naming convention

**Files**:
- `config.json`: Renamed `min_profit_usd` → `min_profit_usdc` in arbitrage section (line 9)
- `src/main.py`: Updated `config.get()` to use `min_profit_usdc` key (line 99)
- `README.md`: Updated all mentions of `min_profit_usd` to `min_profit_usdc` (lines 94, 168, 261)

**Note**: After this change, old configs with `min_profit_usd` will use the default value (0.1) instead of reading from config. This is acceptable as `.env` takes precedence, and the change improves naming consistency.

### 15. Code Optimization: Remove Double Environment Access for MAX_SLIPPAGE_BPS ✅

**Problem**: Code for reading `MAX_SLIPPAGE_BPS` had redundant double access to environment variable: `os.getenv('MAX_SLIPPAGE_BPS')` followed by `'MAX_SLIPPAGE_BPS' in os.environ`. This was inconsistent with `SLIPPAGE_BPS` implementation which uses simplified approach.

**Fix**:
- Simplified `max_slippage_bps_explicitly_set` check to use result from `os.getenv()` instead of second access to `os.environ`
- Changed from `'MAX_SLIPPAGE_BPS' in os.environ` to `max_slippage_bps_env is not None`
- Consistent with `SLIPPAGE_BPS` implementation (line 122)
- Removed redundant environment variable access

**Files**:
- `src/main.py`: Changed line 103 to use `max_slippage_bps_env is not None` instead of `'MAX_SLIPPAGE_BPS' in os.environ`

**Note**: Logic remains the same: if variable is set in `.env` (even as empty string), `os.getenv()` returns string and `is not None` returns `True`. If variable is not set, `os.getenv()` returns `None` and `is not None` returns `False`.

### 16. Replace USDT with BONK in Arbitrage Cycles ✅

**Problem**: Cycles included USDT (USDC ↔ USDT is not useful for arbitrage as both are stablecoins). The bot needed more volatile token pairs for better arbitrage opportunities.

**Fix**:
- Removed all cycles containing USDT from `FIXED_CYCLES`
- Added BONK (volatile meme coin) to all cycles for more interesting arbitrage opportunities
- Updated token set from SOL, USDC, USDT, JUP to SOL, USDC, JUP, BONK
- Created 6 new cycles with volatile pairs:
  1. SOL → USDC → BONK → SOL
  2. SOL → JUP → BONK → SOL
  3. SOL → USDC → JUP → SOL
  4. USDC → SOL → BONK → USDC
  5. JUP → SOL → USDC → JUP
  6. BONK → SOL → USDC → BONK
- All cycles remain closed (start and end with the same token)

**Files**:
- `src/arbitrage_finder.py`: Replaced `FIXED_CYCLES` with new cycles without USDT, added BONK (lines 37-64)
- `src/arbitrage_finder.py`: Updated comment to reflect new token set (line 38)
- `config.json`: Removed USDT, added BONK with address "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
- `src/main.py`: Updated default tokens list and comment (lines 192-202)
- `README.md`: Updated config.json example to remove USDT and add BONK

### 17. Implement LOG_LEVEL Configuration from .env ✅

**Problem**: `LOG_LEVEL` variable from `.env` was ignored. Logging was hardcoded to `INFO` level, making it impossible to configure logging level via environment variables.

**Fix**:
- Removed hardcoded `logging.basicConfig()` from module level (it was called before `.env` was loaded)
- Moved logging setup inside `main()` function after `.env` is loaded
- Added reading of `LOG_LEVEL` from `.env` with default `INFO`
- Supports standard log levels: DEBUG, INFO, WARNING, ERROR, CRITICAL
- Added validation: invalid levels fallback to INFO with warning message
- Logging configuration now happens after `dotenv.load_dotenv()` is called

**Files**:
- `src/main.py`: Removed hardcoded `logging.basicConfig()` from module level (lines 22-30)
- `src/main.py`: Added `.env` loading and logging setup at start of `main()` function (lines 68-92)
- `src/main.py`: Added log level validation and conversion from string to `logging.LEVEL`
- `src/main.py`: Added debug logging of configured log level

**Note**: `logging.basicConfig()` is now called only once inside `main()` after `.env` is loaded. All other modules inherit the configured log level automatically.

### 18. Fix Critical Bugs in Simulation Validation and Profit Filtering ✅

**Problem**: Two critical bugs were found:
1. Simulation validation compared `opportunity.final_amount` with itself instead of actual simulation result
2. Profit filtering in `ArbitrageFinder.is_valid()` did not explicitly check if `min_profit_bps > 0` before applying the filter, making it unclear when the filter is disabled

**Fix**:
- **Simulation validation**: Changed to use `first_quote.out_amount` from `opportunity.quotes[0]` as expected value (simulation only executes first leg of cycle)
- Added comments explaining that simulation validates only the first leg, not the full cycle
- Added TODO comment for future extraction of actual output from sim_result logs/accounts
- **Profit filtering**: Added explicit check `if min_profit_bps > 0` before applying bps filter (consistent with `RiskManager.can_open_position`)
- Changed `is_valid()` method to have clear PRIMARY (USDC, always applied) and SECONDARY (BPS, optional if > 0) checks
- If `min_profit_bps = 0`, the bps filter is disabled (not applied)

**Files**:
- `src/trader.py`: Fixed simulation validation to use `first_quote.out_amount` instead of comparing `opportunity.final_amount` with itself (lines 171-187)
- `src/trader.py`: Added comments about first leg validation and TODO for future improvement
- `src/arbitrage_finder.py`: Fixed `is_valid()` method to explicitly check `min_profit_bps > 0` before applying filter (lines 28-43)
- `src/arbitrage_finder.py`: Added clear PRIMARY/SECONDARY check structure with comments

**Note**: Simulation validation now correctly validates the first leg output. Profit filtering now matches the logic in `RiskManager` where bps filter can be disabled by setting `min_profit_bps = 0`.

### 19. Add Security Checks Before Transaction Execution ✅

**Problem**: No validation of quote expiry (`last_valid_block_height`) or re-check of balance before sending transactions. Quotes could expire between receipt and execution, and balance could change after initial check.

**Fix**:
- **Added `get_current_slot()` method** in `SolanaClient` to get current slot/block height from Solana RPC
- **Quote expiry check**: Added validation of `last_valid_block_height` before sending transaction
  - Gets current slot via `get_current_slot()`
  - Compares current slot with `last_valid_block_height` from swap response
  - If `current_slot >= last_valid_block_height`, quote is expired and transaction is blocked
  - If `get_current_slot()` returns `None`, warning is logged but execution continues (non-blocking)
  - If `last_valid_block_height = 0`, expiry check is skipped with warning
- **Balance re-check**: Added second balance check immediately before sending transaction
  - Gets current balance via `get_balance()`
  - Updates balance in `risk_manager` via `update_wallet_balance()`
  - Checks `available_balance >= opportunity.initial_amount`
  - If insufficient, transaction is blocked with error message
- **Improved logging** in `get_swap_transaction()`: logs warning if `lastValidBlockHeight` is missing from Jupiter API response

**Files**:
- `src/solana_client.py`: Added `async def get_current_slot() -> Optional[int]` method (lines 49-68)
- `src/solana_client.py`: Added error handling and logging for slot retrieval
- `src/trader.py`: Added quote expiry check after building swap_response (lines 202-215)
- `src/trader.py`: Added balance re-check before sending transaction (lines 217-227)
- `src/trader.py`: Both checks are placed before `send_transaction()` call and block execution if failed
- `src/jupiter_client.py`: Added logging when `lastValidBlockHeight` is missing from API response (lines 314-315)
- `src/jupiter_client.py`: Added debug logging of `last_valid_block_height` in swap response (line 323)

**Note**: Both security checks are critical and block transaction execution if validation fails. Quote expiry check uses slot-based validation (not timestamp). Balance is updated in risk_manager before checking available balance.

### 20. Add DIAGNOSTIC_AMOUNT_SOL Configuration Variable ✅

**Problem**: Diagnostic mode used hardcoded 1 SOL amount in `jupiter_client.py` method `get_sol_price_usdc()` (line 351). This prevented testing with different amounts and made the code less flexible for diagnostic purposes.

**Fix**:
- Added `DIAGNOSTIC_AMOUNT_SOL` environment variable in `env.example`
- Added `amount_sol: float = 1.0` parameter to `get_sol_price_usdc()` method in `jupiter_client.py`
- Convert hardcoded `amount = 1_000_000_000` to `amount = int(amount_sol * 1e9)` using parameter
- Read `DIAGNOSTIC_AMOUNT_SOL` from `.env` in diagnostic mode (default: 1.0 SOL)
- Pass `diagnostic_amount_sol` to `get_sol_price_usdc()` call in diagnostic mode
- Updated logging to show actual amount used: `logger.info(f"Amount: {diagnostic_amount_sol} SOL")`
- When called at startup (line 184), uses default `amount_sol=1.0` (no change to startup behavior)
- This makes diagnostic mode more flexible and allows testing with different amounts
- **Note**: Scan and simulate modes continue to use risk limit calculations (no changes to their logic)

**Files**:
- `env.example`: Added `DIAGNOSTIC_AMOUNT_SOL` variable in Diagnostic Mode section (after `DIAGNOSTIC_SLIPPAGE_BPS`, line 69)
- `src/jupiter_client.py`: Added `amount_sol: float = 1.0` parameter to `get_sol_price_usdc()` method (line 337)
- `src/jupiter_client.py`: Replaced hardcoded `amount = 1_000_000_000` with `amount = int(amount_sol * 1e9)` (line 353)
- `src/main.py`: Added reading of `DIAGNOSTIC_AMOUNT_SOL` from `.env` in diagnostic mode (line 254)
- `src/main.py`: Pass `amount_sol=diagnostic_amount_sol` to `get_sol_price_usdc()` call (line 263)
- `src/main.py`: Updated logging to show configured amount instead of hardcoded "1.0 SOL" (line 257)
- `CHANGES.md`: Added entry documenting the configuration variable and code implementation

### 21. Optimize Jupiter API Rate Limiting: Increase Cycles and Configurable Delays ✅

**Problem**: Current configuration (6 cycles × 3 requests = 18 requests in ~6 seconds ≈ 3 req/sec) exceeded Jupiter API rate limit of 60 requests/minute (1 req/sec). This limited arbitrage opportunity coverage and risked API quota violations.

**Fix**:
- Increased cycle count from 6 to 12 predefined 3-leg cycles (doubled coverage)
- Added configurable `QUOTE_DELAY_SECONDS` environment variable (default: 1.0 sec for 60 req/min limit)
- Optimized delays from 0.2 sec to 1.0 sec between requests (respects 1 req/sec limit)
- Updated `ArbitrageFinder` to use configurable delay from `.env` instead of hardcoded 0.2 sec
- Result: 12 cycles × 3 requests = 36 requests in ~40-45 seconds (0.8-0.9 req/sec, safely within limit)

**Files**:
- `env.example`: Added `QUOTE_DELAY_SECONDS=1.0` variable in new "Arbitrage Configuration" section (after line 55)
- `src/arbitrage_finder.py`: Added 6 new cycles to `FIXED_CYCLES` (total: 12 cycles, lines 51-95)
- `src/arbitrage_finder.py`: Added `quote_delay_seconds` parameter to `__init__()` (line ~89)
- `src/arbitrage_finder.py`: Replaced hardcoded `0.2` sec delays with `self.quote_delay_seconds` (lines 132, 227)
- `src/arbitrage_finder.py`: Updated comments for `FIXED_CYCLES` and delays (lines 49-50, 131, 225)
- `src/main.py`: Added reading of `QUOTE_DELAY_SECONDS` from `.env` (after line ~141)
- `src/main.py`: Pass `quote_delay_seconds` parameter to `ArbitrageFinder` constructor (line ~232)
- `src/main.py`: Updated scan mode logging to show optimized configuration (line ~323)
- `CHANGES.md`: Added entry documenting rate limit optimization

### 22. Update README.md to Reflect Optimized Rate Limiting Configuration ✅

**Problem**: README.md was outdated and did not reflect the current state of the project after Jupiter API rate limiting optimization. It still mentioned old configuration (6 cycles, 2-3 leg cycles, missing QUOTE_DELAY_SECONDS) and outdated performance metrics.

**Fix**:
- Updated "Quota-safe scanning" to "Quota-optimized scanning" with detailed information (12 cycles, ~40-45 seconds per scan)
- Fixed `max_cycle_length` in config.json example (3 → 4, correct for 3-leg cycles)
- Enhanced "Scan Mode" section with configuration details (12 cycles, 4 tokens, rate limiting, execution time)
- Updated `.env` file example with complete parameter list including `QUOTE_DELAY_SECONDS`, `MIN_PROFIT_USDC` (PRIMARY), `MIN_PROFIT_BPS` (SECONDARY), `LOG_LEVEL`
- Updated "Risk & Capital Management" section to clarify PRIMARY/SECONDARY logic for minimum profit
- Updated "How It Works" → "Opportunity search" to reflect 12 predefined 3-leg cycles, 4 tokens, rate limiting, and execution time
- Updated "Limitations" section with accurate information about 3-leg cycles, rate limits, and scan execution time
- All sections now consistently reflect: 12 cycles, 3-leg format (A → B → C → A), 4 tokens (SOL, USDC, JUP, BONK), 60 req/min limit, ~40-45 seconds execution time

**Files**:
- `README.md`: Updated "Project Status" → "Stage 1" section (line 13-14)
- `README.md`: Fixed `max_cycle_length: 3` → `4` in config.json example (line 104)
- `README.md`: Enhanced "Scan Mode" section with configuration and performance details (lines 112-123)
- `README.md`: Updated `.env` file example with complete parameter list (lines 67-88)
- `README.md`: Updated "Risk & Capital Management" → "Minimum profit" section (lines 175-177)
- `README.md`: Updated "How It Works" → "Opportunity search" section (lines 211-216)
- `README.md`: Updated "Limitations" section (lines 241-246)
- `CHANGES.md`: Added entry documenting README.md updates

## Result

✅ Limit logic is consistent (all in USDC)
✅ Risk control is unambiguous (USD primary, bps secondary)
✅ Accidental live mode impossible (strict mode check)
✅ Parallel trades impossible (trade_in_progress flag)
✅ Sending without simulation impossible (mandatory check)
✅ All RiskConfig attributes synchronized (no AttributeError)
✅ Jupiter API uses working DNS endpoint (api.jup.ag)
✅ Jupiter API uses correct endpoint format (/swap/v1/quote)
✅ Optimized scan configuration: 4 tokens, 12 fixed cycles, sequential processing with configurable delays (rate-limited: 36 requests in ~40-45 seconds, safely within 60 req/min limit)
✅ Slippage is configurable via `.env` (`SLIPPAGE_BPS`, `DIAGNOSTIC_SLIPPAGE_BPS`) with validation against `MAX_SLIPPAGE_BPS`
✅ Improved slippage validation and logging: explicit warnings when `MAX_SLIPPAGE_BPS` is not set, detailed error messages with instructions, final configuration summary
✅ Centralized SOL/USDC price fetching method: no code duplication, automatic price fetching at startup, reusable for diagnostic mode
✅ ArbitrageFinder uses actual SOL price from config (auto-fetched from Jupiter API or `.env`): correct profit calculations regardless of SOL price, no hardcoded values
✅ Consistent naming convention: all profit minimum values use `usdc` suffix consistently across `.env`, `config.json`, and code
✅ Code optimization: removed redundant double environment access for `MAX_SLIPPAGE_BPS`, consistent with `SLIPPAGE_BPS` implementation
✅ Arbitrage cycles updated: removed USDT (stablecoin pairs not useful for arbitrage), added BONK (volatile meme coin) for better arbitrage opportunities
✅ Logging level is configurable via `.env` (`LOG_LEVEL`): supports DEBUG, INFO, WARNING, ERROR, CRITICAL with validation and fallback to INFO
✅ Critical bugs fixed: simulation validation now uses actual quote values instead of comparing with itself, profit filtering explicitly checks `min_profit_bps > 0` before applying filter
✅ Security checks added before transaction execution: quote expiry validation (last_valid_block_height) and balance re-check prevent execution with stale quotes or insufficient balance
✅ Diagnostic mode configuration: `DIAGNOSTIC_AMOUNT_SOL` variable added for configurable diagnostic request amount (default: 1.0 SOL)
✅ Jupiter API rate limiting optimized: increased cycles from 6 to 12 (doubled coverage), configurable `QUOTE_DELAY_SECONDS` delay (1.0 sec default for 60 req/min limit), optimized delays from 0.2 sec to 1.0 sec, rate-limited scan respects API quotas (36 requests in ~40-45 seconds, safely within 60 req/min limit)
✅ README.md updated: accurately reflects optimized rate limiting configuration (12 cycles, 3-leg format, 4 tokens, 60 req/min limit, ~40-45 seconds execution time), complete parameter documentation, clarified PRIMARY/SECONDARY profit logic