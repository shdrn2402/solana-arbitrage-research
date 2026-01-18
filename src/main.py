"""
Main entry point for Solana arbitrage bot.
"""
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional, Dict, Tuple

import dotenv
from solders.keypair import Keypair
from solders.pubkey import Pubkey

from .jupiter_client import JupiterClient
from .solana_client import SolanaClient
from .risk_manager import RiskManager, RiskConfig
from .arbitrage_finder import ArbitrageFinder, ArbitrageOpportunity
from .trader import Trader
from .utils import get_terminal_colors

# Get terminal colors (empty if output is redirected)
colors = get_terminal_colors()

# Logger will be initialized in main() after .env is loaded
logger = logging.getLogger(__name__)

# Suppress verbose httpx INFO logs (HTTP requests)
logging.getLogger("httpx").setLevel(logging.WARNING)

def load_config() -> dict:
    """Load configuration from .env and config.json."""
    # Load .env
    env_path = Path(__file__).parent.parent / '.env'
    if env_path.exists():
        dotenv.load_dotenv(env_path)
    else:
        logger.warning(f".env file not found at {env_path}")
    
    # Load config.json
    config_path = Path(__file__).parent.parent / 'config.json'
    if config_path.exists():
        with open(config_path, 'r') as f:
            config = json.load(f)
    else:
        logger.warning(f"config.json not found at {config_path}")
        config = {}
    
    return config


def format_cycle_with_symbols(cycle: list[str], tokens_map: dict) -> str:
    """Convert cycle addresses to token symbols."""
    symbols = []
    for addr in cycle:
        symbol = tokens_map.get(addr, addr)  # Use symbol if found, otherwise use address
        symbols.append(symbol)
    return ' -> '.join(symbols)


def load_wallet(private_key_str: Optional[str] = None) -> Optional[Keypair]:
    """Load wallet from private key."""
    if not private_key_str:
        private_key_str = os.getenv('WALLET_PRIVATE_KEY')
    
    if not private_key_str:
        logger.warning("No wallet private key provided")
        return None
    
    try:
        # Decode base58 private key
        import base58
        key_bytes = base58.b58decode(private_key_str)
        return Keypair.from_bytes(key_bytes)
    except Exception as e:
        logger.error(f"Error loading wallet: {e}")
        return None


async def fetch_sol_lamports(solana: SolanaClient, timeout_sec: float) -> Optional[int]:
    """Fetch SOL balance in lamports from RPC with timeout.
    
    Returns:
        SOL balance in lamports, or None on error
    """
    try:
        balance = await asyncio.wait_for(solana.get_balance(), timeout=timeout_sec)
        return balance
    except asyncio.TimeoutError:
        logger.warning(f"SOL balance fetch timed out after {timeout_sec}s")
        return None
    except Exception as e:
        logger.warning(f"Error fetching SOL balance: {e}")
        return None


async def fetch_usdc_units(solana: SolanaClient, wallet: Keypair, usdc_mint: str, timeout_sec: float) -> Optional[int]:
    """Fetch USDC balance in smallest units (6 decimals) from RPC with timeout.
    
    Returns:
        USDC balance in smallest units, or None on error
    """
    try:
        from solana.rpc.commitment import Confirmed
        from solana.rpc.types import TokenAccountOpts
        from base64 import b64decode
        import struct
        
        token_program_id = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
        wallet_pubkey = wallet.pubkey()
        
        async def _fetch():
            result = await solana.client.get_token_accounts_by_owner(
                wallet_pubkey,
                TokenAccountOpts(program_id=token_program_id),
                commitment=Confirmed
            )
            return result
        
        result = await asyncio.wait_for(_fetch(), timeout=timeout_sec)
        
        if result.value:
            for account_info in result.value:
                try:
                    account_data = account_info.account.data
                    
                    # Handle different data formats
                    account_data_bytes = None
                    if isinstance(account_data, list) and len(account_data) > 0:
                        account_data_bytes = b64decode(account_data[0])
                    elif isinstance(account_data, str):
                        account_data_bytes = b64decode(account_data)
                    elif hasattr(account_data, '__bytes__'):
                        account_data_bytes = bytes(account_data)
                    elif hasattr(account_data, '__iter__') and not isinstance(account_data, (str, bytes)):
                        try:
                            data_list = list(account_data)
                            if len(data_list) > 0 and isinstance(data_list[0], str):
                                account_data_bytes = b64decode(data_list[0])
                        except Exception:
                            pass
                    
                    if account_data_bytes is None or len(account_data_bytes) < 72:
                        continue
                    
                    # Extract mint (first 32 bytes)
                    mint_bytes = account_data_bytes[0:32]
                    mint_pubkey = Pubkey.from_bytes(mint_bytes)
                    mint = str(mint_pubkey)
                    
                    # Compare with USDC mint address
                    if mint == usdc_mint:
                        # Extract amount (8 bytes, offset 64)
                        amount_bytes = account_data_bytes[64:72]
                        amount = struct.unpack('<Q', amount_bytes)[0]  # u64 little-endian
                        return amount
                except Exception:
                    continue
        
        return 0  # No USDC account found
    except asyncio.TimeoutError:
        logger.warning(f"USDC balance fetch timed out after {timeout_sec}s")
        return None
    except Exception as e:
        logger.warning(f"Error fetching USDC balance: {e}")
        return None


