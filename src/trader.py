"""
Main trading module that orchestrates arbitrage execution.
"""
import asyncio
import logging
import os
import time
import uuid
import base64
import hashlib
from typing import Optional, Dict, Any, Tuple, List, Set
from dataclasses import dataclass

from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.hash import Hash
from solders.instruction import Instruction, AccountMeta
from solders.message import MessageV0
from solders.transaction import VersionedTransaction
from solders.address_lookup_table_account import AddressLookupTableAccount

from .jupiter_client import (
    JupiterClient,
    JupiterSwapResponse,
    JupiterSwapInstructionsResponse,
    SwapInstruction,
    SwapAccountMeta
)
from .solana_client import SolanaClient
from .risk_manager import RiskManager, RiskConfig
from .arbitrage_finder import ArbitrageFinder, ArbitrageOpportunity
from .utils import get_terminal_colors

# Get terminal colors (empty if output is redirected)
colors = get_terminal_colors()

logger = logging.getLogger(__name__)


@dataclass
class PreparedBundle:
    """
    Prepared bundle containing everything needed for live execution.
    
    CRITICAL: The VersionedTransaction MUST be fully assembled AND SIGNED
    with valid signatures, ready for immediate network submission.
    No "sign later", "wallet will sign before send", or "add signatures later".
    
    After successful send, the bundle is considered "consumed" and must not be reused.
    """
    opportunity: ArbitrageOpportunity  # Opportunity with quotes, amounts, dex1/dex2
    leg_instructions: List[JupiterSwapInstructionsResponse]  # Exactly 2 swap instructions (already obtained)
    route_signature: str  # Route signature for negative cache
    min_last_valid_block_height: Optional[int]  # Minimum last valid block height from VT build
    versioned_transaction: VersionedTransaction  # Fully assembled AND SIGNED VT (v0 + ALTs), ready for immediate send
    meta: Dict[str, Any]  # Optional metadata: raw_size_bytes, alts_count, plan_id, etc.
    
    def __post_init__(self):
        """Validate bundle integrity."""
        if len(self.leg_instructions) != 2:
            raise ValueError(f"PreparedBundle must have exactly 2 leg_instructions, got {len(self.leg_instructions)}")
        if not isinstance(self.versioned_transaction, VersionedTransaction):
            raise ValueError(f"PreparedBundle.versioned_transaction must be VersionedTransaction, got {type(self.versioned_transaction)}")
        # Verify VT is signed (has signatures)
        if not self.versioned_transaction.signatures or len(self.versioned_transaction.signatures) == 0:
            raise ValueError("PreparedBundle.versioned_transaction must be signed (have signatures)")


def _extract_dex_from_quote(quote) -> str:
    """
    Extract DEX name from quote's routePlan.
    
    For 1-hop routes, extracts ammKey from the single hop's swapInfo.
    
    Args:
        quote: JupiterQuote with routePlan
    
    Returns:
        DEX name (shortened) or "Unknown" if not found
    """
    route_plan = quote.route_plan or []
    if not route_plan or len(route_plan) == 0:
        return "Unknown"
    
    # For 1-hop routes, take the first (and only) hop
    hop = route_plan[0] if isinstance(route_plan, list) else None
    if not isinstance(hop, dict):
        return "Unknown"
    
    swap_info = hop.get('swapInfo', {})
    amm_key = swap_info.get('ammKey') or hop.get('ammKey', '')
    
    if not amm_key:
        return "Unknown"
    
    # Map common AMM keys to readable names
    amm_map = {
        '675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8': 'Raydium',
        '9W959DqEETiGZocYWCQPaJ6sBmUzgfxXfqGeTEdp3aQP': 'Orca',
        'whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc': 'Orca Whirlpool',
        'CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK': 'Raydium CLMM',
    }
    
    # Try to find readable name, otherwise use first 8 chars of ammKey
    dex_name = amm_map.get(amm_key, amm_key[:8] + '...')
    return dex_name


def _format_execution_plan_with_dex(opportunity: ArbitrageOpportunity, tokens_map: Dict[str, str]) -> str:
    """
    Format execution plan with DEX per leg: "USDC->SOL (Raydium) -> USDC (Orca)"
    
    Args:
        opportunity: ArbitrageOpportunity with execution_plan and quotes
        tokens_map: Mapping from mint address to token symbol
    
    Returns:
        Formatted string with tokens and DEX per leg
    """
    plan = opportunity.execution_plan
    quotes = opportunity.quotes
    
    if len(plan.legs) != len(quotes):
        # Fallback: just show cycle without DEX
        return ' -> '.join(tokens_map.get(addr, addr[:8]) for addr in plan.cycle_mints)
    
    parts = []
    for i, (leg, quote) in enumerate(zip(plan.legs, quotes)):
        from_symbol = tokens_map.get(leg.from_mint, leg.from_mint[:8])
        to_symbol = tokens_map.get(leg.to_mint, leg.to_mint[:8])
        dex = _extract_dex_from_quote(quote)
        
        if i == 0:
            # First leg: show from -> to (DEX)
            parts.append(f"{from_symbol}->{to_symbol} ({dex})")
        else:
            # Subsequent legs: show -> to (DEX)
            parts.append(f"->{to_symbol} ({dex})")
    
    return ' '.join(parts)


