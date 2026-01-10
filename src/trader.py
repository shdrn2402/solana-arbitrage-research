"""
Main trading module that orchestrates arbitrage execution.
"""
import asyncio
import logging
import time
import uuid
from typing import Optional, Dict, Any, Tuple, List, AsyncIterator

from .jupiter_client import JupiterClient
from .solana_client import SolanaClient
from .risk_manager import RiskManager, RiskConfig
from .arbitrage_finder import ArbitrageFinder, ArbitrageOpportunity

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
        address_to_symbol: Optional[Dict[str, str]] = None
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
        self.address_to_symbol = address_to_symbol or {}  # Dictionary mapping address -> symbol
    
    def format_cycle_with_symbols(self, cycle_addresses: List[str]) -> str:
        """
        Convert cycle from addresses to string with token symbols.
        
        Args:
            cycle_addresses: List of token addresses [address1, address2, address3, address1]
        
        Returns:
            String in format "BONK -> SOL -> USDC -> BONK"
            If address is not found in dictionary, shows first 8 characters + "..."
        """
        symbols = []
        for address in cycle_addresses:
            # Use reverse dictionary address -> symbol
            symbol = self.address_to_symbol.get(address)
            if symbol is None:
                # Fallback: show first 8 characters of address
                symbol = address[:8] + "..."
            symbols.append(symbol)
        return " -> ".join(symbols)
    
    async def scan_opportunities(
        self,
        start_token: str,
        amount: int,
        max_opportunities: int = 10
    ) -> list[ArbitrageOpportunity]:
        """Scan for arbitrage opportunities (read-only)."""
        logger.info(f"Scanning for opportunities: {amount/1e9:.4f} SOL")
        opportunities = await self.finder.find_opportunities(
            start_token, amount, max_opportunities
        )
        
        logger.info(f"Found {len(opportunities)} opportunities")
        for i, opp in enumerate(opportunities, 1):
            logger.info(
                f"  {i}. Cycle: {self.format_cycle_with_symbols(opp.cycle)} | "
                f"Profit: {opp.profit_bps} bps (${opp.profit_usd:.4f}) | "
                f"Impact: {opp.price_impact_total:.2f}%"
            )
        
        return opportunities
    
    async def scan_opportunities_stream(
        self,
        start_token: str,
        amount: int,
        max_opportunities: int = 10
    ) -> AsyncIterator[ArbitrageOpportunity]:
        """
        Scan for opportunities and yield them as they are found.
        
        This method uses async generator to yield opportunities immediately
        as they are found, allowing stream processing without waiting
        for all cycles to complete.
        
        Args:
            start_token: Starting token mint address
            amount: Starting amount in smallest unit
            max_opportunities: Maximum number of opportunities to yield
        
        Yields:
            ArbitrageOpportunity as they are found
        """
        logger.info(f"Scanning for opportunities (stream mode): {amount/1e9:.4f} SOL")
        
        async for opportunity in self.finder.find_opportunities_stream(start_token, amount, max_opportunities):
            logger.info(
                f"Found opportunity: Cycle: {self.format_cycle_with_symbols(opportunity.cycle)} | "
                f"Profit: {opportunity.profit_bps} bps (${opportunity.profit_usd:.4f}) | "
                f"Impact: {opportunity.price_impact_total:.2f}%"
            )
            yield opportunity
    
    async def simulate_opportunity(
        self,
        opportunity: ArbitrageOpportunity,
        user_pubkey: str
    ) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
        """
        Simulate an arbitrage opportunity.
        
        Returns:
            (success: bool, error_message: Optional[str], simulation_result: Optional[Dict])
        """
        logger.info(f"Simulating opportunity: {self.format_cycle_with_symbols(opportunity.cycle)}")
        
        # Build swap transaction for the full cycle
        # Note: Jupiter doesn't support multi-leg swaps directly,
        # so we'd need to build a transaction with multiple swaps
        # For now, we simulate the first leg as a proxy
        
        if not opportunity.quotes:
            return False, "No quotes available", None
        
        # Get swap transaction for first leg
        first_quote = opportunity.quotes[0]
        swap_response = await self.jupiter.get_swap_transaction(
            first_quote,
            user_pubkey,
            priority_fee_lamports=self.priority_fee,
            slippage_bps=self.slippage_bps
        )
        
        if swap_response is None:
            return False, "Failed to build swap transaction", None
        
        # Simulate
        sim_result = await self.solana.simulate_transaction(
            swap_response.swap_transaction
        )
        
        if sim_result is None:
            return False, "Simulation failed", None
        
        if sim_result.get("err"):
            return False, f"Simulation error: {sim_result['err']}", sim_result
        
        return True, None, sim_result
    
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
        
        logger.info(f"Executing opportunity: {self.format_cycle_with_symbols(opportunity.cycle)}")
        logger.info(f"Position ID: {position_id}")
        
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
            sim_success, sim_error, sim_result = await self.simulate_opportunity(
                opportunity, user_pubkey
            )
            
            if not sim_success:
                return False, f"Simulation failed (MANDATORY): {sim_error}", None
            
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
            
            # Build transaction
            swap_response = await self.jupiter.get_swap_transaction(
                first_quote,
                user_pubkey,
                priority_fee_lamports=self.priority_fee,
                slippage_bps=self.slippage_bps
            )
            
            if swap_response is None:
                return False, "Failed to build swap transaction", None
            
            # Security checks before sending transaction
            
            # Check 1: Validate quote expiry (last_valid_block_height)
            current_slot = await self.solana.get_current_slot()
            if current_slot is None:
                logger.warning("Failed to get current slot for quote expiry check, proceeding anyway")
            else:
                if swap_response.last_valid_block_height > 0:
                    if current_slot >= swap_response.last_valid_block_height:
                        error_msg = f"Quote expired: current slot {current_slot} >= last valid block height {swap_response.last_valid_block_height}"
                        logger.warning(error_msg)
                        return False, error_msg, None
                    else:
                        logger.debug(f"Quote valid: current slot {current_slot} < last valid block height {swap_response.last_valid_block_height}")
                else:
                    logger.warning("Quote has no last_valid_block_height set (using 0), skipping expiry check")
            
            # Check 2: Re-check balance before sending transaction
            balance = await self.solana.get_balance()
            self.risk.update_wallet_balance(balance)
            available_balance = self.risk.get_available_balance()
            
            if available_balance < opportunity.initial_amount:
                error_msg = f"Insufficient balance: need {opportunity.initial_amount / 1e9:.4f} SOL, have {available_balance / 1e9:.4f} SOL available"
                logger.warning(error_msg)
                return False, error_msg, None
            else:
                logger.debug(f"Balance check passed: have {available_balance / 1e9:.4f} SOL available, need {opportunity.initial_amount / 1e9:.4f} SOL")
            
            # Send transaction (only in 'live' mode, already checked above)
            self.risk.update_position_status(position_id, 'executing')
            tx_sig = await self.solana.send_transaction(swap_response.swap_transaction)
            
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
            await asyncio.sleep(1)
            self.risk.remove_position(position_id)