async def main(mode: str = 'scan'):
    """
    Main function.
    
    Args:
        mode: Operation mode - 'scan' (default), 'simulate', or 'live'
    """
    # Load .env FIRST to read LOG_LEVEL before setting up logging
    env_path = Path(__file__).parent.parent / '.env'
    if env_path.exists():
        dotenv.load_dotenv(env_path)
    
    # Setup logging AFTER .env is loaded
    log_level_str = os.getenv('LOG_LEVEL', 'INFO').upper()
    log_level = getattr(logging, log_level_str, logging.INFO)
    
    # Validate log level
    if log_level_str not in ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']:
        print(f"Warning: Invalid LOG_LEVEL '{log_level_str}', using INFO")
        log_level = logging.INFO
    
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler('arbitrage_bot.log')
        ]
    )


    # Normalize mode to lowercase
    mode = mode.lower()
    
    logger.info("Starting Solana Arbitrage Bot")
    logger.debug(f"Log level set to: {log_level_str}")
    
    # Load configuration (will reload .env, but that's fine - dotenv doesn't overwrite existing vars)
    config = load_config()
    
    # Environment variables
    # RPC URL selection by mode
    # Scan mode: use RPC_URL_SCAN (free/public, doesn't consume trial credits)
    # Simulate/Live modes: use RPC_URL_PRIMARY (trial/premium) with failover to RPC_URL_FALLBACK
    rpc_url_scan = os.getenv('RPC_URL_SCAN', os.getenv('RPC_URL', 'https://api.mainnet-beta.solana.com'))
    rpc_url_primary = os.getenv('RPC_URL_PRIMARY')
    rpc_url_fallback = os.getenv('RPC_URL_FALLBACK')
    
    # Select RPC URL based on mode
    if mode == 'scan':
        rpc_url = rpc_url_scan
        logger.debug(f"Using scan RPC: {rpc_url_scan.split('//')[1].split('/')[0] if '//' in rpc_url_scan else rpc_url_scan}")
    elif mode in ('simulate', 'live'):
        if not rpc_url_primary:
            logger.error(
                f"{colors['RED']}RPC_URL_PRIMARY is required for {mode} mode{colors['RESET']}. "
                f"Please set RPC_URL_PRIMARY in .env file."
            )
            return
        rpc_url = rpc_url_primary
        if rpc_url_fallback:
            logger.debug(
                f"Using primary RPC: {rpc_url_primary.split('//')[1].split('/')[0] if '//' in rpc_url_primary else rpc_url_primary} "
                f"(fallback: {rpc_url_fallback.split('//')[1].split('/')[0] if '//' in rpc_url_fallback else rpc_url_fallback})"
            )
        else:
            logger.debug(f"Using primary RPC: {rpc_url_primary.split('//')[1].split('/')[0] if '//' in rpc_url_primary else rpc_url_primary} (no fallback)")
    else:
        # Fallback to scan RPC for unknown modes
        rpc_url = rpc_url_scan
    
    # Jupiter API URL: if not set, client will use fallback mechanism
    # If set explicitly, that URL will be used (no fallback)
    jupiter_api_url = os.getenv('JUPITER_API_URL')  # None = use fallback
    jupiter_api_key = os.getenv('JUPITER_API_KEY')  # Optional API key for authenticated requests
    
    # Risk config - all limits in USDC for consistency
    sol_price_usdc = float(os.getenv('SOL_PRICE_USDC', '100.0'))  # Default SOL price
    max_position_absolute_sol = float(os.getenv('MAX_POSITION_SIZE_ABSOLUTE', '1.0'))
    max_position_absolute_usdc = max_position_absolute_sol * sol_price_usdc  # Convert to USDC
    
    # Min profit: env takes precedence, then config.json, then default
    min_profit_usdc_env = os.getenv('MIN_PROFIT_USDC')
    if min_profit_usdc_env:
        min_profit_usdc = float(min_profit_usdc_env)
    else:
        min_profit_usdc = config.get('arbitrage', {}).get('min_profit_usdc', 0.1)
    
    # Read MAX_SLIPPAGE_BPS with check for explicit setting
    max_slippage_bps_env = os.getenv('MAX_SLIPPAGE_BPS')
    max_slippage_bps_explicitly_set = max_slippage_bps_env is not None
    max_slippage_bps_value = int(max_slippage_bps_env) if max_slippage_bps_env else 50
    
    risk_config = RiskConfig(
        max_position_size_percent=float(os.getenv('MAX_POSITION_SIZE_PERCENT', '10.0')),
        max_position_size_absolute_usdc=max_position_absolute_usdc,
        min_profit_usdc=min_profit_usdc,  # PRIMARY check in USDC
        min_profit_bps=int(os.getenv('MIN_PROFIT_BPS', '50')),  # Secondary filter (can be 0 to disable)
        max_slippage_bps=max_slippage_bps_value,
        max_active_positions=int(os.getenv('MAX_ACTIVE_POSITIONS', '1')),
        sol_price_usdc=sol_price_usdc
    )
    
    
    # Priority fee
    priority_fee = int(os.getenv('PRIORITY_FEE_LAMPORTS', '10000'))
    use_jito = os.getenv('USE_JITO', 'false').lower() == 'true'
    
    # Slippage configuration - save original value before validation
    slippage_bps_env = os.getenv('SLIPPAGE_BPS')
    slippage_bps_explicitly_set = slippage_bps_env is not None
    slippage_bps = int(slippage_bps_env) if (slippage_bps_env and slippage_bps_env.strip()) else 50
    slippage_bps_original = slippage_bps  # Сохраняем для сравнения после валидации
    diagnostic_slippage_bps = int(os.getenv('DIAGNOSTIC_SLIPPAGE_BPS', '500'))
    quote_delay_seconds = float(os.getenv('QUOTE_DELAY_SECONDS', '1.0'))  # Deprecated, kept for backward compatibility
    
    # Jupiter rate limiting configuration
    jupiter_rps = float(os.getenv('JUPITER_RPS', '1.0'))  # Requests per second (default: 1.0)
    jupiter_max_retries_429 = int(os.getenv('JUPITER_MAX_RETRIES_ON_429', '3'))
    jupiter_backoff_base = float(os.getenv('JUPITER_BACKOFF_BASE_SECONDS', '1.0'))
    jupiter_backoff_max = float(os.getenv('JUPITER_BACKOFF_MAX_SECONDS', '30.0'))
    
    # Non-stop mode configuration (for simulate/live)
    balance_refresh_sol_every_sec = float(os.getenv('BALANCE_REFRESH_SOL_EVERY_SEC', '2.0'))
    balance_refresh_usdc_every_sec = float(os.getenv('BALANCE_REFRESH_USDC_EVERY_SEC', '15.0'))
    balance_force_refresh_usdc_if_older_sec = float(os.getenv('BALANCE_FORCE_REFRESH_USDC_IF_OLDER_SEC', '5.0'))
    balance_refresh_rpc_timeout_sec = float(os.getenv('BALANCE_REFRESH_RPC_TIMEOUT_SEC', '2.0'))
    loop_idle_sleep_sec = float(os.getenv('LOOP_IDLE_SLEEP_SEC', '2.0'))
    fail_backoff_base_sec = float(os.getenv('FAIL_BACKOFF_BASE_SEC', '1.0'))
    fail_backoff_max_sec = float(os.getenv('FAIL_BACKOFF_MAX_SEC', '30.0'))
    
    # SOL price refresh configuration
    sol_price_refresh_every_sec = float(os.getenv('SOL_PRICE_REFRESH_EVERY_SEC', '300.0'))  # 5 minutes default
    sol_price_refresh_timeout_sec = float(os.getenv('SOL_PRICE_REFRESH_TIMEOUT_SEC', '2.0'))
    sol_price_refresh_log_every_n = int(os.getenv('SOL_PRICE_REFRESH_LOG_EVERY_N', '12'))  # Log every ~1 hour if 5min interval
    
    # Route negative cache configuration
    negative_cache_ttl_sec = int(os.getenv('NEGATIVE_CACHE_TTL_SEC', '600'))  # 10 minutes default
    
    # Warn if MAX_SLIPPAGE_BPS not explicitly set (only if SLIPPAGE_BPS is explicitly set)
    # This preserves backward compatibility: if both are unset (defaults 50/50), no warning
    if not max_slippage_bps_explicitly_set and slippage_bps_explicitly_set:
        logger.warning(
            "MAX_SLIPPAGE_BPS not set in .env, using default value 50. "
            "If SLIPPAGE_BPS > 50, it will be automatically capped."
        )
    
    # Validate slippage: SLIPPAGE_BPS must be <= MAX_SLIPPAGE_BPS
    if slippage_bps > risk_config.max_slippage_bps:
        logger.error(
            f"SLIPPAGE_BPS ({slippage_bps}) exceeds MAX_SLIPPAGE_BPS ({risk_config.max_slippage_bps}). "
            f"Either increase MAX_SLIPPAGE_BPS in .env or decrease SLIPPAGE_BPS. "
            f"Using MAX_SLIPPAGE_BPS as limit for safety."
        )
        slippage_bps = risk_config.max_slippage_bps
    
    # Final validation summary - show if slippage was adjusted or if explicitly configured
    if slippage_bps != slippage_bps_original:
        logger.warning(
            f"Slippage adjusted: MAX_SLIPPAGE_BPS={risk_config.max_slippage_bps}, "
            f"SLIPPAGE_BPS={slippage_bps} (adjusted from {slippage_bps_original} in .env)"
        )
    elif max_slippage_bps_explicitly_set or slippage_bps_explicitly_set:
        # Log current configuration if at least one variable was explicitly set
        logger.info(
            f"Slippage configuration: MAX_SLIPPAGE_BPS={colors['YELLOW']}{risk_config.max_slippage_bps}{colors['RESET']}, "
            f"SLIPPAGE_BPS={colors['GREEN']}{slippage_bps}{colors['RESET']}"
        )   
    
    # Load wallet
    wallet = load_wallet()
    if wallet is None and mode != 'scan':
        logger.error("Wallet required for simulate/live modes")
        return
    
    # Initialize clients
    jupiter = JupiterClient(
        jupiter_api_url,
        api_key=jupiter_api_key,
        requests_per_second=jupiter_rps,
        max_retries_on_429=jupiter_max_retries_429,
        backoff_base_seconds=jupiter_backoff_base,
        backoff_max_seconds=jupiter_backoff_max
    )
    # Create SolanaClient with failover support for simulate/live modes
    if mode in ('simulate', 'live') and rpc_url_fallback:
        solana = SolanaClient(rpc_url, wallet, fallback_rpc_url=rpc_url_fallback)
    else:
        solana = SolanaClient(rpc_url, wallet)
    
    # Try to fetch SOL price from Jupiter API
    sol_price_auto = await jupiter.get_sol_price_usdc(slippage_bps=10)
    if sol_price_auto and sol_price_auto > 0:
        sol_price_usdc = sol_price_auto
        logger.info(f"SOL price fetched from Jupiter API: {colors['GREEN']}{sol_price_usdc:.2f} {colors['CYAN']}USDC{colors['RESET']}")
        # Update risk_config with fetched price
        risk_config.sol_price_usdc = sol_price_usdc
        # Recalculate max_position_absolute_usdc with updated price
        risk_config.max_position_size_absolute_usdc = max_position_absolute_sol * sol_price_usdc
    else:
        # Fallback to .env value
        logger.warning(
            f"Could not fetch SOL price from Jupiter API, using .env value: "
            f"${sol_price_usdc:.2f} USDC"
        )
    
    # Initialize risk manager
    risk_manager = RiskManager(risk_config)
    
    # Initialize balance variables
    sol_balance = 0.0
    usdc_balance = 0.0
    
    # Token mint addresses
    sol_mint = "So11111111111111111111111111111111111111112"
    usdc_mint = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    
    # Update wallet balances (token-aware)
    if wallet:
        balance = await solana.get_balance()
        sol_balance = balance / 1e9  # Convert from lamports to SOL
        logger.info(f"SOL balance: {colors['GREEN']}{sol_balance:.4f}{colors['RESET']} {colors['CYAN']}SOL{colors['RESET']}")
        
        # Get USDC balance
        usdc_units = 0  # in smallest units (6 decimals)
        try:
            from solana.rpc.commitment import Confirmed
            from solana.rpc.types import TokenAccountOpts
            
            usdc_mint_str = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
            token_program_id = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
            wallet_pubkey = wallet.pubkey()
            
            # Get all SPL token accounts by owner with programId filter
            # Note: get_token_accounts_by_owner doesn't support encoding parameter
            # We'll get raw data and parse it manually
            from solana.rpc.types import TokenAccountOpts
            
            result = await solana.client.get_token_accounts_by_owner(
                wallet_pubkey,
                TokenAccountOpts(program_id=token_program_id),
                commitment=Confirmed
            )
            
            if result.value:
                logger.debug(f"Found {len(result.value)} token accounts")
                # Iterate through all token accounts to find USDC
                for idx, account_info in enumerate(result.value):
                    try:
                        # Parse raw account data
                        # SPL Token Account structure:
                        # - mint: Pubkey (32 bytes, offset 0)
                        # - owner: Pubkey (32 bytes, offset 32)
                        # - amount: u64 (8 bytes, offset 64)
                        from base64 import b64decode
                        import struct
                        
                        # Get account data (base64 encoded bytes)
                        account_data = account_info.account.data
                        logger.debug(f"Account {idx}: data type = {type(account_data)}")
                        
                        # Handle different data formats
                        account_data_bytes = None
                        if isinstance(account_data, list) and len(account_data) > 0:
                            # Format: ["base64string", "base58"]
                            account_data_bytes = b64decode(account_data[0])
                        elif isinstance(account_data, str):
                            # Format: "base64string"
                            account_data_bytes = b64decode(account_data)
                        elif hasattr(account_data, '__bytes__'):
                            # If it's already bytes
                            account_data_bytes = bytes(account_data)
                        elif hasattr(account_data, '__iter__') and not isinstance(account_data, (str, bytes)):
                            # Try to get first element if it's iterable
                            try:
                                data_list = list(account_data)
                                if len(data_list) > 0 and isinstance(data_list[0], str):
                                    account_data_bytes = b64decode(data_list[0])
                            except Exception as decode_err:
                                logger.debug(f"Account {idx}: decode error: {decode_err}")
                                pass
                        
                        if account_data_bytes is None:
                            logger.debug(f"Account {idx}: could not decode data, type={type(account_data)}, repr={repr(account_data)[:100]}")
                            continue
                        
                        logger.debug(f"Account {idx}: decoded data length = {len(account_data_bytes)}")
                        
                        # SPL Token Account is 165 bytes total
                        if len(account_data_bytes) < 72:
                            logger.debug(f"Account {idx}: data too short ({len(account_data_bytes)} bytes)")
                            continue
                        
                        # Extract mint (first 32 bytes)
                        mint_bytes = account_data_bytes[0:32]
                        mint_pubkey = Pubkey.from_bytes(mint_bytes)
                        mint = str(mint_pubkey)
                        
                        logger.debug(f"Account {idx}: mint = {mint}")
                        
                        # Compare with USDC mint address
                        if mint == usdc_mint_str:
                            # Extract amount (8 bytes, offset 64)
                            # Use little-endian unsigned long long (Q)
                            amount_bytes = account_data_bytes[64:72]
                            amount = struct.unpack('<Q', amount_bytes)[0]  # u64 little-endian
                            usdc_units = amount  # Store in smallest units
                            usdc_balance = amount / 1e6  # USDC has 6 decimals
                            logger.debug(f"USDC found! Raw amount: {amount}, UI amount: {usdc_balance}")
                            break  # USDC found, exit loop
                    except Exception as e:
                        logger.debug(f"Error parsing token account {idx}: {e}", exc_info=True)
                        continue
            else:
                logger.debug("No token accounts found in result.value")
            
            logger.info(f"USDC balance: {colors['GREEN']}{usdc_balance:.2f}{colors['RESET']} {colors['CYAN']}USDC{colors['RESET']}")
        except Exception as e:
            logger.warning(f"Could not retrieve USDC balance: {e}", exc_info=True)
            logger.info(f"{colors['CYAN']}USDC balance:{colors['RESET']} {colors['GREEN']}0.00{colors['RESET']} {colors['CYAN']}USDC{colors['RESET']}")
            usdc_units = 0
        
        # Update RiskManager with token-aware balances
        balances_by_mint = {
            sol_mint: int(balance),  # SOL in lamports
            usdc_mint: usdc_units  # USDC in smallest units (6 decimals)
        }
        risk_manager.update_wallet_balances(balances_by_mint)
    
    # Get tokens from config (minimal set: SOL, USDC, JUP, BONK)
    tokens_config = config.get('tokens', {})
    tokens = list(tokens_config.values())
    
    # Create reverse mapping: address -> symbol
    tokens_map = {v: k for k, v in tokens_config.items()}
    if not tokens:
        # Default minimal tokens (quota-safe)
        tokens = [
            "So11111111111111111111111111111111111111112",  # SOL
            "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
            "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",  # JUP
            "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",  # BONK
        ]
    
    # Load cycles from config.json
    cycles = config.get('cycles', [])
    if not cycles:
        logger.warning("No cycles found in config.json. Please add cycles section to config.json")
    
    # Calculate test_amount (will be logged in effective config after calculation)
    # This is done here to have cycles count available for logging
    # Note: test_amount calculation happens later, but we'll log it there too
    
    # Initialize arbitrage finder
    arbitrage_config = config.get('arbitrage', {})
    finder = ArbitrageFinder(
        jupiter,
        tokens,
        min_profit_bps=risk_config.min_profit_bps,
        min_profit_usd=risk_config.min_profit_usdc,  # Use min_profit_usdc from RiskConfig
        max_cycle_length=arbitrage_config.get('max_cycle_length', 4),
        max_cycles=arbitrage_config.get('max_cycles', 100),
        quote_timeout=arbitrage_config.get('quote_timeout', 5.0),
        slippage_bps=slippage_bps,
        sol_price_usdc=risk_config.sol_price_usdc,
        quote_delay_seconds=quote_delay_seconds,
        cycles=cycles
    )
    
    # Initialize trader with mode for safety checks
    trader = Trader(
        jupiter,
        solana,
        risk_manager,
        finder,
        priority_fee_lamports=priority_fee,
        use_jito=use_jito,
        mode=mode,  # Pass mode for strict checking
        slippage_bps=slippage_bps,
        tokens_map=tokens_map,
        negative_cache_ttl_sec=negative_cache_ttl_sec
    )
    
    # DIAGNOSTIC MODE: Test if Jupiter can return routes at all
    # Set DIAGNOSTIC_MODE=true in .env to enable
    diagnostic_mode = os.getenv('DIAGNOSTIC_MODE', 'false').lower() == 'true'
    if diagnostic_mode:
        logger.info("=" * 60)
        logger.info("DIAGNOSTIC MODE: Testing Jupiter route capability")
        logger.info("=" * 60)
        
        diagnostic_amount_sol = float(os.getenv('DIAGNOSTIC_AMOUNT_SOL', '1.0'))
        
        logger.info(f"Request: {colors['CYAN']}SOL{colors['RESET']} → {colors['CYAN']}USDC{colors['RESET']}")
        logger.info(f"Amount: {colors['GREEN']}{diagnostic_amount_sol}{colors['RESET']} {colors['CYAN']}SOL{colors['RESET']}")
        logger.info(f"Parameters: slippageBps={colors['GREEN']}{diagnostic_slippage_bps}{colors['RESET']}, onlyDirectRoutes=false")
        
        quote = await jupiter.get_sol_price_usdc(
            slippage_bps=diagnostic_slippage_bps,
            return_full_quote=True,
            amount_sol=diagnostic_amount_sol
        )
        
        if quote:
            logger.info("✓ Quote received successfully")
            logger.info(f"  Input: {quote.in_amount / 1e9:.6f} SOL")
            logger.info(f"  Output: {quote.out_amount / 1e6:.2f} USDC")
            logger.info(f"  Price impact: {quote.price_impact_pct:.4f}%")
            
            if quote.route_plan:
                logger.info(f"✓ Route plan found: {len(quote.route_plan)} hops/steps")
                for i, hop in enumerate(quote.route_plan, 1):
                    # Log hop details safely
                    hop_info = f"Hop {i}: "
                    if isinstance(hop, dict):
                        swap_info = hop.get('swapInfo', {})
                        if swap_info:
                            amm_key = swap_info.get('ammKey', 'N/A')
                            hop_info += f"AMM={amm_key[:16]}..." if len(amm_key) > 16 else f"AMM={amm_key}"
                        else:
                            hop_info += str(hop)[:50]
                    else:
                        hop_info += str(hop)[:50]
                    logger.info(f"  {hop_info}")
            else:
                logger.warning("✗ Route plan is empty (no hops/steps)")
        else:
            logger.error("✗ No quote received from Jupiter")
            logger.error("  Jupiter cannot build routes for SOL → USDC")
        
        logger.info("=" * 60)
        logger.info("Diagnostic test complete. Exiting.")
        logger.info("Set DIAGNOSTIC_MODE=false to run normal scan mode")
        logger.info("=" * 60)
        
        # Cleanup and exit
        await jupiter.close()
        await solana.close()
        return
    
    # Determine starting token and amount
    # Prefer SOL or USDC as base token for cycles
    sol_mint = "So11111111111111111111111111111111111111112"
    usdc_mint = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    
    if sol_mint in tokens:
        start_token = sol_mint
    elif usdc_mint in tokens:
        start_token = usdc_mint
    else:
        start_token = tokens[0]  # Fallback to first token
    
    # Compute per-base-token test amounts (SOL and USDC have different decimals and balances).
    # This makes USDC-based cycles comparable/fair instead of reusing SOL lamports for all cycles.
    max_position_absolute_sol_calc = risk_config.max_position_size_absolute_usdc / risk_config.sol_price_usdc

    available_sol_lamports = risk_manager.get_available_balance(sol_mint) if wallet else 1_000_000_000  # 1 SOL default
    test_amount_sol = min(
        int(available_sol_lamports * risk_config.max_position_size_percent / 100),
        int(max_position_absolute_sol_calc * 1e9)
    )

    available_usdc_units = int(usdc_balance * 1e6)  # USDC has 6 decimals
    test_amount_usdc = min(
        int(available_usdc_units * risk_config.max_position_size_percent / 100),
        int(risk_config.max_position_size_absolute_usdc * 1e6)
    )

    # Backward-compatible default (used for logs/start_token only)
    test_amount = test_amount_sol if start_token == sol_mint else test_amount_usdc

    amounts_by_mint = {
        sol_mint: test_amount_sol,
        usdc_mint: test_amount_usdc
    }
    
    # Log effective runtime configuration with test_amount and cycles count
    logger.info(
        f"Effective config: "
        f"mode={colors['CYAN']}{mode}{colors['RESET']}, "
        f"MIN_PROFIT_USDC={colors['YELLOW']}{risk_config.min_profit_usdc:.4f}{colors['RESET']}, "
        f"MIN_PROFIT_BPS={colors['GREEN']}{risk_config.min_profit_bps}{colors['RESET']}, "
        f"SLIPPAGE_BPS={colors['GREEN']}{slippage_bps}{colors['RESET']}, "
        f"MAX_SLIPPAGE_BPS={colors['YELLOW']}{risk_config.max_slippage_bps}{colors['RESET']}, "
        f"MAX_POSITION_SIZE_PERCENT={colors['YELLOW']}{risk_config.max_position_size_percent}%{colors['RESET']}, "
        f"MAX_POSITION_SIZE_ABSOLUTE_SOL={colors['YELLOW']}{max_position_absolute_sol:.4f}{colors['RESET']}, "
        f"TEST_AMOUNT_SOL={colors['GREEN']}{test_amount_sol/1e9:.6f}{colors['RESET']} ({colors['DIM']}{test_amount_sol} lamports{colors['RESET']}), "
        f"TEST_AMOUNT_USDC={colors['GREEN']}{test_amount_usdc/1e6:.2f}{colors['RESET']} ({colors['DIM']}{test_amount_usdc} units{colors['RESET']}), "
        f"QUOTE_DELAY_SECONDS={colors['GREEN']}{quote_delay_seconds}{colors['RESET']}, "
        f"PATHS_CONFIGURED={colors['GREEN']}{len(cycles)}{colors['RESET']}"
    )
    
    async def run_nonstop(
        mode: str,
        finder: ArbitrageFinder,
        trader: Trader,
        solana: SolanaClient,
        jupiter: JupiterClient,
        risk_manager: RiskManager,
        wallet: Keypair,
        tokens_map: Dict[str, str],
        cycles: list,
        amounts_by_mint: Dict[str, int],
        sol_mint: str,
        usdc_mint: str,
        initial_sol_lamports: int,
        initial_usdc_units: int,
        start_token: str,
        test_amount: int
    ):
        """Non-stop runner for simulate/live modes with smart balance refresh."""
        user_pubkey = str(wallet.pubkey())
        
        # Initialize balance state
        sol_lamports_last = initial_sol_lamports
        usdc_units_last = initial_usdc_units
        t_sol_last = time.monotonic()
        t_usdc_last = time.monotonic()
        fail_streak = 0
        
        # Initialize SOL price refresh state
        sol_price_usdc_last = risk_manager.config.sol_price_usdc
        t_sol_price_last = time.monotonic()
        price_update_count = 0
        
        # Callback for immediate processing when opportunity is found
        async def on_opportunity_found(opp: ArbitrageOpportunity) -> bool:
            """Process opportunity immediately with retries."""
            nonlocal usdc_units_last, t_usdc_last
            
            cycle_display = format_cycle_with_symbols(opp.cycle, tokens_map)
            base_mint = opp.cycle[0]
            
            # Force refresh USDC if this is a USDC-base opportunity and balance is stale
            if base_mint == usdc_mint:
                t_now = time.monotonic()
                age = t_now - t_usdc_last
                if age >= balance_force_refresh_usdc_if_older_sec:
                    logger.debug(f"USDC balance is {age:.1f}s old, forcing refresh before USDC-base opportunity")
                    new_usdc_units = await fetch_usdc_units(
                        solana, wallet, usdc_mint, balance_refresh_rpc_timeout_sec
                    )
                    if new_usdc_units is not None:
                        usdc_units_last = new_usdc_units
                        t_usdc_last = t_now
                        # Update risk manager immediately
                        balances_by_mint = {
                            sol_mint: sol_lamports_last,
                            usdc_mint: usdc_units_last
                        }
                        risk_manager.update_wallet_balances(balances_by_mint)
                        logger.debug(f"USDC balance refreshed: {usdc_units_last / 1e6:.2f} USDC")
                    else:
                        logger.warning("Failed to refresh USDC balance before opportunity, using stale value")
            
            logger.debug(f"Found opportunity: {colors['CYAN']}{cycle_display}{colors['RESET']}")
            
            try:
                # Use burst mode for fast processing
                async with jupiter.rate_limiter.burst():
                    success_count = await trader.process_opportunity_with_retries(
                        opp.cycle,
                        opp.initial_amount,
                        user_pubkey,
                        max_retries=10,
                        first_attempt_use_original_opportunity=True,
                        original_opportunity=opp
                    )
                
                if success_count > 0:
                    logger.info(
                        f"Processed {colors['GREEN']}{success_count}{colors['RESET']} "
                        f"successful {'simulations' if mode == 'simulate' else 'executions'}"
                    )
            except Exception as e:
                error_msg = str(e)
                # Check if this is an expected "infra" error (swap-instructions unavailable)
                is_infra_error = (
                    "Failed to build atomic VersionedTransaction" in error_msg or
                    "Failed to get swap instructions" in error_msg or
                    "swap-instructions" in error_msg.lower() or
                    "cannot unpack" in error_msg.lower()
                )
                
                if is_infra_error:
                    # Log as WARNING without traceback (expected infra issue)
                    logger.warning(
                        f"{colors['YELLOW']}Skipping opportunity (infra issue):{colors['RESET']} {error_msg}"
                    )
                else:
                    # Unexpected error - log with traceback
                    logger.error(
                        f"{colors['RED']}Error in process_opportunity_with_retries:{colors['RESET']} {e}",
                        exc_info=True
                    )
            
            # Continue searching
            return True
        
        logger.info(f"Starting non-stop {colors['CYAN']}{mode.upper()}{colors['RESET']} mode")
        logger.debug(
            f"Balance refresh config: SOL every {balance_refresh_sol_every_sec}s "
            f"(0=every cycle), USDC every {balance_refresh_usdc_every_sec}s, "
            f"force refresh if >{balance_force_refresh_usdc_if_older_sec}s old"
        )
        logger.debug(
            f"SOL price refresh: every {sol_price_refresh_every_sec}s, "
            f"timeout: {sol_price_refresh_timeout_sec}s, "
            f"log every {sol_price_refresh_log_every_n} updates"
        )
        
        # Main non-stop loop
        while True:
            try:
                # Update balances based on timers
                t_now = time.monotonic()
                
                # Update SOL balance if needed
                if balance_refresh_sol_every_sec == 0 or (t_now - t_sol_last) >= balance_refresh_sol_every_sec:
                    new_sol_lamports = await fetch_sol_lamports(solana, balance_refresh_rpc_timeout_sec)
                    if new_sol_lamports is not None:
                        sol_lamports_last = new_sol_lamports
                        t_sol_last = t_now
                        logger.debug(f"SOL balance refreshed: {sol_lamports_last / 1e9:.4f} SOL")
                
                # Update USDC balance if needed
                if (t_now - t_usdc_last) >= balance_refresh_usdc_every_sec:
                    new_usdc_units = await fetch_usdc_units(
                        solana, wallet, usdc_mint, balance_refresh_rpc_timeout_sec
                    )
                    if new_usdc_units is not None:
                        usdc_units_last = new_usdc_units
                        t_usdc_last = t_now
                        logger.debug(f"USDC balance refreshed: {usdc_units_last / 1e6:.2f} USDC")
                    else:
                        logger.warning("Failed to refresh USDC balance, using stale value")
                
                # Update SOL→USDC price if needed
                if (t_now - t_sol_price_last) >= sol_price_refresh_every_sec:
                    try:
                        new_price = await asyncio.wait_for(
                            jupiter.get_sol_price_usdc(slippage_bps=10),
                            timeout=sol_price_refresh_timeout_sec
                        )
                        if new_price and new_price > 0:
                            # Save old price for logging
                            old_price = sol_price_usdc_last
                            
                            # Calculate price change percentage
                            price_change_pct = abs((new_price - old_price) / old_price * 100) if old_price > 0 else 0
                            price_update_count += 1
                            
                            # Update price in risk_manager and finder
                            risk_manager.config.sol_price_usdc = new_price
                            finder.sol_price_usdc = new_price
                            sol_price_usdc_last = new_price
                            t_sol_price_last = t_now
                            
                            # Log conditionally: if significant change (>0.5%) or every N updates
                            should_log = (price_change_pct >= 0.5) or (price_update_count % sol_price_refresh_log_every_n == 0)
                            if should_log:
                                if price_change_pct >= 0.5:
                                    logger.info(
                                        f"SOL price updated: {colors['YELLOW']}${new_price:.2f}{colors['RESET']} USDC "
                                        f"({colors['YELLOW']}{price_change_pct:+.2f}%{colors['RESET']} from ${old_price:.2f})"
                                    )
                                else:
                                    logger.debug(
                                        f"SOL price refreshed: {colors['YELLOW']}${new_price:.2f}{colors['RESET']} USDC "
                                        f"(update #{price_update_count})"
                                    )
                        else:
                            logger.debug("Failed to fetch SOL price (invalid response), keeping previous value")
                    except asyncio.TimeoutError:
                        logger.debug(f"SOL price fetch timed out after {sol_price_refresh_timeout_sec}s, keeping previous value")
                    except Exception as e:
                        logger.debug(f"Error fetching SOL price: {e}, keeping previous value")
                
                # Update risk manager with current balances
                balances_by_mint = {
                    sol_mint: sol_lamports_last,
                    usdc_mint: usdc_units_last
                }
                risk_manager.update_wallet_balances(balances_by_mint)
                
                # Reset fail streak on successful iteration
                fail_streak = 0
                
                # Find and process opportunities
                await finder.find_opportunities(
                    start_token,
                    test_amount,
                    max_opportunities=100,  # High limit, callback processes immediately
                    on_opportunity_found=on_opportunity_found,
                    amounts_by_mint=amounts_by_mint
                )
                
                # Idle sleep after scanning
                logger.debug(f"No more opportunities in current cycle, sleeping {loop_idle_sleep_sec}s")
                await asyncio.sleep(loop_idle_sleep_sec)
                
            except KeyboardInterrupt:
                logger.info("Stopping bot...")
                break
            except Exception as e:
                fail_streak += 1
                backoff_sec = min(
                    fail_backoff_base_sec * (2 ** (fail_streak - 1)),
                    fail_backoff_max_sec
                )
                logger.error(
                    f"{colors['RED']}Error in {mode} loop (streak: {fail_streak}):{colors['RESET']} {e}",
                    exc_info=True
                )
                logger.warning(f"Backing off for {backoff_sec:.1f}s before retry...")
                await asyncio.sleep(backoff_sec)
    
    try:
        if mode == 'scan':
            logger.info(f"Starting {colors['CYAN']}SCAN (read-only){colors['RESET']} mode")
            usdc_cycles = sum(1 for c in cycles if len(c) == 4 and c[0] == "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v" and c[-1] == "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v")
            sol_cycles = sum(1 for c in cycles if len(c) == 4 and c[0] == "So11111111111111111111111111111111111111112" and c[-1] == "So11111111111111111111111111111111111111112")
            logger.info(f"Optimized scan: paths={len(cycles)} ({usdc_cycles} USDC-based + {sol_cycles} SOL-based, all 3-leg) delay={quote_delay_seconds}s ({len(cycles) * 3} requests in ~{len(cycles) * 3 * quote_delay_seconds:.0f}s, rate-limited: {int(60/quote_delay_seconds)} req/min)")
            opportunities = await trader.scan_opportunities(
                start_token,
                test_amount,
                max_opportunities=10,
                sol_balance=sol_balance,
                usdc_balance=usdc_balance,
                amounts_by_mint=amounts_by_mint
            )
            
            count = len(opportunities)
            count_color = colors['GREEN'] if count > 0 else colors['RED']
            if count > 0:
                logger.info(f"Found {count_color}{count}{colors['CYAN']} profitable opportunities:{colors['RESET']}")
            
            if opportunities:
                for i, opp in enumerate(opportunities, 1):
                    cycle_str = format_cycle_with_symbols(opp.cycle, tokens_map)
                    base_mint = opp.cycle[0]
                    initial_display = trader._format_amount(opp.initial_amount, base_mint)
                    final_display = trader._format_amount(opp.final_amount, base_mint)
                    logger.info(
                        f"\n{colors['CYAN']}{i}. Cycle:{colors['RESET']} {cycle_str}"
                        f"\n   {colors['CYAN']}Profit:{colors['RESET']} {colors['GREEN']}{opp.profit_bps} bps{colors['RESET']} ({colors['YELLOW']}${opp.profit_usd:.4f}{colors['RESET']})"
                        f"\n   {colors['CYAN']}Initial:{colors['RESET']} {colors['GREEN']}{initial_display}{colors['RESET']}"
                        f"\n   {colors['CYAN']}Final:{colors['RESET']} {colors['GREEN']}{final_display}{colors['RESET']}"
                        f"\n   {colors['CYAN']}Price Impact:{colors['RESET']} {colors['GREEN']}{opp.price_impact_total:.2f}%{colors['RESET']}"
                    )
            else:
                logger.info(f"{colors['RED']}No profitable opportunities found{colors['RESET']}")
        
        elif mode == 'simulate':
            if not wallet:
                logger.error("Wallet required for simulation")
                return
            
            # Preflight check: verify swap-instructions availability
            logger.info(f"{colors['CYAN']}Preflight check: verifying swap-instructions availability...{colors['RESET']}")
            try:
                # Test with SOL -> USDC swap (small amount: 0.1 SOL)
                test_input_mint = sol_mint
                test_output_mint = usdc_mint
                test_amount = int(0.1 * 1e9)  # 0.1 SOL in lamports
                
                test_quote = await jupiter.get_quote(
                    input_mint=test_input_mint,
                    output_mint=test_output_mint,
                    amount=test_amount,
                    slippage_bps=50
                )
                
                if test_quote is None:
                    logger.error(
                        f"{colors['RED']}Preflight failed: Cannot get quote for {test_input_mint[:8]}... -> {test_output_mint[:8]}...{colors['RESET']}"
                    )
                    return
                
                # Try to get swap instructions
                user_pubkey = str(wallet.pubkey())
                instructions_resp = await jupiter.get_swap_instructions(
                    quote=test_quote,
                    user_public_key=user_pubkey,
                    priority_fee_lamports=0,
                    wrap_unwrap_sol=True,
                    dynamic_compute_unit_limit=True,
                    slippage_bps=50
                )
                
                if instructions_resp is None:
                    # Log detailed error information
                    jupiter_url = os.getenv('JUPITER_API_URL', 'not set (using fallback)')
                    jupiter_key = os.getenv('JUPITER_API_KEY', 'not set')
                    key_status = "set" if jupiter_key != "not set" else "not set"
                    
                    logger.error("=" * 60)
                    logger.error(f"{colors['RED']}PREFLIGHT FAILED: Swap-instructions unavailable{colors['RESET']}")
                    logger.error("=" * 60)
                    logger.error(f"Jupiter API URL: {colors['CYAN']}{jupiter_url}{colors['RESET']}")
                    logger.error(f"Jupiter API Key: {colors['CYAN']}{key_status}{colors['RESET']}")
                    logger.error("")
                    logger.error(f"{colors['YELLOW']}Solution:{colors['RESET']}")
                    logger.error("  1. Set JUPITER_API_URL=https://api.jup.ag (or use default)")
                    logger.error("  2. Set JUPITER_API_KEY=<your-api-key> (if required)")
                    logger.error("  3. Ensure Jupiter API supports instructions-only mode")
                    logger.error("")
                    logger.error(f"{colors['RED']}Cannot proceed with simulate mode without swap-instructions.{colors['RESET']}")
                    logger.error("=" * 60)
                    return
                
                # Validate response structure
                if not instructions_resp.swap_instruction:
                    logger.error(
                        f"{colors['RED']}Preflight failed: swap_instruction is missing in response{colors['RESET']}"
                    )
                    return
                
                if not instructions_resp.swap_instruction.program_id:
                    logger.error(
                        f"{colors['RED']}Preflight failed: swap_instruction.program_id is empty{colors['RESET']}"
                    )
                    return
                
                if instructions_resp.last_valid_block_height < 0:
                    logger.error(
                        f"{colors['RED']}Preflight failed: invalid last_valid_block_height: {instructions_resp.last_valid_block_height}{colors['RESET']}"
                    )
                    return
                
                logger.info(f"{colors['GREEN']}Preflight check passed: swap-instructions available{colors['RESET']}")
                
            except Exception as e:
                logger.error(
                    f"{colors['RED']}Preflight check failed with exception: {e}{colors['RESET']}",
                    exc_info=True
                )
                return
            
            # Get initial balances
            initial_sol_lamports = int(sol_balance * 1e9) if sol_balance > 0 else 0
            initial_usdc_units = int(usdc_balance * 1e6) if usdc_balance > 0 else 0
            
            await run_nonstop(
                mode='simulate',
                finder=finder,
                trader=trader,
                solana=solana,
                jupiter=jupiter,
                risk_manager=risk_manager,
                wallet=wallet,
                tokens_map=tokens_map,
                cycles=cycles,
                amounts_by_mint=amounts_by_mint,
                sol_mint=sol_mint,
                usdc_mint=usdc_mint,
                initial_sol_lamports=initial_sol_lamports,
                initial_usdc_units=initial_usdc_units,
                start_token=start_token,
                test_amount=test_amount
            )
        
        elif mode == 'live':
            if not wallet:
                logger.error("Wallet required for live trading")
                return
            
            # Preflight check: verify swap-instructions availability
            logger.info(f"{colors['CYAN']}Preflight check: verifying swap-instructions availability...{colors['RESET']}")
            try:
                # Test with SOL -> USDC swap (small amount: 0.1 SOL)
                test_input_mint = sol_mint
                test_output_mint = usdc_mint
                test_amount = int(0.1 * 1e9)  # 0.1 SOL in lamports
                
                test_quote = await jupiter.get_quote(
                    input_mint=test_input_mint,
                    output_mint=test_output_mint,
                    amount=test_amount,
                    slippage_bps=50
                )
                
                if test_quote is None:
                    logger.error(
                        f"{colors['RED']}Preflight failed: Cannot get quote for {test_input_mint[:8]}... -> {test_output_mint[:8]}...{colors['RESET']}"
                    )
                    return
                
                # Try to get swap instructions
                user_pubkey = str(wallet.pubkey())
                instructions_resp = await jupiter.get_swap_instructions(
                    quote=test_quote,
                    user_public_key=user_pubkey,
                    priority_fee_lamports=0,
                    wrap_unwrap_sol=True,
                    dynamic_compute_unit_limit=True,
                    slippage_bps=50
                )
                
                if instructions_resp is None:
                    # Log detailed error information
                    jupiter_url = os.getenv('JUPITER_API_URL', 'not set (using fallback)')
                    jupiter_key = os.getenv('JUPITER_API_KEY', 'not set')
                    key_status = "set" if jupiter_key != "not set" else "not set"
                    
                    logger.error("=" * 60)
                    logger.error(f"{colors['RED']}PREFLIGHT FAILED: Swap-instructions unavailable{colors['RESET']}")
                    logger.error("=" * 60)
                    logger.error(f"Jupiter API URL: {colors['CYAN']}{jupiter_url}{colors['RESET']}")
                    logger.error(f"Jupiter API Key: {colors['CYAN']}{key_status}{colors['RESET']}")
                    logger.error("")
                    logger.error(f"{colors['YELLOW']}Solution:{colors['RESET']}")
                    logger.error("  1. Set JUPITER_API_URL=https://api.jup.ag (or use default)")
                    logger.error("  2. Set JUPITER_API_KEY=<your-api-key> (if required)")
                    logger.error("  3. Ensure Jupiter API supports instructions-only mode")
                    logger.error("")
                    logger.error(f"{colors['RED']}Cannot proceed with live mode without swap-instructions.{colors['RESET']}")
                    logger.error("=" * 60)
                    return
                
                # Validate response structure
                if not instructions_resp.swap_instruction:
                    logger.error(
                        f"{colors['RED']}Preflight failed: swap_instruction is missing in response{colors['RESET']}"
                    )
                    return
                
                if not instructions_resp.swap_instruction.program_id:
                    logger.error(
                        f"{colors['RED']}Preflight failed: swap_instruction.program_id is empty{colors['RESET']}"
                    )
                    return
                
                if instructions_resp.last_valid_block_height < 0:
                    logger.error(
                        f"{colors['RED']}Preflight failed: invalid last_valid_block_height: {instructions_resp.last_valid_block_height}{colors['RESET']}"
                    )
                    return
                
                logger.info(f"{colors['GREEN']}Preflight check passed: swap-instructions available{colors['RESET']}")
                
            except Exception as e:
                logger.error(
                    f"{colors['RED']}Preflight check failed with exception: {e}{colors['RESET']}",
                    exc_info=True
                )
                return
            
            # STRICT WARNING: Live mode sends real transactions
            logger.warning("=" * 60)
            logger.warning("LIVE MODE ENABLED - REAL TRANSACTIONS WILL BE SENT!")
            logger.warning("=" * 60)
            
            # Additional confirmation check (can be removed if automated)
            logger.warning("Starting live mode in 3 seconds... Press Ctrl+C to cancel")
            await asyncio.sleep(3)
            
            # Get initial balances
            initial_sol_lamports = int(sol_balance * 1e9) if sol_balance > 0 else 0
            initial_usdc_units = int(usdc_balance * 1e6) if usdc_balance > 0 else 0
            
            await run_nonstop(
                mode='live',
                finder=finder,
                trader=trader,
                solana=solana,
                jupiter=jupiter,
                risk_manager=risk_manager,
                wallet=wallet,
                tokens_map=tokens_map,
                cycles=cycles,
                amounts_by_mint=amounts_by_mint,
                sol_mint=sol_mint,
                usdc_mint=usdc_mint,
                initial_sol_lamports=initial_sol_lamports,
                initial_usdc_units=initial_usdc_units,
                start_token=start_token,
                test_amount=test_amount
            )
        
        else:
            logger.error(f"Unknown mode: {mode}. Use: scan, simulate, or live")
    
    finally:
        # Cleanup
        await jupiter.close()
        await solana.close()
        logger.info(f"{colors['DIM']}Bot stopped{colors['RESET']}")


if __name__ == '__main__':
    asyncio.run(main())
