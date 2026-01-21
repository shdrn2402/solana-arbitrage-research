"""
Arbitrage opportunity finder.
Searches for 2-swap cross-AMM arbitrage opportunities with enforced 1-hop per leg.
"""
import asyncio
import logging
from typing import List, Optional, Tuple, Callable, Awaitable, AsyncIterator, Dict, Set, TYPE_CHECKING, Any
from dataclasses import dataclass
import time

from .jupiter_client import JupiterClient, JupiterQuote
from .utils import get_terminal_colors

if TYPE_CHECKING:
    from .trader import Trader, PreparedBundle

logger = logging.getLogger(__name__)
colors = get_terminal_colors()


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


@dataclass
class ExecutionLeg:
    """Represents a single leg in an execution plan."""
    from_mint: str
    to_mint: str
    allowed_dexes: Optional[Set[str]] = None  # Optional: specific DEXes allowed for this leg
    max_hops: int = 1  # Hard requirement: 1-hop only
    
    def __post_init__(self):
        """Validate leg constraints."""
        if self.max_hops != 1:
            raise ValueError("max_hops must be 1 (enforced 1-hop requirement)")


@dataclass
class ExecutionPlan:
    """Represents a 2-swap cross-AMM execution plan."""
    cycle_mints: List[str]  # [A, B, A] - 2-leg cycle (3 tokens total)
    legs: List[ExecutionLeg]  # Exactly 2 legs
    atomic: bool = True  # Always atomic
    use_shared_accounts: bool = False  # Always False for 2-swap
    dex1: Optional[str] = None  # DEX for leg1 (extracted from quote after execution)
    dex2: Optional[str] = None  # DEX for leg2 (extracted from quote after execution)
    
    def __post_init__(self):
        """Validate execution plan."""
        if len(self.legs) != 2:
            raise ValueError("Execution plan must have exactly 2 legs")
        if len(self.cycle_mints) != 3:
            raise ValueError("Execution plan cycle_mints must have 3 tokens [A, B, A]")
        if self.cycle_mints[0] != self.cycle_mints[2]:
            raise ValueError("Execution plan must start and end with the same token")
        if not self.atomic:
            raise ValueError("Execution plan must be atomic")
        if self.use_shared_accounts:
            raise ValueError("Execution plan must have use_shared_accounts=False")
    
    def get_plan_id(self) -> str:
        """
        Generate unique plan identifier: cycle_mints + DEX1 + DEX2 + direction.
        
        This is used for:
        - negative-cache keys
        - hit-rate statistics
        - prioritization (which pairs to run more frequently)
        
        Returns:
            Plan ID string: "cycle_mints|dex1|dex2|direction"
        """
        cycle_str = "->".join(self.cycle_mints)
        dex1_str = self.dex1 or "Unknown"
        dex2_str = self.dex2 or "Unknown"
        direction = f"{dex1_str}->{dex2_str}"
        return f"{cycle_str}|{dex1_str}|{dex2_str}|{direction}"


@dataclass
class ArbitrageOpportunity:
    """Represents an arbitrage opportunity with execution plan."""
    execution_plan: ExecutionPlan  # Execution plan with constraints
    quotes: List[JupiterQuote]  # Exactly 2 quotes (one per leg)
    initial_amount: int
    final_amount: int
    profit_bps: int
    profit_usd: float
    price_impact_total: float
    timestamp: float
    
    # Backward compatibility: cycle property
    @property
    def cycle(self) -> List[str]:
        """Backward compatibility: return cycle_mints from execution_plan."""
        return self.execution_plan.cycle_mints
    
    def is_valid(self, min_profit_bps: int, min_profit_usd: float) -> bool:
        """
        Check if opportunity meets minimum requirements.
        
        PRIMARY: profit_usd >= min_profit_usd (always applied)
        SECONDARY: profit_bps >= min_profit_bps (optional, disabled if min_profit_bps = 0)
        """
        # PRIMARY check: minimum profit in USDC (always applied)
        if self.profit_usd < min_profit_usd:
            return False
        
        # SECONDARY check: optional bps filter (can be disabled by setting min_profit_bps to 0)
        if min_profit_bps > 0 and self.profit_bps < min_profit_bps:
            return False
        
        return True


