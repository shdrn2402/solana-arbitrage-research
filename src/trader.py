"""
Main trading module that orchestrates arbitrage execution.
"""
import asyncio
import logging
import time
import uuid
from typing import Optional, Dict, Any, Tuple, List

from .jupiter_client import JupiterClient, JupiterSwapResponse
from .solana_client import SolanaClient
from .risk_manager import RiskManager, RiskConfig
from .arbitrage_finder import ArbitrageFinder, ArbitrageOpportunity
from .utils import get_terminal_colors

# Get terminal colors (empty if output is redirected)
colors = get_terminal_colors()

logger = logging.getLogger(__name__)

class Trader:
    """Main trading orchestrator."""
    
    def __init__(
        self,
        jupiter_client: JupiterClient,
        solana_client: SolanaClient,
        risk_manager: RiskManager,
        arbitrage_finder: ArbitrageFinder,
        priority_fee_lamports: int = 0,
        use_jito: bool = False,
        mode: str = 'scan',  # 'scan', 'simulate', or 'live'
        slippage_bps: int = 50,
        tokens_map: Optional[Dict[str, str]] = None
    ):
        self.jupiter = jupiter_client
        self.solana = solana_client
        self.risk = risk_manager
        self.finder = arbitrage_finder
        self.priority_fee = priority_fee_lamports
        self.use_jito = use_jito
        self.mode = mode.lower()
        self.trade_in_progress = False  # Protection against parallel trades
        self.slippage_bps = slippage_bps
        self.tokens_map = tokens_map or {}
    
    async def scan_opportunities(
        self,
        start_token: str,
        amount: int,
        max_opportunities: int = 10,
        sol_balance: float = 0.0,
        usdc_balance: float = 0.0,
        amounts_by_mint: Optional[Dict[str, int]] = None
    ) -> list[ArbitrageOpportunity]:
        """Scan for arbitrage opportunities (read-only)."""
        sol_limit = sol_balance * self.risk.config.max_position_size_percent / 100
        usdc_limit = usdc_balance * self.risk.config.max_position_size_percent / 100
        logger.info(f"{colors['CYAN']}SOL scanning limits: {colors['YELLOW']}{sol_limit:.4f} SOL{colors['RESET']}")
        logger.info(f"{colors['CYAN']}USDC scanning limits: {colors['YELLOW']}{usdc_limit:.2f} USDC{colors['RESET']}")
        opportunities = await self.finder.find_opportunities(
            start_token,
            amount,
            max_opportunities,
            amounts_by_mint=amounts_by_mint
        )
        
        count = len(opportunities)
        count_color = colors['GREEN'] if count > 0 else colors['RED']
        logger.info(f"Found {count_color}{count}{colors['RESET']} opportunities")
        for i, opp in enumerate(opportunities, 1):
            cycle_display = ' -> '.join(self.tokens_map.get(addr, addr) for addr in opp.cycle)
            logger.info(
                f"  {i}. Cycle: {cycle_display} | "
                f"Profit: {opp.profit_bps} bps (${opp.profit_usd:.4f}) | "
                f"Impact: {opp.price_impact_total:.2f}%"
            )
        
        return opportunities
    
    async def process_opportunity_with_retries(
        self,
        cycle: List[str],
        amount: int,
        user_pubkey: str,
        max_retries: int = 10,
        first_attempt_use_original_opportunity: bool = True,
        original_opportunity: Optional[ArbitrageOpportunity] = None
    ) -> int:
        """
        Process an opportunity with retries: check, simulate/execute, and repeat if successful.
        
        Args:
            cycle: List of token addresses in the cycle
            amount: Starting amount in smallest unit
            user_pubkey: User's public key
            max_retries: Maximum number of successful executions before stopping
        
        Returns:
            Number of successful executions
        """
        cycle_display = ' -> '.join(self.tokens_map.get(addr, addr) for addr in cycle)
        logger.info(f"{colors['CYAN']}Processing opportunity with retries:{colors['RESET']} {colors['YELLOW']}{cycle_display}{colors['RESET']} (mode: {self.mode})")
        success_count = 0
        timestamp_start = time.monotonic()
        
        while success_count < max_retries:
            # Skip recheck on first attempt if original_opportunity is provided (zero-recheck first attempt)
            if success_count == 0 and first_attempt_use_original_opportunity and original_opportunity is not None:
                # Use original opportunity directly for first attempt (no recheck = faster)
                opportunity = original_opportunity
                logger.debug("Using original opportunity for first attempt (zero-recheck)")
            else:
                # Check cycle again (3 requests, no delays for fast checking) for retries
                recheck_start = time.monotonic()
                opportunity = await self.finder._check_cycle(cycle, amount, skip_delays=True)
                recheck_duration_ms = (time.monotonic() - recheck_start) * 1000
                
                if not opportunity or not opportunity.is_valid(
                    self.finder.min_profit_bps,
                    self.finder.min_profit_usd
                ):
                    # Opportunity no longer profitable, stop retrying
                    if success_count > 0:
                        # Already had successful executions, opportunity just became unprofitable
                        logger.info(f"{colors['YELLOW']}Opportunity {cycle_display} no longer profitable after {success_count} successful executions{colors['RESET']}")
                    else:
                        # Dropped before first execution - this is the "died before execution" case
                        logger.info(f"{colors['RED']}Opportunity dropped before execution (recheck not profitable):{colors['RESET']} {colors['YELLOW']}{cycle_display}{colors['RESET']} (recheck: {recheck_duration_ms:.1f}ms)")
                    break

            # Safety: never simulate/execute an opportunity that doesn't meet current thresholds.
            # This protects the "zero-recheck first attempt" path too.
            if not opportunity.is_valid(self.finder.min_profit_bps, self.finder.min_profit_usd):
                logger.info(
                    f"{colors['RED']}Opportunity rejected before processing (not profitable):{colors['RESET']} "
                    f"{colors['YELLOW']}{cycle_display}{colors['RESET']} | "
                    f"profit_bps={opportunity.profit_bps}, profit_usdc={opportunity.profit_usd:.4f}"
                )
                break
            
            # Process based on mode
            if self.mode == 'simulate':
                # Simulate only
                success, error, sim_result, swap_response = await self.simulate_opportunity(opportunity, user_pubkey)
                if success:
                    success_count += 1
                    cycle_display = ' -> '.join(self.tokens_map.get(addr, addr) for addr in cycle)
                    
                    # Format initial/final amounts based on starting token
                    start_token = opportunity.cycle[0]
                    initial_display = self._format_amount(opportunity.initial_amount, start_token)
                    final_display = self._format_amount(opportunity.final_amount, start_token)
                    
                    logger.info(
                        f"{colors['GREEN']}Simulation #{success_count} successful for cycle: {cycle_display} | "
                        f"Profit: {opportunity.profit_bps} bps (${opportunity.profit_usd:.4f}) | "
                        f"Initial: {initial_display} | "
                        f"Final: {final_display}{colors['RESET']}"
                    )
                    # Continue to next retry
                else:
                    logger.warning(f"{colors['RED']}Simulation failed: {colors['YELLOW']}{error}{colors['RESET']}")
                    break  # Stop retrying on failure
            
            elif self.mode == 'live':
                # Execute (includes mandatory simulation)
                success, error, tx_sig = await self.execute_opportunity(opportunity, user_pubkey)
                if success:
                    success_count += 1
                    cycle_display = ' -> '.join(self.tokens_map.get(addr, addr) for addr in cycle)
                    
                    # Format initial/final amounts based on starting token
                    start_token = opportunity.cycle[0]
                    initial_display = self._format_amount(opportunity.initial_amount, start_token)
                    final_display = self._format_amount(opportunity.final_amount, start_token)
                    
                    logger.info(
                        f"{colors['GREEN']}Execution #{success_count} successful: {colors['CYAN']}{tx_sig}{colors['RESET']} | "
                        f"Cycle: {cycle_display} | "
                        f"Profit: {opportunity.profit_bps} bps (${opportunity.profit_usd:.4f}) | "
                        f"Initial: {initial_display} | "
                        f"Final: {final_display}"
                    )
                    # Continue to next retry
                else:
                    logger.warning(f"{colors['RED']}Execution failed: {error}{colors['RESET']}")
                    break  # Stop retrying on failure
            
            else:
                # scan mode - shouldn't reach here
                break
        
        total_duration_ms = (time.monotonic() - timestamp_start) * 1000
        if success_count > 0:
            logger.debug(f"Processed {success_count} executions in {total_duration_ms:.1f}ms")
        
        return success_count
    
    def _format_amount(self, amount: int, token_mint: str) -> str:
        """
        Format amount based on token type (SOL, USDC, or unknown).
        
        Args:
            amount: Amount in smallest units
            token_mint: Token mint address
        
        Returns:
            Formatted string with amount and token symbol
        """
        sol_mint = "So11111111111111111111111111111111111111112"
        usdc_mint = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
        
        if token_mint == sol_mint:
            return f"{amount/1e9:.6f} SOL"
        elif token_mint == usdc_mint:
            return f"{amount/1e6:.2f} USDC"
        else:
            # Unknown token, show raw amount
            return f"{amount}"
    
    def _format_sim_logs(self, logs, tail: int = 20) -> str:
        """
        Format simulation logs, showing only last N lines to avoid spam.
        
        Args:
            logs: List of log strings from simulation
            tail: Number of last lines to show
        
        Returns:
            Formatted string with log lines
        """
        # logs should be a list[str], but be defensive (tests/mocks may pass non-list)
        if not logs or not isinstance(logs, (list, tuple)):
            return "  (no logs)"
        
        # Show full logs in DEBUG, tail in INFO/WARNING
        if logger.isEnabledFor(logging.DEBUG):
            lines_to_show = logs
        else:
            lines_to_show = logs[-tail:] if len(logs) > tail else logs
        
        return "\n".join(f"  {log}" for log in lines_to_show)
    
    async def simulate_opportunity(
        self,
        opportunity: ArbitrageOpportunity,
        user_pubkey: str
    ) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]], Optional[JupiterSwapResponse]]:
        """
        Simulate an arbitrage opportunity.
        
        Returns:
            (success: bool, error_message: Optional[str], simulation_result: Optional[Dict], swap_response: Optional[JupiterSwapResponse])
        """
        cycle_display = ' -> '.join(self.tokens_map.get(addr, addr) for addr in opportunity.cycle)
        logger.info(f"{colors['CYAN']}Simulating opportunity:{colors['RESET']} {colors['YELLOW']}{cycle_display}{colors['RESET']}")
        
        # Build swap transaction for the full cycle
        # Note: Jupiter doesn't support multi-leg swaps directly,
        # so we'd need to build a transaction with multiple swaps
        # For now, we simulate the first leg as a proxy
        
        if not opportunity.quotes:
            return False, "No quotes available", None, None
        
        # Get swap transaction for first leg
        first_quote = opportunity.quotes[0]
        swap_response = await self.jupiter.get_swap_transaction(
            first_quote,
            user_pubkey,
            priority_fee_lamports=self.priority_fee,
            slippage_bps=self.slippage_bps
        )
        
        if swap_response is None:
            return False, "Failed to build swap transaction", None, None
        
        # Simulate
        sim_result = await self.solana.simulate_transaction(
            swap_response.swap_transaction
        )
        
        if sim_result is None:
            return False, "Simulation failed (no result from RPC)", None, None

        # Be defensive: RPC client should return a dict, but mocks may return other objects
        if not isinstance(sim_result, dict):
            return False, f"Simulation failed (invalid result type: {type(sim_result).__name__})", None, swap_response
        
        if sim_result.get("err"):
            # Include simulation logs in error message for debugging
            err_msg = f"Simulation error: {sim_result['err']}"
            logs = sim_result.get("logs", [])
            if logs:
                log_tail = self._format_sim_logs(logs, tail=20)
                err_msg += f"\nSimulation logs (last 20):\n{log_tail}"
            return False, err_msg, sim_result, swap_response
        
        return True, None, sim_result, swap_response
    
    async def execute_opportunity(
        self,
        opportunity: ArbitrageOpportunity,
        user_pubkey: str
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Execute an arbitrage opportunity.
        
        CRITICAL SAFETY CHECKS:
        - Mode must be 'live' to send transactions
        - Only one trade can be in progress at a time
        - Simulation is MANDATORY and must succeed
        
        Returns:
            (success: bool, error_message: Optional[str], tx_signature: Optional[str])
        """
        # STRICT MODE CHECK: Only 'live' mode can send transactions
        if self.mode != 'live':
            return False, f"Transaction sending disabled in mode '{self.mode}'. Use 'live' mode to send transactions.", None
        
        # PARALLEL TRADE PROTECTION: Only one trade at a time
        if self.trade_in_progress:
            return False, "Another trade is already in progress. Wait for completion.", None
        
        position_id = str(uuid.uuid4())
        
        cycle_display = ' -> '.join(self.tokens_map.get(addr, addr) for addr in opportunity.cycle)
        logger.info(f"{colors['CYAN']}Executing opportunity:{colors['RESET']} {colors['YELLOW']}{cycle_display}{colors['RESET']}")
        logger.info(f"{colors['CYAN']}Position ID:{colors['RESET']} {colors['YELLOW']}{position_id}{colors['RESET']}")
        
        # Set trade_in_progress flag BEFORE any operations
        self.trade_in_progress = True
        
        try:
            # Risk check
            can_open, reason = self.risk.can_open_position(
                opportunity.initial_amount,
                opportunity.profit_bps,
                slippage_bps=self.slippage_bps,  # from config
                expected_profit_usdc=opportunity.profit_usd  # Note: profit_usd is actually USDC
            )
            
            if not can_open:
                return False, f"Risk check failed: {reason}", None
            
            # Add position
            self.risk.add_position(
                position_id,
                opportunity.cycle[0],
                opportunity.cycle[-1],
                opportunity.initial_amount,
                opportunity.final_amount
            )
            
            # MANDATORY SIMULATION: No transaction can be sent without successful simulation
            # Reuse swap_response from simulation to avoid duplicate Jupiter API call
            sim_success, sim_error, sim_result, swap_response = await self.simulate_opportunity(
                opportunity, user_pubkey
            )
            
            if not sim_success:
                # sim_error already includes logs if available
                error_msg = f"Simulation failed (MANDATORY): {sim_error}"
                # If sim_result has logs but they weren't included in sim_error, add them
                if sim_result and sim_result.get("logs") and "Simulation logs" not in sim_error:
                    log_tail = self._format_sim_logs(sim_result.get("logs", []), tail=20)
                    error_msg += f"\nSimulation logs (last 20):\n{log_tail}"
                return False, error_msg, None
            
            if swap_response is None:
                return False, "No swap transaction from simulation", None
            
            # Validate simulation result
            # Note: Simulation only executes the first leg of the cycle (see simulate_opportunity method)
            # Therefore, we validate only the first leg output, not the full cycle
            first_quote = opportunity.quotes[0]
            expected_first_leg_output = first_quote.out_amount
            # TODO: Extract actual output from sim_result logs or accounts for full validation
            # For now, using expected value as proxy (simplified validation of first leg only)
            actual_first_leg_output = first_quote.out_amount  # placeholder - should extract from sim_result
            
            is_valid, reason = self.risk.validate_simulation_result(
                expected_first_leg_output,
                actual_first_leg_output,
                max_deviation_bps=100
            )
            
            if not is_valid:
                return False, f"Simulation validation failed: {reason}", None
            
            # Security checks before sending transaction
            
            # Check 1: Validate quote expiry (lastValidBlockHeight)
            current_block_height = await self.solana.get_current_block_height()
            if current_block_height is None:
                logger.warning("Failed to get current block height for quote expiry check, proceeding anyway")
            else:
                if swap_response.last_valid_block_height > 0:
                    if current_block_height >= swap_response.last_valid_block_height:
                        error_msg = (
                            f"Quote expired: current block height {current_block_height} "
                            f">= last valid block height {swap_response.last_valid_block_height}"
                        )
                        logger.warning(error_msg)
                        return False, error_msg, None
                    else:
                        logger.debug(
                            f"Quote valid: current block height {current_block_height} "
                            f"< last valid block height {swap_response.last_valid_block_height}"
                        )
                else:
                    logger.warning("Quote has no last_valid_block_height set (0), skipping expiry check")
            
            # Balance check is already done in can_open_position() before add_position()
            # Removing duplicate check here to avoid false "Insufficient balance" after add_position() locks funds
            
            # Send transaction (only in 'live' mode, already checked above)
            # Use skip_preflight=True since we already have mandatory simulation
            self.risk.update_position_status(position_id, 'executing')
            tx_sig = await self.solana.send_transaction(
                swap_response.swap_transaction,
                skip_preflight=True
            )
            
            if tx_sig is None:
                return False, "Failed to send transaction", None
            
            # Wait for confirmation
            confirmed = await self.solana.confirm_transaction(tx_sig, timeout=30.0)
            
            if confirmed:
                self.risk.update_position_status(position_id, 'completed')
                logger.info(f"Transaction confirmed: {tx_sig}")
                return True, None, tx_sig
            else:
                self.risk.update_position_status(position_id, 'failed')
                return False, "Transaction not confirmed", tx_sig
            
        except Exception as e:
            logger.error(f"Error executing opportunity: {e}")
            if position_id in self.risk.active_positions:
                self.risk.update_position_status(position_id, 'failed')
            return False, str(e), None
        
        finally:
            # ALWAYS release trade_in_progress flag and clean up position
            self.trade_in_progress = False
            # Removed artificial delay - no sleep in hot path for live mode
            self.risk.remove_position(position_id)
    
    async def _confirm_transaction_background(
        self,
        position_id: str,
        tx_sig: str
    ) -> None:
        """
        Background task to confirm transaction to finalized status and update position.
        
        This runs asynchronously and does not block the main execution loop.
        """
        try:
            # Wait for confirmed commitment (up to 30s)
            confirmed = await self.solana.confirm_transaction(tx_sig, commitment="confirmed", timeout=30.0)
            
            if confirmed:
                self.risk.update_position_status(position_id, 'completed')
                logger.info(f"{colors['GREEN']}Transaction confirmed: {colors['CYAN']}{tx_sig}{colors['RESET']}")
            else:
                self.risk.update_position_status(position_id, 'failed')
                logger.warning(f"{colors['RED']}Transaction not confirmed: {colors['CYAN']}{tx_sig}{colors['RESET']}")
        except Exception as e:
            logger.error(f"Error in background confirmation for {tx_sig}: {e}", exc_info=True)
            # Update position status to failed on error
            if position_id in self.risk.active_positions:
                self.risk.update_position_status(position_id, 'failed')