class RouteNegativeCache:
    """
    TTL-based negative cache for unstable Jupiter routes.
    
    Caches routes that fail with:
    - Runtime 6024 + SharedAccountsRoute error (InstructionErrorCustom(6024))
    - Atomic VT size overflow (> 1232 bytes)
    
    to avoid repeated simulate/RPC calls for the same failing route.
    """
    
    def __init__(self, ttl_seconds: int = 600, ttl_size_overflow_seconds: int = 600, ttl_runtime_6024_seconds: int = 600):
        """
        Initialize negative cache.
        
        Args:
            ttl_seconds: Time-to-live for legacy 6024 error cache entries (default: 600 = 10 minutes, deprecated)
            ttl_size_overflow_seconds: Time-to-live for size overflow cache entries (default: 600 = 10 minutes)
            ttl_runtime_6024_seconds: Time-to-live for runtime 6024 + SharedAccountsRoute cache entries (default: 600 = 10 minutes)
        """
        self.ttl_seconds = ttl_seconds
        self.ttl_size_overflow_seconds = ttl_size_overflow_seconds
        self.ttl_runtime_6024_seconds = ttl_runtime_6024_seconds
        # route_signature -> (failure_type: str, timestamp: float)
        self._cache: Dict[str, Tuple[str, float]] = {}
    
    def _get_route_signature(
        self,
        cycle_mints: str,
        legs_count: int,
        use_shared_accounts: bool,
        dex1: str = "Unknown",
        dex2: str = "Unknown",
        direction: str = "Unknown->Unknown",
        program_ids_fingerprint: str = ""
    ) -> str:
        """
        Generate route signature from route characteristics.
        
        Plan identification includes DEX pairs (REQUIRED):
        - Plan USDC→SOL→USDC with Ray→Orca ≠ Plan USDC→SOL→USDC with Orca→Ray
        
        Args:
            cycle_mints: Cycle mints string (e.g., "USDC->SOL->USDC")
            legs_count: Number of legs (quotes count, always 2 for 2-swap)
            use_shared_accounts: Whether useSharedAccounts was used (always False for 2-swap)
            dex1: DEX for leg1 (e.g., "Raydium")
            dex2: DEX for leg2 (e.g., "Orca")
            direction: DEX direction string (e.g., "Raydium->Orca")
            program_ids_fingerprint: Comma-separated program IDs
        
        Returns:
            Route signature string: "cycle_mints|legs_count|useSharedAccounts|dex1|dex2|direction|program_ids_fingerprint"
        """
        return f"{cycle_mints}|{legs_count}|{use_shared_accounts}|{dex1}|{dex2}|{direction}|{program_ids_fingerprint}"
    
    def is_cached(self, route_signature: str, failure_type: Optional[str] = None) -> Tuple[bool, Optional[str], Optional[float]]:
        """
        Check if route is cached (still within TTL).
        
        Args:
            route_signature: Route signature
            failure_type: Optional failure type filter ("atomic_size_overflow" or "runtime_6024_shared_accounts")
                          If None, checks for any cached failure type
        
        Returns:
            Tuple of (is_cached: bool, cached_failure_type: Optional[str], ttl_remaining: Optional[float])
            ttl_remaining is None if not cached, otherwise seconds remaining
        """
        if route_signature not in self._cache:
            return False, None, None
        
        cached_failure_type, timestamp = self._cache[route_signature]
        
        # If failure_type filter specified, check it matches
        if failure_type is not None and cached_failure_type != failure_type:
            return False, None, None
        
        current_time = time.monotonic()
        age = current_time - timestamp
        
        # Select TTL based on failure type
        if cached_failure_type == "atomic_size_overflow":
            ttl = self.ttl_size_overflow_seconds
        elif cached_failure_type == "runtime_6024_shared_accounts":
            ttl = self.ttl_runtime_6024_seconds
        else:
            ttl = self.ttl_seconds  # Legacy/fallback
        
        if age >= ttl:
            # TTL expired, remove entry
            del self._cache[route_signature]
            return False, None, None
        
        ttl_remaining = ttl - age
        return True, cached_failure_type, ttl_remaining
    
    def cache_route(self, route_signature: str, failure_type: str = "runtime_6024_shared_accounts") -> None:
        """
        Cache a route with current timestamp and failure type.
        
        Args:
            route_signature: Route signature
            failure_type: Failure type ("atomic_size_overflow" or "runtime_6024_shared_accounts")
        """
        # Don't overwrite existing entry - TTL is counted from first detection
        if route_signature in self._cache:
            return
        
        self._cache[route_signature] = (failure_type, time.monotonic())
        
        # Select TTL based on failure type
        if failure_type == "atomic_size_overflow":
            ttl = self.ttl_size_overflow_seconds
        elif failure_type == "runtime_6024_shared_accounts":
            ttl = self.ttl_runtime_6024_seconds
        else:
            ttl = self.ttl_seconds  # Legacy/fallback
        
        logger.info(f"Negative-cache route for TTL={ttl}s (type={failure_type}): {route_signature}")
    
    def cleanup_expired(self) -> int:
        """
        Remove expired entries from cache.
        
        Returns:
            Number of entries removed
        """
        current_time = time.monotonic()
        expired = []
        for sig, (failure_type, timestamp) in self._cache.items():
            # Select TTL based on failure type
            if failure_type == "atomic_size_overflow":
                ttl = self.ttl_size_overflow_seconds
            elif failure_type == "runtime_6024_shared_accounts":
                ttl = self.ttl_runtime_6024_seconds
            else:
                ttl = self.ttl_seconds  # Legacy/fallback
            
            if (current_time - timestamp) >= ttl:
                expired.append(sig)
        for sig in expired:
            del self._cache[sig]
        return len(expired)


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
        tokens_map: Optional[Dict[str, str]] = None,
        negative_cache_ttl_sec: int = 600
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
        negative_cache_ttl_size_overflow_sec = int(os.getenv('NEGATIVE_CACHE_TTL_SIZE_OVERFLOW_SEC', '600'))
        negative_cache_ttl_runtime_6024_sec = int(os.getenv('NEGATIVE_CACHE_TTL_RUNTIME_6024_SEC', '600'))
        self.negative_cache = RouteNegativeCache(
            ttl_seconds=negative_cache_ttl_sec,
            ttl_size_overflow_seconds=negative_cache_ttl_size_overflow_sec,
            ttl_runtime_6024_seconds=negative_cache_ttl_runtime_6024_sec
        )
    
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
        logger.info(f"SOL scanning limits: {colors['GREEN']}{sol_limit:.4f} {colors['CYAN']}SOL{colors['RESET']}")
        logger.info(f"USDC scanning limits: {colors['GREEN']}{usdc_limit:.2f} {colors['CYAN']}USDC{colors['RESET']}")
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
            # Format execution plan with DEX per leg (if quotes available)
            if opp.quotes and len(opp.quotes) == 2:
                cycle_display = _format_execution_plan_with_dex(opp, self.tokens_map)
            else:
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
        # Format cycle (backward compatibility for process_opportunity_with_retries)
        cycle_display = ' -> '.join(self.tokens_map.get(addr, addr) for addr in cycle)
        logger.info(f"Processing opportunity with retries: {colors['CYAN']}{cycle_display}{colors['RESET']} (mode: {colors['CYAN']}{self.mode}{colors['RESET']})")
        success_count = 0
        timestamp_start = time.monotonic()
        
        while success_count < max_retries:
            # Skip recheck on first attempt if original_opportunity is provided (zero-recheck first attempt)
            if success_count == 0 and first_attempt_use_original_opportunity and original_opportunity is not None:
                # Use original opportunity directly for first attempt (no recheck = faster)
                opportunity = original_opportunity
                logger.debug("Using original opportunity for first attempt (zero-recheck)")
            else:
                # Recheck execution_plan with same constraints (2-swap, 1-hop per leg, useSharedAccounts=False)
                # Use execution_plan from original_opportunity to preserve constraints
                if original_opportunity and original_opportunity.execution_plan:
                    execution_plan = original_opportunity.execution_plan
                else:
                    # Fallback: reconstruct execution_plan from cycle (backward compatibility)
                    from .arbitrage_finder import ExecutionLeg, ExecutionPlan
                    if len(cycle) == 3:  # 2-leg cycle: [A, B, A]
                        leg1 = ExecutionLeg(from_mint=cycle[0], to_mint=cycle[1], max_hops=1)
                        leg2 = ExecutionLeg(from_mint=cycle[1], to_mint=cycle[2], max_hops=1)
                        execution_plan = ExecutionPlan(cycle_mints=cycle, legs=[leg1, leg2], atomic=True, use_shared_accounts=False)
                    else:
                        logger.error(f"Invalid cycle length for retry: {len(cycle)} (expected 3 for 2-swap)")
                        break
                
                recheck_start = time.monotonic()
                opportunity = await self.finder._check_execution_plan(execution_plan, amount)
                recheck_duration_ms = (time.monotonic() - recheck_start) * 1000
                
                if not opportunity or not opportunity.is_valid(
                    self.finder.min_profit_bps,
                    self.finder.min_profit_usd
                ):
                    # Opportunity no longer profitable, stop retrying
                    if success_count > 0:
                        # Already had successful executions, opportunity just became unprofitable
                        logger.info(f"Opportunity {colors['CYAN']}{cycle_display}{colors['RESET']} {colors['YELLOW']}no longer profitable{colors['RESET']} after {colors['GREEN']}{success_count}{colors['RESET']} successful executions")
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
                    # Format cycle (backward compatibility for process_opportunity_with_retries)
                    cycle_display = ' -> '.join(self.tokens_map.get(addr, addr) for addr in cycle)
                    
                    # Format initial/final amounts based on starting token
                    start_token = opportunity.cycle[0]
                    initial_display = self._format_amount(opportunity.initial_amount, start_token)
                    final_display = self._format_amount(opportunity.final_amount, start_token)
                    
                    # Parse initial_display and final_display to colorize numbers and tickers separately
                    # Format: "X.XX TOKEN" -> number GREEN, ticker CYAN
                    initial_parts = initial_display.split()
                    final_parts = final_display.split()
                    initial_colored = f"{colors['GREEN']}{initial_parts[0]}{colors['RESET']} {colors['CYAN']}{initial_parts[1] if len(initial_parts) > 1 else ''}{colors['RESET']}"
                    final_colored = f"{colors['GREEN']}{final_parts[0]}{colors['RESET']} {colors['CYAN']}{final_parts[1] if len(final_parts) > 1 else ''}{colors['RESET']}"
                    
                    logger.info(
                        f"Simulation #{colors['GREEN']}{success_count}{colors['RESET']} successful for cycle: {colors['CYAN']}{cycle_display}{colors['RESET']} | "
                        f"Profit: {colors['YELLOW']}{opportunity.profit_bps} bps{colors['RESET']} ({colors['YELLOW']}${opportunity.profit_usd:.4f}{colors['RESET']}) | "
                        f"Initial: {initial_colored} | "
                        f"Final: {final_colored}"
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
                    # Format cycle (backward compatibility for process_opportunity_with_retries)
                    cycle_display = ' -> '.join(self.tokens_map.get(addr, addr) for addr in cycle)
                    
                    # Format initial/final amounts based on starting token
                    start_token = opportunity.cycle[0]
                    initial_display = self._format_amount(opportunity.initial_amount, start_token)
                    final_display = self._format_amount(opportunity.final_amount, start_token)
                    
                    # Parse initial_display and final_display to colorize numbers and tickers separately
                    initial_parts = initial_display.split()
                    final_parts = final_display.split()
                    initial_colored = f"{colors['GREEN']}{initial_parts[0]}{colors['RESET']} {colors['CYAN']}{initial_parts[1] if len(initial_parts) > 1 else ''}{colors['RESET']}"
                    final_colored = f"{colors['GREEN']}{final_parts[0]}{colors['RESET']} {colors['CYAN']}{final_parts[1] if len(final_parts) > 1 else ''}{colors['RESET']}"
                    
                    logger.info(
                        f"Execution #{colors['GREEN']}{success_count}{colors['RESET']} successful: {colors['CYAN']}{tx_sig}{colors['RESET']} | "
                        f"Cycle: {colors['CYAN']}{cycle_display}{colors['RESET']} | "
                        f"Profit: {colors['YELLOW']}{opportunity.profit_bps} bps{colors['RESET']} ({colors['YELLOW']}${opportunity.profit_usd:.4f}{colors['RESET']}) | "
                        f"Initial: {initial_colored} | "
                        f"Final: {final_colored}"
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
        # Format execution plan with DEX per leg
        cycle_display = _format_execution_plan_with_dex(opportunity, self.tokens_map)
        legs_count = len(opportunity.quotes)
        
        logger.info(
            f"Simulating opportunity: {colors['CYAN']}{cycle_display}{colors['RESET']} "
            f"({colors['GREEN']}{legs_count}{colors['RESET']} leg{'s' if legs_count != 1 else ''})"
        )
        
        if not opportunity.quotes:
            return False, "No quotes available", None, None
        
        # Use atomic VT for multi-leg cycles (len(quotes) > 1)
        if len(opportunity.quotes) > 1:
            # Get swap instructions for all legs first (needed for route signature and cache check)
            leg_instructions: List[JupiterSwapInstructionsResponse] = []
            for i, quote in enumerate(opportunity.quotes):
                try:
                    instructions_resp = await self.jupiter.get_swap_instructions(
                        quote=quote,
                        user_public_key=user_pubkey,
                        priority_fee_lamports=self.priority_fee,
                        wrap_unwrap_sol=True,
                        dynamic_compute_unit_limit=True,
                        slippage_bps=self.slippage_bps
                    )
                    
                    if instructions_resp is None:
                        return False, f"Failed to get swap instructions for leg {i+1}", None, None
                    
                    leg_instructions.append(instructions_resp)
                except Exception as e:
                    return False, f"Error getting instructions for leg {i+1}: {e}", None, None
            
            # Form full route signature for negative cache check
            # useSharedAccounts is False for 2-swap cross-AMM (hard requirement)
            use_shared_accounts = False
            route_signature = self._get_route_signature(
                opportunity=opportunity,
                leg_instructions=leg_instructions,
                use_shared_accounts=use_shared_accounts
            )
            
            # Check negative cache for size overflow BEFORE building VT
            is_cached, cached_failure_type, ttl_remaining = self.negative_cache.is_cached(
                route_signature, failure_type="atomic_size_overflow"
            )
            if is_cached and cached_failure_type == "atomic_size_overflow":
                logger.info(
                    f"Skipping route by size-cache (ttl_remaining={ttl_remaining:.1f}s): "
                    f"{cycle_display}"
                )
                return False, "skipped_by_size_cache", None, None
            
            # Check negative cache for runtime 6024 BEFORE simulate (only if useSharedAccounts is True)
            if use_shared_accounts:
                is_cached, cached_failure_type, ttl_remaining = self.negative_cache.is_cached(
                    route_signature, failure_type="runtime_6024_shared_accounts"
                )
                if is_cached and cached_failure_type == "runtime_6024_shared_accounts":
                    logger.info(
                        f"Skipping route by runtime-6024 cache (ttl_remaining={ttl_remaining:.1f}s): "
                        f"{cycle_display}"
                    )
                    return False, "skipped_by_runtime_6024_cache", None, None
            
            # Build atomic VersionedTransaction (with pre-fetched instructions to avoid duplicate API calls)
            vt, min_last_valid_block_height, fail_reason, fail_meta = await self._build_atomic_cycle_vt(
                opportunity, user_pubkey, leg_instructions=leg_instructions
            )
            
            # Handle size overflow: cache and return
            if vt is None and fail_reason == "atomic_size_overflow":
                # Cache the route for TTL
                self.negative_cache.cache_route(route_signature, failure_type="atomic_size_overflow")
                
                # Log caching with details from fail_meta
                raw_size = fail_meta.get("raw_size_bytes", 0) if fail_meta else 0
                max_size = fail_meta.get("max_size_bytes", 1232) if fail_meta else 1232
                instr_count = fail_meta.get("instr_count", 0) if fail_meta else 0
                alts_count = fail_meta.get("alts_count", 0) if fail_meta else 0
                ttl = self.negative_cache.ttl_size_overflow_seconds
                
                logger.info(
                    f"Atomic VT too large -> caching route "
                    f"(raw={raw_size}, max={max_size}, instr={instr_count}, alts={alts_count}, ttl={ttl}s): "
                    f"{cycle_display}"
                )
                return False, "atomic_size_overflow", None, None
            
            if vt is None:
                # Other build failures - don't cache
                return False, "Failed to build atomic VersionedTransaction", None, None
            
            # Log VT details
            logger.debug(
                f"Atomic VT built: {colors['GREEN']}{len(vt.message.instructions)}{colors['RESET']} instructions, "
                f"{colors['GREEN']}{len(vt.message.address_table_lookups)}{colors['RESET']} ALT lookups, "
                f"message_type: {colors['CYAN']}v0{colors['RESET']}, "
                f"last_valid_block_height: {colors['YELLOW']}{min_last_valid_block_height or 0}{colors['RESET']}"
            )
            
            # Simulate atomic VT
            sim_result = await self.solana.simulate_versioned_transaction(vt)
            
            if sim_result is None:
                return False, "Simulation failed (no result from RPC)", None, None
            
            # Be defensive: RPC client should return a dict
            if not isinstance(sim_result, dict):
                return False, f"Simulation failed (invalid result type: {type(sim_result).__name__})", None, None
            
            if sim_result.get("err"):
                # Check for runtime 6024 + SharedAccountsRoute (STRICT criteria)
                err = sim_result.get("err")
                logs = sim_result.get("logs", []) or []
                
                # Check if error contains 6024 or 0x1788
                err_str = str(err).lower()
                has_6024 = "6024" in err_str or "0x1788" in err_str
                
                # Check if logs contain "Instruction: SharedAccountsRoute"
                has_shared_accounts_route = any("Instruction: SharedAccountsRoute" in line for line in logs if isinstance(line, str))
                
                # Cache ONLY if: 6024 error + SharedAccountsRoute in logs + useSharedAccounts is True
                if has_6024 and has_shared_accounts_route and use_shared_accounts:
                    # Cache the route for TTL
                    self.negative_cache.cache_route(route_signature, failure_type="runtime_6024_shared_accounts")
                    ttl = self.negative_cache.ttl_runtime_6024_seconds
                    
                    logger.info(
                        f"Runtime 6024 SharedAccountsRoute -> caching route (ttl={ttl}s): "
                        f"{cycle_display}"
                    )
                    return False, "runtime_6024_shared_accounts", sim_result, None
                
                # Include simulation logs in error message for debugging
                err_msg = f"Simulation error: {sim_result['err']}"
                if logs:
                    log_tail = self._format_sim_logs(logs, tail=20)
                    err_msg += f"\nSimulation logs (last 20):\n{log_tail}"
                return False, err_msg, sim_result, None
            
            return True, None, sim_result, None
        
        # Single-leg path: use old method for backward compatibility
        # (Could also use atomic VT, but keeping old path for now)
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
        
        # Format execution plan with DEX per leg
        cycle_display = _format_execution_plan_with_dex(opportunity, self.tokens_map)
        legs_count = len(opportunity.quotes) if opportunity.quotes else 0
        
        if not opportunity.quotes:
            logger.warning(f"Refusing execution: no quotes available (cycle: {cycle_display})")
            return False, "No quotes available", None
        
        position_id = str(uuid.uuid4())
        
        logger.info(
            f"{colors['CYAN']}Executing opportunity:{colors['RESET']} {colors['YELLOW']}{cycle_display}{colors['RESET']} "
            f"({colors['GREEN']}{legs_count}{colors['RESET']} leg{'s' if legs_count != 1 else ''})"
        )
        logger.info(f"{colors['CYAN']}Position ID:{colors['RESET']} {colors['YELLOW']}{position_id}{colors['RESET']}")
        
        # Set trade_in_progress flag BEFORE any operations
        self.trade_in_progress = True
        
        try:
            # A) Risk check (token-aware: base_mint is first token in cycle)
            base_mint = opportunity.cycle[0]
            can_open, reason = self.risk.can_open_position(
                base_mint=base_mint,
                amount_in=opportunity.initial_amount,
                expected_profit_bps=opportunity.profit_bps,
                slippage_bps=self.slippage_bps,  # from config
                expected_profit_usdc=opportunity.profit_usd  # Note: profit_usd is actually USDC
            )
            
            if not can_open:
                return False, f"Risk check failed: {reason}", None
            
            # Add position (base_mint is first token in cycle)
            self.risk.add_position(
                position_id,
                opportunity.cycle[0],  # input_mint
                opportunity.cycle[-1],  # output_mint
                opportunity.initial_amount,
                opportunity.final_amount,
                base_mint=base_mint  # base token for balance locking
            )
            
            # B) Build atomic VersionedTransaction
            vt, min_last_valid_block_height, fail_reason, fail_meta = await self._build_atomic_cycle_vt(opportunity, user_pubkey)
            
            if vt is None:
                # Handle size overflow separately (don't cache in execute mode, just return error)
                if fail_reason == "atomic_size_overflow":
                    return False, "atomic_size_overflow", None
                return False, "Failed to build atomic VersionedTransaction", None
            
            # Log VT details
            logger.debug(
                f"Atomic VT built: {colors['GREEN']}{len(vt.message.instructions)}{colors['RESET']} instructions, "
                f"{colors['GREEN']}{len(vt.message.address_table_lookups)}{colors['RESET']} ALT lookups, "
                f"message_type: {colors['CYAN']}v0{colors['RESET']}, "
                f"last_valid_block_height: {colors['YELLOW']}{min_last_valid_block_height or 0}{colors['RESET']}"
            )
            
            # C) Mandatory simulation of atomic VT
            sim_result = await self.solana.simulate_versioned_transaction(vt)
            
            if sim_result is None:
                return False, "Simulation failed (no result from RPC)", None
            
            if not isinstance(sim_result, dict):
                return False, f"Simulation failed (invalid result type: {type(sim_result).__name__})", None
            
            if sim_result.get("err"):
                # Include simulation logs in error message for debugging
                err_msg = f"Simulation failed (MANDATORY): {sim_result['err']}"
                logs = sim_result.get("logs", [])
                if logs:
                    log_tail = self._format_sim_logs(logs, tail=20)
                    err_msg += f"\nSimulation logs (last 20):\n{log_tail}"
                return False, err_msg, None
            
            # D) Quote expiry check using min_last_valid_block_height from VT
            current_block_height = await self.solana.get_current_block_height()
            if current_block_height is None:
                logger.warning("Failed to get current block height for quote expiry check, proceeding anyway")
            else:
                if min_last_valid_block_height and min_last_valid_block_height > 0:
                    if current_block_height >= min_last_valid_block_height:
                        error_msg = (
                            f"Quote expired: current block height {current_block_height} "
                            f">= last valid block height {min_last_valid_block_height}"
                        )
                        logger.warning(error_msg)
                        return False, error_msg, None
                    else:
                        logger.debug(
                            f"Quote valid: current block height {current_block_height} "
                            f"< last valid block height {min_last_valid_block_height}"
                        )
                else:
                    logger.warning("Quote has no last_valid_block_height set (0), skipping expiry check")
            
            # E) Send atomic VersionedTransaction
            # Use skip_preflight=True since we already have mandatory simulation
            self.risk.update_position_status(position_id, 'executing')
            tx_sig = await self.solana.send_versioned_transaction(
                vt,
                skip_preflight=True
            )
            
            if tx_sig is None:
                return False, "Failed to send transaction", None
            
            # Wait for confirmation
            confirmed = await self.solana.confirm_transaction(tx_sig, timeout=30.0)
            
            if confirmed:
                self.risk.update_position_status(position_id, 'completed')
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
    
    async def execute_prepared_bundle(
        self,
        bundle: PreparedBundle,
        user_pubkey: str
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Execute a PreparedBundle (proof→action: use the exact VT that was simulated).
        
        CRITICAL: Uses the exact VersionedTransaction from bundle (already signed, ready for send).
        Rebuild is ONLY allowed for expiry (with headroom check).
        
        Args:
            bundle: PreparedBundle with fully signed VT ready for immediate send
            user_pubkey: User's public key (for logging/validation)
        
        Returns:
            (success: bool, error_message: Optional[str], tx_signature: Optional[str])
        """
        # STRICT MODE CHECK: Only 'live' mode can send transactions
        if self.mode != 'live':
            return False, f"Transaction sending disabled in mode '{self.mode}'. Use 'live' mode to send transactions.", None
        
        # PARALLEL TRADE PROTECTION: Only one trade at a time
        if self.trade_in_progress:
            return False, "Another trade is already in progress. Wait for completion.", None
        
        opportunity = bundle.opportunity
        cycle_display = _format_execution_plan_with_dex(opportunity, self.tokens_map)
        position_id = str(uuid.uuid4())
        
        logger.info(
            f"{colors['CYAN']}Executing prepared bundle:{colors['RESET']} {colors['YELLOW']}{cycle_display}{colors['RESET']}"
        )
        logger.info(f"{colors['CYAN']}Position ID:{colors['RESET']} {colors['YELLOW']}{position_id}{colors['RESET']}")
        
        # Set trade_in_progress flag BEFORE any operations
        self.trade_in_progress = True
        
        try:
            # A) Risk check (token-aware: base_mint is first token in cycle)
            base_mint = opportunity.cycle[0]
            can_open, reason = self.risk.can_open_position(
                base_mint=base_mint,
                amount_in=opportunity.initial_amount,
                expected_profit_bps=opportunity.profit_bps,
                slippage_bps=self.slippage_bps,
                expected_profit_usdc=opportunity.profit_usd
            )
            
            if not can_open:
                return False, f"Risk check failed: {reason}", None
            
            # Add position
            self.risk.add_position(
                position_id,
                opportunity.cycle[0],
                opportunity.cycle[-1],
                opportunity.initial_amount,
                opportunity.final_amount,
                base_mint=base_mint
            )
            
            # B) Expiry check with headroom (ONLY reason for rebuild)
            expiry_rebuild_headroom_blocks = int(os.getenv('EXPIRY_REBUILD_HEADROOM_BLOCKS', '150'))  # Default: 150 blocks (~30s at 4 blocks/sec)
            current_block_height = await self.solana.get_current_block_height()
            
            if current_block_height is None:
                logger.warning("Failed to get current block height for expiry check, proceeding with bundle VT")
                vt_to_use = bundle.versioned_transaction
                rebuild_reason = None
            elif bundle.min_last_valid_block_height and bundle.min_last_valid_block_height > 0:
                # Check if expiry is close (within headroom)
                blocks_remaining = bundle.min_last_valid_block_height - current_block_height
                if blocks_remaining <= expiry_rebuild_headroom_blocks:
                    # Expiry rebuild allowed (ONLY exception)
                    logger.warning(
                        f"{colors['YELLOW']}Expiry rebuild required:{colors['RESET']} "
                        f"blocks_remaining={blocks_remaining} <= headroom={expiry_rebuild_headroom_blocks} "
                        f"(current={current_block_height}, last_valid={bundle.min_last_valid_block_height})"
                    )
                    rebuild_reason = "expiry_rebuild"
                    # Rebuild: get new swap-instructions and build new VT
                    vt_to_use, min_last_valid_block_height_new, fail_reason, fail_meta = await self._build_atomic_cycle_vt(
                        opportunity, user_pubkey, leg_instructions=bundle.leg_instructions
                    )
                    if vt_to_use is None:
                        return False, f"Expiry rebuild failed: {fail_reason}", None
                    # Optional: re-simulate rebuilt VT (mandatory simulate in live)
                    sim_result_rebuild = await self.solana.simulate_versioned_transaction(vt_to_use)
                    if sim_result_rebuild is None or not isinstance(sim_result_rebuild, dict) or sim_result_rebuild.get("err"):
                        return False, f"Expiry rebuild simulation failed: {sim_result_rebuild.get('err') if isinstance(sim_result_rebuild, dict) else 'no result'}", None
                else:
                    # Use bundle VT (no rebuild)
                    vt_to_use = bundle.versioned_transaction
                    rebuild_reason = None
                    logger.debug(
                        f"Using bundle VT: blocks_remaining={blocks_remaining} > headroom={expiry_rebuild_headroom_blocks}"
                    )
            else:
                # No expiry info - use bundle VT
                vt_to_use = bundle.versioned_transaction
                rebuild_reason = None
                logger.debug("No expiry info in bundle, using bundle VT")
            
            # C) Optional: Re-simulate bundle VT (mandatory simulate in live, but same VT)
            # This is allowed as it simulates the exact same VT that was already simulated
            sim_result = await self.solana.simulate_versioned_transaction(vt_to_use)
            
            if sim_result is None:
                return False, "Simulation failed (no result from RPC)", None
            
            if not isinstance(sim_result, dict):
                return False, f"Simulation failed (invalid result type: {type(sim_result).__name__})", None
            
            if sim_result.get("err"):
                err_msg = f"Simulation failed (MANDATORY): {sim_result['err']}"
                if rebuild_reason:
                    err_msg += f" (after {rebuild_reason})"
                logs = sim_result.get("logs", [])
                if logs:
                    log_tail = self._format_sim_logs(logs, tail=20)
                    err_msg += f"\nSimulation logs (last 20):\n{log_tail}"
                return False, err_msg, None
            
            # D) Send VersionedTransaction (use skip_preflight=True since we already simulated)
            self.risk.update_position_status(position_id, 'executing')
            tx_sig = await self.solana.send_versioned_transaction(
                vt_to_use,
                skip_preflight=True
            )
            
            if tx_sig is None:
                return False, "Failed to send transaction", None
            
            # Wait for confirmation
            confirmed = await self.solana.confirm_transaction(tx_sig, timeout=30.0)
            
            if confirmed:
                self.risk.update_position_status(position_id, 'completed')
                if rebuild_reason:
                    logger.info(f"{colors['YELLOW']}Transaction sent (after {rebuild_reason}){colors['RESET']}: {colors['CYAN']}{tx_sig}{colors['RESET']}")
                return True, None, tx_sig
            else:
                self.risk.update_position_status(position_id, 'failed')
                return False, "Transaction not confirmed", tx_sig
            
        except Exception as e:
            logger.error(f"Error executing prepared bundle: {e}", exc_info=True)
            if position_id in self.risk.active_positions:
                self.risk.update_position_status(position_id, 'failed')
            return False, str(e), None
        
        finally:
            # Always reset trade_in_progress flag
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
    
    def _swap_instruction_to_solana_instruction(self, swap_instr: SwapInstruction) -> Instruction:
        """
        Convert SwapInstruction from Jupiter API to Solana Instruction.
        
        Args:
            swap_instr: SwapInstruction from Jupiter API
        
        Returns:
            Solana Instruction object
        """
        program_id = Pubkey.from_string(swap_instr.program_id)
        
        # Convert accounts
        accounts = []
        for account_meta in swap_instr.accounts:
            pubkey = Pubkey.from_string(account_meta.pubkey)
            accounts.append(AccountMeta(
                pubkey=pubkey,
                is_signer=account_meta.is_signer,
                is_writable=account_meta.is_writable
            ))
        
        # Decode data from base64
        try:
            data = base64.b64decode(swap_instr.data)
        except Exception as e:
            raise ValueError(f"Failed to decode instruction data from base64: {e}") from e
        
        return Instruction(
            program_id=program_id,
            accounts=accounts,
            data=data
        )
    
    def _instruction_signature(self, instruction: Instruction) -> str:
        """
        Generate a signature for an instruction to detect duplicates.
        
        Args:
            instruction: Solana Instruction
        
        Returns:
            Hash signature string
        """
        # Create signature from program_id, accounts (pubkey + flags), and data
        sig_parts = [str(instruction.program_id)]
        for account in instruction.accounts:
            sig_parts.append(f"{account.pubkey}:{account.is_signer}:{account.is_writable}")
        sig_parts.append(base64.b64encode(instruction.data).decode('utf-8'))
        
        sig_string = "|".join(sig_parts)
        return hashlib.sha256(sig_string.encode()).hexdigest()
    
    def _deduplicate_instructions(
        self,
        instructions: List[Instruction]
    ) -> List[Instruction]:
        """
        Deduplicate instructions by signature (program_id, accounts, data).
        
        Args:
            instructions: List of instructions
        
        Returns:
            Deduplicated list of instructions (preserving order, keeping first occurrence)
        """
        seen = set()
        deduplicated = []
        
        for instr in instructions:
            sig = self._instruction_signature(instr)
            if sig not in seen:
                seen.add(sig)
                deduplicated.append(instr)
        
        return deduplicated
    
    def _extract_program_ids_fingerprint(
        self,
        leg_instructions: List[JupiterSwapInstructionsResponse]
    ) -> str:
        """
        Extract unique program IDs from swap instructions in order of first appearance.
        
        Args:
            leg_instructions: List of JupiterSwapInstructionsResponse for each leg
        
        Returns:
            Comma-separated program IDs string
        """
        program_ids = []
        seen = set()
        
        for leg_resp in leg_instructions:
            # Setup instructions
            for setup_instr in leg_resp.setup_instructions:
                if setup_instr.program_id not in seen:
                    program_ids.append(setup_instr.program_id)
                    seen.add(setup_instr.program_id)
            
            # Swap instruction
            if leg_resp.swap_instruction.program_id not in seen:
                program_ids.append(leg_resp.swap_instruction.program_id)
                seen.add(leg_resp.swap_instruction.program_id)
            
            # Cleanup instruction
            if leg_resp.cleanup_instruction and leg_resp.cleanup_instruction.program_id not in seen:
                program_ids.append(leg_resp.cleanup_instruction.program_id)
                seen.add(leg_resp.cleanup_instruction.program_id)
        
        return ",".join(program_ids)
    
    def _get_route_signature(
        self,
        opportunity: ArbitrageOpportunity,
        leg_instructions: Optional[List[JupiterSwapInstructionsResponse]] = None,
        use_shared_accounts: bool = False
    ) -> str:
        """
        Generate route signature for negative cache.
        
        Plan identification includes DEX pairs (REQUIRED):
        - Plan USDC→SOL→USDC with Ray→Orca ≠ Plan USDC→SOL→USDC with Orca→Ray
        
        Args:
            opportunity: ArbitrageOpportunity with execution_plan (must have dex1, dex2 set)
            leg_instructions: Optional list of JupiterSwapInstructionsResponse (exactly 2 for 2-swap)
            use_shared_accounts: Whether useSharedAccounts was used (always False for 2-swap)
        
        Returns:
            Route signature string: "cycle_mints|legs_count|useSharedAccounts|dex1|dex2|direction|program_ids_fingerprint"
        """
        # Cycle mints string from execution_plan
        cycle_mints = "->".join(opportunity.execution_plan.cycle_mints)
        
        # Legs count (always 2 for 2-swap)
        legs_count = len(opportunity.quotes)
        assert legs_count == 2, f"Expected 2 legs for 2-swap, got {legs_count}"
        
        # DEX pairs from execution_plan (REQUIRED for plan identification)
        dex1 = opportunity.execution_plan.dex1 or "Unknown"
        dex2 = opportunity.execution_plan.dex2 or "Unknown"
        direction = f"{dex1}->{dex2}"
        
        # Program IDs fingerprint (unified for entire execution_plan, if available)
        if leg_instructions:
            program_ids_fingerprint = self._extract_program_ids_fingerprint(leg_instructions)
        else:
            program_ids_fingerprint = ""  # Not available yet
        
        return self.negative_cache._get_route_signature(
            cycle_mints=cycle_mints,
            legs_count=legs_count,
            use_shared_accounts=use_shared_accounts,
            dex1=dex1,
            dex2=dex2,
            direction=direction,
            program_ids_fingerprint=program_ids_fingerprint
        )
    
    async def _build_atomic_cycle_vt(
        self,
        opportunity: ArbitrageOpportunity,
        user_pubkey: str,
        leg_instructions: Optional[List[JupiterSwapInstructionsResponse]] = None
    ) -> Tuple[Optional[VersionedTransaction], Optional[int], Optional[str], Optional[Dict[str, Any]]]:
        """
        Build atomic VersionedTransaction for 2-swap execution plan (all-or-nothing execution).
        
        Args:
            opportunity: ArbitrageOpportunity with 2-swap execution plan
            user_pubkey: User's public key (base58)
            leg_instructions: Optional pre-fetched swap instructions (to avoid duplicate API calls)
        
        Returns:
            Tuple of (VersionedTransaction, min_last_valid_block_height, fail_reason, fail_meta) or (None, None, reason, meta) if build failed
            fail_reason: "atomic_size_overflow" for size > 1232 bytes, "build_failed" for other errors, None for success
            fail_meta: Dict with failure details (raw_size_bytes, max_size_bytes, instr_count, alts_count for overflow)
        """
        # Validate cycle length (must be 2-swap: 3 tokens [A, B, A], 2 quotes)
        if len(opportunity.cycle) != 3:
            logger.error(
                f"Invalid cycle length for atomic transaction: {len(opportunity.cycle)} "
                f"(expected 3 for 2-swap execution plan)"
            )
            return None, None, "build_failed", {"error": "Invalid cycle length"}
        
        if len(opportunity.quotes) != 2:
            logger.error(
                f"Invalid quotes count for atomic transaction: {len(opportunity.quotes)} "
                f"(expected 2 for 2-swap execution plan)"
            )
            return None, None, "build_failed", {"error": "Invalid quotes count"}
        
        cycle_display = ' -> '.join(self.tokens_map.get(addr, addr[:8]) for addr in opportunity.cycle)
        logger.debug(
            f"Building atomic VersionedTransaction for cycle: {colors['CYAN']}{cycle_display}{colors['RESET']}"
        )
        
        # Get instructions for each leg (if not provided)
        if leg_instructions is None:
            leg_instructions = []
            all_alt_addresses: Set[str] = set()
            last_valid_block_heights: List[int] = []
            
            for i, quote in enumerate(opportunity.quotes):
                try:
                    instructions_resp = await self.jupiter.get_swap_instructions(
                        quote=quote,
                        user_public_key=user_pubkey,
                        priority_fee_lamports=self.priority_fee,
                        wrap_unwrap_sol=True,
                        dynamic_compute_unit_limit=True,
                        slippage_bps=self.slippage_bps
                    )
                    
                    if instructions_resp is None:
                        logger.error(f"Failed to get swap instructions for leg {i+1}")
                        return None, None, "build_failed", {"error": f"Failed to get swap instructions for leg {i+1}"}
                    
                    leg_instructions.append(instructions_resp)
                    all_alt_addresses.update(instructions_resp.address_lookup_tables)
                    last_valid_block_heights.append(instructions_resp.last_valid_block_height)
                    
                    logger.debug(
                        f"Leg {i+1}: {len(instructions_resp.setup_instructions)} setup, "
                        f"1 swap, {1 if instructions_resp.cleanup_instruction else 0} cleanup, "
                        f"{len(instructions_resp.address_lookup_tables)} ALTs"
                    )
                except NotImplementedError as e:
                    logger.error(f"Leg {i+1} failed: {e}")
                    return None, None, "build_failed", {"error": str(e)}
                except Exception as e:
                    logger.error(f"Error getting instructions for leg {i+1}: {e}", exc_info=True)
                    return None, None, "build_failed", {"error": str(e)}
        else:
            # Extract ALT addresses and block heights from provided instructions
            all_alt_addresses: Set[str] = set()
            last_valid_block_heights: List[int] = []
            for instructions_resp in leg_instructions:
                all_alt_addresses.update(instructions_resp.address_lookup_tables)
                last_valid_block_heights.append(instructions_resp.last_valid_block_height)
        
        # Calculate minimum last_valid_block_height (most restrictive)
        min_last_valid_block_height = min(last_valid_block_heights) if last_valid_block_heights else 0
        logger.debug(f"Using minimum last_valid_block_height: {colors['YELLOW']}{min_last_valid_block_height}{colors['RESET']}")
        
        # Load ALT accounts
        alt_accounts: List[AddressLookupTableAccount] = []
        if all_alt_addresses:
            try:
                alt_accounts = await self.solana.get_address_lookup_table_accounts(
                    list(all_alt_addresses)
                )
                logger.debug(f"Loaded {colors['GREEN']}{len(alt_accounts)}{colors['RESET']} ALT accounts")
            except Exception as e:
                logger.error(f"Failed to load ALT accounts: {e}")
                return None, None, "build_failed", {"error": f"Failed to load ALT accounts: {e}"}
        
        # Build instruction list in order:
        # A) ComputeBudget (if needed - skip for now, Jupiter handles it)
        # B) Setup instructions (all legs, deduplicated)
        # C) Swap instructions (leg1 -> leg2)
        # D) Cleanup instructions (all legs, deduplicated)
        
        all_setup_instructions: List[Instruction] = []
        swap_instructions: List[Instruction] = []
        all_cleanup_instructions: List[Instruction] = []
        
        # Collect setup and cleanup from all legs
        for leg_resp in leg_instructions:
            for setup_instr in leg_resp.setup_instructions:
                all_setup_instructions.append(
                    self._swap_instruction_to_solana_instruction(setup_instr)
                )
            
            swap_instructions.append(
                self._swap_instruction_to_solana_instruction(leg_resp.swap_instruction)
            )
            
            if leg_resp.cleanup_instruction:
                all_cleanup_instructions.append(
                    self._swap_instruction_to_solana_instruction(leg_resp.cleanup_instruction)
                )
        
        # Deduplicate setup and cleanup
        setup_instructions = self._deduplicate_instructions(all_setup_instructions)
        cleanup_instructions = self._deduplicate_instructions(all_cleanup_instructions)
        
        logger.debug(
            f"Instruction counts: {colors['GREEN']}{len(setup_instructions)}{colors['RESET']} setup "
            f"(deduped from {len(all_setup_instructions)}), "
            f"{colors['GREEN']}{len(swap_instructions)}{colors['RESET']} swap, "
            f"{colors['GREEN']}{len(cleanup_instructions)}{colors['RESET']} cleanup "
            f"(deduped from {len(all_cleanup_instructions)})"
        )
        
        # Combine all instructions in order
        all_instructions = setup_instructions + swap_instructions + cleanup_instructions
        
        if not all_instructions:
            logger.error("No instructions to build transaction")
            return None, None, "build_failed", {"error": "No instructions to build transaction"}
        
        # Get recent blockhash RIGHT BEFORE building VT to minimize time gap
        # This reduces BlockhashNotFound errors during simulation
        recent_blockhash = await self.solana.get_recent_blockhash()
        if not recent_blockhash:
            logger.error("Failed to get recent blockhash")
            return None, None, "build_failed", {"error": "Failed to get recent blockhash"}
        
        # Get wallet pubkey
        if self.solana.wallet is None:
            logger.error("No wallet available for transaction signing")
            return None, None, "build_failed", {"error": "No wallet available for transaction signing"}
        
        payer = self.solana.wallet.pubkey()
        
        # Build MessageV0 with ALT using try_compile
        try:
            # MessageV0.try_compile() automatically:
            # - Collects account keys from instructions (preserving order, handling duplicates)
            # - Creates proper MessageHeader
            # - Handles address_table_lookups from ALT accounts
            # - Returns proper MessageV0 (not legacy Message)
            message_v0 = MessageV0.try_compile(
                payer=payer,
                instructions=all_instructions,
                address_lookup_table_accounts=alt_accounts,
                recent_blockhash=recent_blockhash
            )
            
            # Validate that we got MessageV0 (not legacy)
            if not isinstance(message_v0, MessageV0):
                logger.error(f"Expected MessageV0, got {type(message_v0)}")
                return None, None, "build_failed", {"error": f"Expected MessageV0, got {type(message_v0)}"}
            
            # Create VersionedTransaction from MessageV0 with signer
            # VersionedTransaction automatically signs when signers are passed
            versioned_tx = VersionedTransaction(message_v0, [self.solana.wallet])
            
            # Early size check: Solana transaction size limit is 1232 bytes (raw)
            tx_bytes = bytes(versioned_tx)
            raw_len = len(tx_bytes)
            b64_len = len(base64.b64encode(tx_bytes))
            
            if raw_len > 1232:
                logger.warning(
                    f"Atomic VT too large: raw={colors['YELLOW']}{raw_len}{colors['RESET']} bytes "
                    f"(max 1232), b64={colors['YELLOW']}{b64_len}{colors['RESET']} bytes, "
                    f"instr={colors['GREEN']}{len(all_instructions)}{colors['RESET']}, "
                    f"ALTs={colors['GREEN']}{len(alt_accounts)}{colors['RESET']}: skipping opportunity"
                )
                return None, None, "atomic_size_overflow", {
                    "raw_size_bytes": raw_len,
                    "max_size_bytes": 1232,
                    "instr_count": len(all_instructions),
                    "alts_count": len(alt_accounts)
                }
            
            # Log transaction details
            logger.info(
                f"{colors['GREEN']}Atomic VersionedTransaction built (v0):{colors['RESET']} "
                f"{colors['GREEN']}{len(all_instructions)}{colors['RESET']} instructions, "
                f"{colors['GREEN']}{len(alt_accounts)}{colors['RESET']} ALTs, "
                f"size={colors['GREEN']}{raw_len}{colors['RESET']}/{colors['YELLOW']}1232{colors['RESET']} bytes, "
                f"message_type: {colors['CYAN']}v0{colors['RESET']}, "
                f"last_valid_block_height: {colors['YELLOW']}{min_last_valid_block_height}{colors['RESET']}"
            )
            
            # Log ALT details if present
            if alt_accounts:
                alt_addresses = [str(alt.addresses[0]) if alt.addresses else "empty" for alt in alt_accounts]
                logger.debug(
                    f"ALT accounts used: {colors['CYAN']}{len(alt_accounts)}{colors['RESET']} "
                    f"(address_table_lookups: {len(message_v0.address_table_lookups)})"
                )
            
            return versioned_tx, min_last_valid_block_height, None, None
            
        except Exception as e:
            logger.error(
                f"Failed to build VersionedTransaction (v0): {e}",
                exc_info=True
            )
            # Check for specific error types
            if "too many accounts" in str(e).lower() or "account" in str(e).lower():
                logger.error(
                    f"Transaction too large: {len(all_instructions)} instructions, "
                    f"{len(alt_accounts)} ALTs. Consider reducing instruction count."
                )
            elif "alt" in str(e).lower() or "lookup" in str(e).lower():
                logger.error(
                    f"ALT loading/compilation failed. "
                    f"ALT accounts: {len(alt_accounts)}, addresses: {list(all_alt_addresses)}"
                )
            return None, None, "build_failed", {"error": str(e)}