"""
Main entry point for Solana arbitrage bot.
"""
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

import dotenv
from solders.keypair import Keypair
from solders.pubkey import Pubkey

from .jupiter_client import JupiterClient
from .solana_client import SolanaClient
from .risk_manager import RiskManager, RiskConfig
from .arbitrage_finder import ArbitrageFinder
from .trader import Trader

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('arbitrage_bot.log')
    ]
)
logger = logging.getLogger(__name__)


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


async def main():
    """Main function."""
    logger.info("Starting Solana Arbitrage Bot")
    
    # Load configuration
    config = load_config()
    
    # Environment variables
    rpc_url = os.getenv('RPC_URL', 'https://api.mainnet-beta.solana.com')
    # Jupiter API URL: if not set, client will use fallback mechanism
    # If set explicitly, that URL will be used (no fallback)
    jupiter_api_url = os.getenv('JUPITER_API_URL')  # None = use fallback
    jupiter_api_key = os.getenv('JUPITER_API_KEY')  # Optional API key for authenticated requests
    mode = os.getenv('MODE', 'scan').lower()
    
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
            f"Slippage configuration: MAX_SLIPPAGE_BPS={risk_config.max_slippage_bps}, "
            f"SLIPPAGE_BPS={slippage_bps}"
        )
    
    # Load wallet
    wallet = load_wallet()
    if wallet is None and mode != 'scan':
        logger.error("Wallet required for simulate/live modes")
        return
    
    # Initialize clients
    jupiter = JupiterClient(jupiter_api_url, api_key=jupiter_api_key)
    solana = SolanaClient(rpc_url, wallet)
    
    # Try to fetch SOL price from Jupiter API
    sol_price_auto = await jupiter.get_sol_price_usdc(slippage_bps=10)
    if sol_price_auto and sol_price_auto > 0:
        sol_price_usdc = sol_price_auto
        logger.info(f"SOL price fetched from Jupiter API: ${sol_price_usdc:.2f} USDC")
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
    
    # Update wallet balance
    if wallet:
        balance = await solana.get_balance()
        risk_manager.update_wallet_balance(balance)
        logger.info(f"Wallet balance: {balance / 1e9:.4f} SOL")
    
    # Get tokens from config (minimal set: SOL, USDC, JUP, BONK)
    tokens_config = config.get('tokens', {})
    tokens = list(tokens_config.values())
    if not tokens:
        # Default minimal tokens (quota-safe)
        tokens = [
            "So11111111111111111111111111111111111111112",  # SOL
            "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
            "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",  # JUP
            "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",  # BONK
        ]
    
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
        sol_price_usdc=risk_config.sol_price_usdc
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
        slippage_bps=slippage_bps
    )
    
    # DIAGNOSTIC MODE: Test if Jupiter can return routes at all
    # Set DIAGNOSTIC_MODE=true in .env to enable
    diagnostic_mode = os.getenv('DIAGNOSTIC_MODE', 'false').lower() == 'true'
    if diagnostic_mode:
        logger.info("=" * 60)
        logger.info("DIAGNOSTIC MODE: Testing Jupiter route capability")
        logger.info("=" * 60)
        
        logger.info(f"Request: SOL → USDC")
        logger.info(f"Amount: 1.0 SOL")
        logger.info(f"Parameters: slippageBps={diagnostic_slippage_bps}, onlyDirectRoutes=false")
        
        quote = await jupiter.get_sol_price_usdc(
            slippage_bps=diagnostic_slippage_bps,
            return_full_quote=True
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
    
    available_balance = risk_manager.get_available_balance() if wallet else 1_000_000_000  # 1 SOL default
    # Convert max_position_size_absolute_usdc back to SOL for test_amount calculation
    max_position_absolute_sol_calc = risk_config.max_position_size_absolute_usdc / risk_config.sol_price_usdc
    test_amount = min(
        int(available_balance * risk_config.max_position_size_percent / 100),
        int(max_position_absolute_sol_calc * 1e9)
    )
    
    try:
        if mode == 'scan':
            logger.info("Mode: SCAN (read-only)")
            logger.info("Minimal scan: tokens=4 cycles=6 (quota-safe)")
            opportunities = await trader.scan_opportunities(
                start_token,
                test_amount,
                max_opportunities=10
            )
            
            if opportunities:
                logger.info(f"\nFound {len(opportunities)} profitable opportunities:")
                for i, opp in enumerate(opportunities, 1):
                    logger.info(
                        f"\n{i}. Cycle: {' -> '.join(opp.cycle)}"
                        f"\n   Profit: {opp.profit_bps} bps (${opp.profit_usd:.4f})"
                        f"\n   Initial: {opp.initial_amount / 1e9:.6f} SOL"
                        f"\n   Final: {opp.final_amount / 1e9:.6f} SOL"
                        f"\n   Price Impact: {opp.price_impact_total:.2f}%"
                    )
            else:
                logger.info("No profitable opportunities found")
        
        elif mode == 'simulate':
            logger.info("Mode: SIMULATE")
            if not wallet:
                logger.error("Wallet required for simulation")
                return
            
            opportunities = await trader.scan_opportunities(
                start_token,
                test_amount,
                max_opportunities=5
            )
            
            for opp in opportunities:
                user_pubkey = str(wallet.pubkey())
                success, error, sim_result = await trader.simulate_opportunity(opp, user_pubkey)
                
                if success:
                    logger.info(f"Simulation successful for cycle: {' -> '.join(opp.cycle)}")
                    logger.debug(f"Simulation result: {sim_result}")
                else:
                    logger.warning(f"Simulation failed: {error}")
        
        elif mode == 'live':
            logger.info("Mode: LIVE (real trading)")
            if not wallet:
                logger.error("Wallet required for live trading")
                return
            
            # STRICT WARNING: Live mode sends real transactions
            logger.warning("=" * 60)
            logger.warning("LIVE MODE ENABLED - REAL TRANSACTIONS WILL BE SENT!")
            logger.warning("=" * 60)
            
            # Additional confirmation check (can be removed if automated)
            import time
            logger.warning("Starting live mode in 3 seconds... Press Ctrl+C to cancel")
            await asyncio.sleep(3)
            
            # Continuous loop
            while True:
                try:
                    # Update balance
                    balance = await solana.get_balance()
                    risk_manager.update_wallet_balance(balance)
                    
                    # Find opportunities
                    opportunities = await trader.scan_opportunities(
                        start_token,
                        test_amount,
                        max_opportunities=1
                    )
                    
                    if opportunities:
                        opp = opportunities[0]
                        user_pubkey = str(wallet.pubkey())
                        
                        # Execute
                        success, error, tx_sig = await trader.execute_opportunity(opp, user_pubkey)
                        
                        if success:
                            logger.info(f"Successfully executed arbitrage: {tx_sig}")
                        else:
                            logger.warning(f"Execution failed: {error}")
                    else:
                        logger.info("No opportunities found, waiting...")
                    
                    # Wait before next iteration
                    await asyncio.sleep(5)
                    
                except KeyboardInterrupt:
                    logger.info("Stopping bot...")
                    break
                except Exception as e:
                    logger.error(f"Error in live loop: {e}")
                    await asyncio.sleep(5)
        
        else:
            logger.error(f"Unknown mode: {mode}. Use: scan, simulate, or live")
    
    finally:
        # Cleanup
        await jupiter.close()
        await solana.close()
        logger.info("Bot stopped")


if __name__ == '__main__':
    asyncio.run(main())