class ArbitrageFinder:
    """Finds arbitrage opportunities using Jupiter API."""
    
    def __init__(
        self,
        jupiter_client: JupiterClient,
        tokens: List[str],
        min_profit_bps: int = 50,
        min_profit_usd: float = 0.1,
        max_cycle_length: int = 4,
        max_cycles: int = 100,
        quote_timeout: float = 5.0,
        slippage_bps: int = 50,
        sol_price_usdc: float = 100.0,
        quote_delay_seconds: float = 1.0,
        execution_plans: Optional[List[ExecutionPlan]] = None
    ):
        self.jupiter = jupiter_client
        self.tokens = tokens
        self.min_profit_bps = min_profit_bps
        self.min_profit_usd = min_profit_usd
        self.max_cycle_length = max_cycle_length
        self.max_cycles = max_cycles
        self.quote_timeout = quote_timeout
        self.slippage_bps = slippage_bps
        self.sol_price_usdc = sol_price_usdc
        self.quote_delay_seconds = quote_delay_seconds
        self.execution_plans = execution_plans or []  # Execution plans from config.json
    
    async def find_opportunities(
        self,
        start_token: str,
        amount: int,
        max_opportunities: int = 10,
        on_opportunity_found: Optional[Callable[[ArbitrageOpportunity], Awaitable[bool]]] = None,
        amounts_by_mint: Optional[Dict[str, int]] = None
    ) -> List[ArbitrageOpportunity]:
        """
        Find arbitrage opportunities starting from a token.
        
        Args:
            start_token: Starting token mint address
            amount: Starting amount in smallest unit
            max_opportunities: Maximum number of opportunities to return
        
        Returns:
            List of ArbitrageOpportunity sorted by profit
        """
        opportunities = []
        
        # Use execution plans from config.json (loaded via constructor)
        execution_plans = self.execution_plans
        
        if not execution_plans:
            logger.warning("No execution plans configured. Please add execution plans to config.json")
            return []
        
        logger.info(f"{colors['DIM']}Searching {len(execution_plans)} execution plans for arbitrage opportunities...{colors['RESET']}")
        if on_opportunity_found:
            logger.debug("Callback on_opportunity_found is provided, will call it for each profitable opportunity")
        else:
            logger.debug("No callback provided, will collect all opportunities")
        
        # Check each execution plan sequentially (no parallelism) with delays to avoid quota burn
        for execution_plan in execution_plans:
            # Use per-base-token amount if provided (SOL and USDC have different decimals/limits)
            base_mint = execution_plan.cycle_mints[0]
            plan_amount = amounts_by_mint.get(base_mint, amount) if amounts_by_mint else amount
            if plan_amount <= 0:
                # Skip execution plans we cannot fund (e.g., no USDC balance)
                continue

            result = await self._check_execution_plan(execution_plan, plan_amount)
            
            if result:
                # Check validity and log rejection reasons at DEBUG level
                is_valid = result.is_valid(self.min_profit_bps, self.min_profit_usd)
                
                if not is_valid:
                    # Log why opportunity was rejected (DEBUG level to avoid noise)
                    rejection_reasons = []
                    if result.profit_usd < self.min_profit_usd:
                        rejection_reasons.append(
                            f"profit_usd={result.profit_usd:.4f} < min_profit_usd={self.min_profit_usd:.4f}"
                        )
                    if self.min_profit_bps > 0 and result.profit_bps < self.min_profit_bps:
                        rejection_reasons.append(
                            f"profit_bps={result.profit_bps} < min_profit_bps={self.min_profit_bps}"
                        )
                    
                    logger.debug(
                        f"Opportunity rejected: {'; '.join(rejection_reasons)} "
                        f"(plan: {' -> '.join(result.execution_plan.cycle_mints)})"
                    )
                    # Not profitable -> do NOT call callback / do NOT append
                    continue
                else:
                    # Safety assertion: ensure opportunity meets minimum requirements
                    if result.profit_usd < self.min_profit_usd:
                        logger.error(
                            f"CRITICAL: Opportunity passed is_valid() but profit_usd={result.profit_usd:.4f} < "
                            f"min_profit_usd={self.min_profit_usd:.4f}. Skipping to prevent invalid results."
                        )
                        continue  # Skip this opportunity
                    
                    if self.min_profit_bps > 0 and result.profit_bps < self.min_profit_bps:
                        logger.error(
                            f"CRITICAL: Opportunity passed is_valid() but profit_bps={result.profit_bps} < "
                            f"min_profit_bps={self.min_profit_bps}. Skipping to prevent invalid results."
                        )
                        continue  # Skip this opportunity
                    
                    # All checks passed - safe to append
                    opportunities.append(result)

                    # If callback provided, call it immediately (processing will pause the search loop)
                    if on_opportunity_found:
                        logger.debug(f"{colors['DIM']}Calling on_opportunity_found callback...{colors['RESET']}")
                        try:
                            should_continue = await on_opportunity_found(result)
                            logger.debug(f"{colors['DIM']}Callback finished, should_continue={should_continue}{colors['RESET']}")
                        except Exception as e:
                            logger.error(f"Error in on_opportunity_found callback: {e}", exc_info=True)
                            should_continue = True  # Continue on error to avoid blocking
                        
                        if not should_continue:
                            # Callback requested to stop searching
                            logger.info("Callback requested to stop searching")
                            break
                        # No delay needed after callback - rate limiting is handled by JupiterClient
                        # Burst mode is used inside callback for fast processing
                        continue
            
            # No delay needed between cycles - rate limiting is handled by JupiterClient
            # Each quote request in _check_cycle() will be rate-limited individually
        
        # Sort by profit (descending)
        opportunities.sort(key=lambda x: x.profit_bps, reverse=True)
        
        return opportunities[:max_opportunities]
    
    async def find_opportunities_stream(
        self,
        start_token: str,
        amount: int,
        max_opportunities: int = 10
    ) -> AsyncIterator[ArbitrageOpportunity]:
        """
        Find opportunities and yield them as they are found (async generator).
        
        Yields opportunities immediately for processing (simulation/execution).
        This allows stream processing without waiting for all cycles to complete.
        
        Args:
            start_token: Starting token mint address
            amount: Starting amount in smallest unit
            max_opportunities: Maximum number of opportunities to yield
        
        Yields:
            ArbitrageOpportunity as they are found
        """
        found_count = 0
        
        # Use execution plans from config.json (already in mint-address form)
        execution_plans = self.execution_plans
        
        logger.info(f"{colors['DIM']}Searching {len(execution_plans)} execution plans for arbitrage opportunities (stream mode)...{colors['RESET']}")
        
        for execution_plan in execution_plans:
            result = await self._check_execution_plan(execution_plan, amount)
            
            if result and result.is_valid(self.min_profit_bps, self.min_profit_usd):
                found_count += 1
                yield result
                
                if found_count >= max_opportunities:
                    break
            
            # Delay between cycles for rate limiting (configurable via QUOTE_DELAY_SECONDS, default: 1.0 sec for 60 req/min)
            await asyncio.sleep(self.quote_delay_seconds)
    
    def _enforce_1hop_check(self, quote: JupiterQuote, from_mint: str, to_mint: str) -> bool:
        """
        Enforce 1-hop requirement: check that routePlan contains exactly 1 hop.
        
        This is a HARD GATE - no fallbacks, no "try anyway".
        
        Args:
            quote: JupiterQuote to check
            from_mint: Expected input mint for this leg
            to_mint: Expected output mint for this leg
        
        Returns:
            True if route is strictly 1-hop, False otherwise
        """
        route_plan = quote.route_plan or []
        
        # HARD REQUIREMENT: exactly 1 hop
        if len(route_plan) != 1:
            logger.debug(
                f"Route rejected: multi-hop (hops={len(route_plan)}), "
                f"expected 1-hop for {from_mint[:8]}... -> {to_mint[:8]}..."
            )
            return False
        
        # Verify the single hop matches (from_mint, to_mint)
        hop = route_plan[0]
        if not isinstance(hop, dict):
            logger.debug(f"Route rejected: invalid hop format for {from_mint[:8]}... -> {to_mint[:8]}...")
            return False
        
        # Check if hop has swapInfo with input/output mints
        swap_info = hop.get('swapInfo', {})
        hop_input_mint = swap_info.get('inputMint') or hop.get('inputMint')
        hop_output_mint = swap_info.get('outputMint') or hop.get('outputMint')
        
        # If hop doesn't have explicit mints, verify quote-level mints match
        if not hop_input_mint or not hop_output_mint:
            # Fallback: verify quote-level mints match expected leg mints
            if quote.input_mint != from_mint or quote.output_mint != to_mint:
                logger.debug(
                    f"Route rejected: quote mints mismatch "
                    f"(quote: {quote.input_mint[:8]}... -> {quote.output_mint[:8]}..., "
                    f"expected: {from_mint[:8]}... -> {to_mint[:8]}...)"
                )
                return False
        else:
            # Verify hop-level mints match expected leg mints
            if hop_input_mint != from_mint or hop_output_mint != to_mint:
                logger.debug(
                    f"Route rejected: hop mints mismatch "
                    f"(hop: {hop_input_mint[:8]}... -> {hop_output_mint[:8]}..., "
                    f"expected: {from_mint[:8]}... -> {to_mint[:8]}...)"
                )
                return False
        
        # All checks passed - route is strictly 1-hop
        return True
    
    async def _check_execution_plan(
        self,
        execution_plan: ExecutionPlan,
        initial_amount: int
    ) -> Optional[ArbitrageOpportunity]:
        """
        Check if an execution plan is profitable with enforced 1-hop per leg.
        
        Args:
            execution_plan: ExecutionPlan with 2 legs and constraints
            initial_amount: Starting amount in smallest unit
        
        Returns:
            ArbitrageOpportunity if profitable and all constraints met, None otherwise
        """
        if len(execution_plan.legs) != 2:
            return None
        
        quotes = []
        current_amount = initial_amount
        
        try:
            # Get quotes for each leg sequentially with enforced 1-hop
            for i, leg in enumerate(execution_plan.legs):
                # Request quote with onlyDirectRoutes=True to enforce 1-hop
                quote = await asyncio.wait_for(
                    self.jupiter.get_quote(
                        leg.from_mint,
                        leg.to_mint,
                        current_amount,
                        slippage_bps=self.slippage_bps,
                        only_direct_routes=True  # Enforce 1-hop via Jupiter API
                    ),
                    timeout=self.quote_timeout
                )
                
                if quote is None or quote.out_amount == 0:
                    logger.debug(f"Leg {i+1} quote failed: {leg.from_mint[:8]}... -> {leg.to_mint[:8]}...")
                    return None
                
                # HARD GATE: Enforce 1-hop check immediately after quote
                if not self._enforce_1hop_check(quote, leg.from_mint, leg.to_mint):
                    logger.debug(
                        f"Leg {i+1} rejected: 1-hop constraint violated "
                        f"({leg.from_mint[:8]}... -> {leg.to_mint[:8]}...)"
                    )
                    return None  # Hard reject - no fallback
                
                quotes.append(quote)
                current_amount = quote.out_amount
                
                # NO delays between legs - take quotes in burst for consistency
                # Rate limiting is handled by delay AFTER the execution plan
            
            # Extract DEX pairs from quotes (REQUIRED for plan identification)
            dex1 = _extract_dex_from_quote(quotes[0]) if len(quotes) > 0 else None
            dex2 = _extract_dex_from_quote(quotes[1]) if len(quotes) > 1 else None
            
            # HARD GATE: Cross-AMM invariant (DEX1 ≠ DEX2) - REJECT if same DEX or Unknown
            # This ensures consistency across all paths (scan/stream/recheck/inline)
            if dex1 == "Unknown" or dex2 == "Unknown":
                logger.debug(
                    f"Execution plan rejected: DEX extraction failed "
                    f"(dex1={dex1}, dex2={dex2}): {execution_plan.cycle_mints}"
                )
                return None  # Hard reject - cross-AMM invariant cannot be verified
            
            if dex1 == dex2:
                logger.debug(
                    f"Execution plan rejected: same DEX on both legs "
                    f"(dex1={dex1}, dex2={dex2}): {execution_plan.cycle_mints}"
                )
                return None  # Hard reject - cross-AMM requirement violated
            
            # Update execution_plan with DEX pairs (for plan identification)
            execution_plan.dex1 = dex1
            execution_plan.dex2 = dex2
            
            # HARD SKIP: Check for anomalously weak liquidity (high price impact)
            # Skip plans with excessive price impact (indicates poor liquidity/paper quotes)
            max_price_impact_pct = 5.0  # Hard limit: skip if any leg has >5% price impact
            for i, quote in enumerate(quotes):
                if quote.price_impact_pct > max_price_impact_pct:
                    logger.debug(
                        f"Execution plan rejected: anomalously weak liquidity "
                        f"(leg {i+1} price impact {quote.price_impact_pct:.2f}% > {max_price_impact_pct}%): "
                        f"{execution_plan.cycle_mints} ({dex1}->{dex2})"
                    )
                    return None  # Hard reject - no fallback
            
            # Calculate profit
            final_amount = current_amount
            profit_bps = self._calculate_profit_bps(initial_amount, final_amount)
            profit_usd = self._estimate_profit_usd(initial_amount, final_amount, execution_plan.cycle_mints[0])
            price_impact_total = sum(q.price_impact_pct for q in quotes)
            
            opportunity = ArbitrageOpportunity(
                execution_plan=execution_plan,
                quotes=quotes,
                initial_amount=initial_amount,
                final_amount=final_amount,
                profit_bps=profit_bps,
                profit_usd=profit_usd,
                price_impact_total=price_impact_total,
                timestamp=time.time()
            )
            
            return opportunity
            
        except asyncio.TimeoutError:
            logger.debug(f"Execution plan {execution_plan.cycle_mints} quote timeout")
            return None
        except Exception as e:
            logger.debug(f"Execution plan {execution_plan.cycle_mints} check failed: {e}")
            return None
    
    def _calculate_profit_bps(self, amount_in: int, amount_out: int) -> int:
        """Calculate profit in basis points."""
        if amount_in == 0:
            return 0
        profit_pct = ((amount_out - amount_in) / amount_in) * 100
        return int(profit_pct * 100)
    
    def _estimate_profit_usd(
        self,
        amount_in: int,
        amount_out: int,
        token_mint: str
    ) -> float:
        """
        Estimate profit in USDC (referred to as USD in variable names for compatibility).
        
        NOTE: This assumes SOL for conversion. For production, should:
        - Use price oracle for accurate conversion
        - Handle different input tokens properly
        
        Returns profit in USDC.
        """
        # NOTE: We only support accurate estimation for cycles starting in SOL or USDC.
        # For other tokens, a real price oracle is required.
        sol_mint = "So11111111111111111111111111111111111111112"
        usdc_mint = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

        profit_raw = amount_out - amount_in
        if token_mint == usdc_mint:
            # USDC has 6 decimals
            return profit_raw / 1e6
        if token_mint == sol_mint:
            # SOL has 9 decimals; convert to USDC via configured SOL price
            profit_sol = profit_raw / 1e9
            return profit_sol * self.sol_price_usdc

        # Unknown base token: can't estimate reliably
        return 0.0
    
    async def inline_arbitrage_one_iteration(
        self,
        amounts_by_mint: Dict[str, int],
        trader: 'Trader',  # Forward reference to avoid circular import
        user_pubkey: str,
        on_success: Optional[Callable[['PreparedBundle', Dict[str, Any]], Awaitable[None]]] = None,
        stats: Optional[Dict[str, int]] = None
    ) -> Dict[str, Any]:
        """
        Execute one iteration of inline arbitrage search (one pass through all execution plans).
        
        Returns:
            Dict with stats: {
                'candidates': int,
                'successes': int,
                'skips': Dict[str, int] (reason -> count),
                'errors': int,
                'had_fundable_plans': bool,  # At least one plan with initial_amount > 0
                'did_any_quote_call': bool,  # At least one get_quote was actually called
                'did_candidate_flow': bool   # Reached swap-instructions/VT/simulate
            }
        """
        if stats is None:
            stats = {
                'candidates': 0,
                'successes': 0,
                'skips': {},
                'errors': 0,
                'had_fundable_plans': False,  # At least one plan with initial_amount > 0
                'did_any_quote_call': False,  # At least one get_quote was actually called
                'did_candidate_flow': False  # Reached swap-instructions/VT/simulate
            }
        
        # Process each execution plan
        for plan in self.execution_plans:
            try:
                # Get initial amount for this plan
                initial_mint = plan.cycle_mints[0]
                initial_amount = amounts_by_mint.get(initial_mint, 0)
                
                if initial_amount == 0:
                    stats['skips'].setdefault('zero_balance', 0)
                    stats['skips']['zero_balance'] += 1
                    continue  # SKIP silently
                
                stats['had_fundable_plans'] = True  # Mark that we have at least one fundable plan
                
                # 1) Quote leg1
                leg1 = plan.legs[0]
                try:
                    stats['did_any_quote_call'] = True  # Mark that we actually called get_quote
                    quote1 = await asyncio.wait_for(
                        self.jupiter.get_quote(
                            leg1.from_mint,
                            leg1.to_mint,
                            initial_amount,
                            slippage_bps=self.slippage_bps,
                            only_direct_routes=True  # Enforce 1-hop
                        ),
                        timeout=self.quote_timeout
                    )
                except asyncio.TimeoutError:
                    stats['skips'].setdefault('timeout_leg1', 0)
                    stats['skips']['timeout_leg1'] += 1
                    continue
                
                if quote1 is None or quote1.out_amount == 0:
                    stats['skips'].setdefault('no_quote_leg1', 0)
                    stats['skips']['no_quote_leg1'] += 1
                    continue  # SKIP silently
                
                # 2) FORCED 1-HOP CHECK (HARD)
                if not self._enforce_1hop_check(quote1, leg1.from_mint, leg1.to_mint):
                    stats['skips'].setdefault('multi_hop_leg1', 0)
                    stats['skips']['multi_hop_leg1'] += 1
                    continue  # SKIP silently
                
                # Extract DEX1 from quote
                dex1 = _extract_dex_from_quote(quote1)
                if dex1 == "Unknown":
                    stats['skips'].setdefault('unknown_dex1', 0)
                    stats['skips']['unknown_dex1'] += 1
                    continue  # SKIP silently
                
                # 3) Quote leg2
                leg2 = plan.legs[1]
                amount_leg2_in = quote1.out_amount
                try:
                    quote2 = await asyncio.wait_for(
                        self.jupiter.get_quote(
                            leg2.from_mint,
                            leg2.to_mint,
                            amount_leg2_in,
                            slippage_bps=self.slippage_bps,
                            only_direct_routes=True  # Enforce 1-hop
                        ),
                        timeout=self.quote_timeout
                    )
                except asyncio.TimeoutError:
                    stats['skips'].setdefault('timeout_leg2', 0)
                    stats['skips']['timeout_leg2'] += 1
                    continue
                
                if quote2 is None or quote2.out_amount == 0:
                    stats['skips'].setdefault('no_quote_leg2', 0)
                    stats['skips']['no_quote_leg2'] += 1
                    continue  # SKIP
                
                # 4) FORCED 1-HOP CHECK (HARD)
                if not self._enforce_1hop_check(quote2, leg2.from_mint, leg2.to_mint):
                    stats['skips'].setdefault('multi_hop_leg2', 0)
                    stats['skips']['multi_hop_leg2'] += 1
                    continue  # SKIP
                
                # Extract DEX2 from quote
                dex2 = _extract_dex_from_quote(quote2)
                if dex2 == "Unknown":
                    stats['skips'].setdefault('unknown_dex2', 0)
                    stats['skips']['unknown_dex2'] += 1
                    continue  # SKIP silently
                
                # Ensure DEX1 ≠ DEX2 (cross-AMM requirement)
                if dex1 == dex2:
                    stats['skips'].setdefault('same_dex', 0)
                    stats['skips']['same_dex'] += 1
                    continue  # SKIP silently (same DEX, not cross-AMM)
                
                # Update execution_plan with DEX pairs
                plan.dex1 = dex1
                plan.dex2 = dex2
                
                # 5) HARD SKIP: Check for anomalously weak liquidity
                max_price_impact_pct = 5.0
                if quote1.price_impact_pct > max_price_impact_pct or quote2.price_impact_pct > max_price_impact_pct:
                    stats['skips'].setdefault('high_impact', 0)
                    stats['skips']['high_impact'] += 1
                    continue  # SKIP silently
                
                # 6) Edge-gate: Calculate profit
                final_amount = quote2.out_amount
                profit_bps = int(((final_amount - initial_amount) / initial_amount) * 10000) if initial_amount > 0 else 0
                profit_usd = self._estimate_profit_usd(initial_amount, final_amount, plan.cycle_mints[0])
                
                # Quick edge-gate check
                if profit_usd < self.min_profit_usd:
                    stats['skips'].setdefault('low_profit_usd', 0)
                    stats['skips']['low_profit_usd'] += 1
                    continue  # SKIP silently
                if self.min_profit_bps > 0 and profit_bps < self.min_profit_bps:
                    stats['skips'].setdefault('low_profit_bps', 0)
                    stats['skips']['low_profit_bps'] += 1
                    continue  # SKIP silently
                
                stats['candidates'] += 1
                
                # Log candidate found (before swap-instructions)
                logger.info(
                    f"{colors['CYAN']}Candidate found{colors['RESET']}: "
                    f"{dex1}->{dex2} | "
                    f"Profit: {colors['GREEN']}{profit_bps} bps (${profit_usd:.4f}){colors['RESET']} | "
                    f"Getting swap-instructions..."
                )
                
                # Create opportunity
                opportunity = ArbitrageOpportunity(
                    execution_plan=plan,
                    quotes=[quote1, quote2],
                    initial_amount=initial_amount,
                    final_amount=final_amount,
                    profit_bps=profit_bps,
                    profit_usd=profit_usd,
                    price_impact_total=quote1.price_impact_pct + quote2.price_impact_pct,
                    timestamp=time.time()
                )
                
                # 7) Get swap-instructions (Jupiter) - BURST MODE (no rate limit)
                stats['did_candidate_flow'] = True  # Mark that we reached candidate flow
                leg_instructions = []
                try:
                    async with trader.jupiter.rate_limiter.burst():
                        for quote in [quote1, quote2]:
                            instructions_resp = await trader.jupiter.get_swap_instructions(
                                quote=quote,
                                user_public_key=user_pubkey,
                                priority_fee_lamports=trader.priority_fee,
                                wrap_unwrap_sol=True,
                                dynamic_compute_unit_limit=True,
                                slippage_bps=self.slippage_bps
                            )
                            
                            if instructions_resp is None:
                                raise Exception("Failed to get swap instructions")
                            
                            leg_instructions.append(instructions_resp)
                except Exception as e:
                    stats['skips'].setdefault('swap_instructions_failed', 0)
                    stats['skips']['swap_instructions_failed'] += 1
                    logger.debug(f"Failed to get swap instructions for {dex1}->{dex2}: {e}")
                    continue  # SKIP
                
                # 8) Build route_signature
                route_signature = trader._get_route_signature(
                    opportunity=opportunity,
                    leg_instructions=leg_instructions,
                    use_shared_accounts=False  # Hard requirement for 2-swap
                )
                
                # 9) Negative-cache CHECK (EARLY)
                is_cached, cached_failure_type, ttl_remaining = trader.negative_cache.is_cached(
                    route_signature, failure_type="atomic_size_overflow"
                )
                if is_cached and cached_failure_type == "atomic_size_overflow":
                    stats['skips'].setdefault('cache_hit_size', 0)
                    stats['skips']['cache_hit_size'] += 1
                    continue  # SKIP
                
                # Optional: runtime 6024 cache (only if useSharedAccounts=True, but we use False)
                # Skipped for 2-swap cross-AMM
                
                # 10) Build atomic VT (blockhash is fetched inside _build_atomic_cycle_vt)
                # NOTE:
                # - Blockhash is fetched as late as possible (inside VT build) to minimize time gap
                # - In MODE=simulate we will also do an inline simulate here (research mode)
                # - In MODE=live we treat this as BUILD-ONLY gate (no simulate here; final-gate sim is in execute_prepared_bundle)
                vt, min_last_valid_block_height, fail_reason, fail_meta = await trader._build_atomic_cycle_vt(
                    opportunity, user_pubkey, leg_instructions=leg_instructions
                )
                
                # Handle size overflow: cache and SKIP
                if vt is None and fail_reason == "atomic_size_overflow":
                    trader.negative_cache.cache_route(route_signature, failure_type="atomic_size_overflow")
                    stats['skips'].setdefault('vt_size_overflow', 0)
                    stats['skips']['vt_size_overflow'] += 1
                    continue  # SKIP
                
                if vt is None:
                    stats['skips'].setdefault('vt_build_failed', 0)
                    stats['skips']['vt_build_failed'] += 1
                    continue  # SKIP

                # 11) MODE-SPECIFIC handling:
                # - MODE=simulate: full inline simulate (research mode)
                # - MODE=live: BUILD-ONLY gate (no simulate here, final-gate sim in execute_prepared_bundle)
                sim_result: Dict[str, Any]
                if getattr(trader, "mode", "simulate") == "simulate":
                    # INLINE SIMULATE (simulate mode only)
                    sim_result = await trader.solana.simulate_versioned_transaction(vt)
                    
                    if sim_result is None:
                        stats['skips'].setdefault('sim_rpc_none', 0)
                        stats['skips']['sim_rpc_none'] += 1
                        continue  # SKIP
                    
                    if not isinstance(sim_result, dict):
                        stats['skips'].setdefault('sim_invalid_type', 0)
                        stats['skips']['sim_invalid_type'] += 1
                        continue  # SKIP
                    
                    if sim_result.get("err"):
                        stats['skips'].setdefault('sim_err', 0)
                        stats['skips']['sim_err'] += 1
                        continue  # SKIP
                    
                    # SUCCESS (simulate mode): full build + simulate OK
                    stats['successes'] += 1
                    logger.info(
                        f"{colors['GREEN']}✓ Inline simulation SUCCESS{colors['RESET']}: "
                        f"{dex1}->{dex2} | "
                        f"Profit: {colors['GREEN']}{profit_bps} bps (${profit_usd:.4f}){colors['RESET']}"
                    )
                else:
                    # BUILD-ONLY SUCCESS (live mode): VT built and passed size/negative-cache gates
                    # No simulateTransaction here – final-gate sim is executed in execute_prepared_bundle().
                    sim_result = {}
                    stats['successes'] += 1  # Count as build_ok for summary in live mode
                    logger.info(
                        f"{colors['GREEN']}Bundle prepared (build-only gate passed){colors['RESET']}: "
                        f"{dex1}->{dex2} | "
                        f"Profit: {colors['GREEN']}{profit_bps} bps (${profit_usd:.4f}){colors['RESET']}"
                    )
                
                # SUCCESS PATH (both modes): create PreparedBundle and call callback
                from .trader import PreparedBundle  # Import here to avoid circular import
                bundle = PreparedBundle(
                    opportunity=opportunity,
                    leg_instructions=leg_instructions,
                    route_signature=route_signature,
                    min_last_valid_block_height=min_last_valid_block_height,
                    versioned_transaction=vt,  # Already signed by _build_atomic_cycle_vt
                    meta={
                        'raw_size_bytes': fail_meta.get('raw_size_bytes', 0) if fail_meta else 0,
                        'alts_count': len(vt.message.address_table_lookups) if vt else 0,
                        'plan_id': plan.get_plan_id(),
                        'dex1': dex1,
                        'dex2': dex2
                    }
                )
                
                if on_success:
                    await on_success(bundle, sim_result)
                    
            except asyncio.TimeoutError:
                stats['skips'].setdefault('timeout_other', 0)
                stats['skips']['timeout_other'] += 1
                continue  # SKIP silently
            except Exception as e:
                stats['errors'] += 1
                logger.debug(f"Error in inline iteration for plan {plan.cycle_mints[0][:8]}...: {e}")
                continue
        
        return stats