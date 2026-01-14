"""
Arbitrage opportunity finder.
Searches for cycles starting and ending in USDC (3-leg and 4-leg formats) with profit.
"""
import asyncio
import logging
from typing import List, Dict, Optional, Tuple, Callable, Awaitable
from dataclasses import dataclass
import time

from .jupiter_client import JupiterClient, JupiterQuote

logger = logging.getLogger(__name__)


@dataclass
class ArbitrageOpportunity:
    """Represents an arbitrage opportunity."""
    cycle: List[str]  # [USDC, X, Y, USDC] or [USDC, X, Y, Z, USDC] - 3-leg or 4-leg cycle starting and ending in USDC
    quotes: List[JupiterQuote]
    initial_amount: int
    final_amount: int
    profit_bps: int
    profit_usd: float
    price_impact_total: float
    timestamp: float
    
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
        cycles: List[List[str]] = None
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
        self.cycles = cycles or []  # Load cycles from config.json
    
    async def find_opportunities(
        self,
        start_token: str,
        amount: int,
        max_opportunities: int = 10,
        on_opportunity_found: Optional[Callable[[ArbitrageOpportunity], Awaitable[bool]]] = None
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
        
        # Use cycles from config.json (loaded via constructor)
        cycles = self.cycles
        
        if not cycles:
            logger.warning("No cycles configured. Please add cycles to config.json")
            return []
        
        logger.info(f"Searching {len(cycles)} cycles for arbitrage opportunities...")
        if on_opportunity_found:
            logger.debug("Callback on_opportunity_found is provided, will call it for each profitable opportunity")
        else:
            logger.debug("No callback provided, will collect all opportunities")
        
        # Check each cycle sequentially (no parallelism) with delays to avoid quota burn
        for cycle in cycles:
            result = await self._check_cycle(cycle, amount)
            
            if result and result.is_valid(self.min_profit_bps, self.min_profit_usd):
                opportunities.append(result)
                
                # If callback provided, call it immediately (processing will pause the search loop)
                if on_opportunity_found:
                    logger.info("Calling on_opportunity_found callback...")
                    try:
                        should_continue = await on_opportunity_found(result)
                        logger.info(f"Callback finished, should_continue={should_continue}")
                    except Exception as e:
                        logger.error(f"Error in on_opportunity_found callback: {e}", exc_info=True)
                        should_continue = True  # Continue on error to avoid blocking
                    
                    if not should_continue:
                        # Callback requested to stop searching
                        logger.info("Callback requested to stop searching")
                        break
                    # Skip delay if callback was called (processing already paused the loop)
                    continue
            
            # Delay between cycles for rate limiting (configurable via QUOTE_DELAY_SECONDS, default: 1.0 sec for 60 req/min)
            # Skip delay if callback was called (already continued above)
            await asyncio.sleep(self.quote_delay_seconds)
        
        # Sort by profit (descending)
        opportunities.sort(key=lambda x: x.profit_bps, reverse=True)
        
        return opportunities[:max_opportunities]
    
    def _generate_cycles(
        self,
        start_token: str,
        tokens: List[str],
        max_length: int
    ) -> List[List[str]]:
        """
        Generate cycles starting from start_token.
        
        Avoids trivial symmetric cycles (A -> B -> A).
        Supports cycles of length 3-4 legs.
        Limits total number of cycles to avoid combinatorial explosion.
        """
        cycles = []
        
        # Remove start_token from tokens list for intermediate steps
        other_tokens = [t for t in tokens if t != start_token]
        
        if len(other_tokens) < 2:
            return cycles
        
        # A -> B -> C -> A (3-leg cycles)
        # Skip trivial A -> B -> A cycles
        if max_length >= 3:
            for token1 in other_tokens:
                for token2 in other_tokens:
                    if token1 != token2:
                        cycle = [start_token, token1, token2, start_token]
                        cycles.append(cycle)
                        if len(cycles) >= self.max_cycles:
                            return cycles
        
        # A -> B -> C -> D -> A (4-leg cycles)
        if max_length >= 4 and len(other_tokens) >= 3:
            for token1 in other_tokens:
                for token2 in other_tokens:
                    if token1 == token2:
                        continue
                    for token3 in other_tokens:
                        if token3 == token1 or token3 == token2:
                            continue
                        cycle = [start_token, token1, token2, token3, start_token]
                        cycles.append(cycle)
                        if len(cycles) >= self.max_cycles:
                            return cycles
        
        return cycles
    
    async def _check_cycle(
        self,
        cycle: List[str],
        initial_amount: int,
        skip_delays: bool = False
    ) -> Optional[ArbitrageOpportunity]:
        """
        Check if a cycle is profitable.
        
        Args:
            cycle: List of token mints [USDC, X, Y, USDC] or [USDC, X, Y, Z, USDC] (3-leg or 4-leg cycle, all starting and ending in USDC)
            initial_amount: Starting amount in smallest unit
        
        Returns:
            ArbitrageOpportunity if profitable, None otherwise
        """
        if len(cycle) < 3:
            return None
        
        quotes = []
        current_amount = initial_amount
        
        try:
            # Get quotes for each leg sequentially with delays
            for i in range(len(cycle) - 1):
                input_mint = cycle[i]
                output_mint = cycle[i + 1]
                
                quote = await asyncio.wait_for(
                    self.jupiter.get_quote(input_mint, output_mint, current_amount, slippage_bps=self.slippage_bps),
                    timeout=self.quote_timeout
                )
                
                if quote is None or quote.out_amount == 0:
                    return None
                
                quotes.append(quote)
                current_amount = quote.out_amount
                
                # Delay between quote requests within a cycle (configurable via QUOTE_DELAY_SECONDS, default: 1.0 sec for 60 req/min)
                # Skip delays for fast retry checks
                if not skip_delays and i < len(cycle) - 2:  # Don't delay after last leg
                    await asyncio.sleep(self.quote_delay_seconds)
            
            # Calculate profit
            final_amount = current_amount
            profit_bps = self._calculate_profit_bps(initial_amount, final_amount)
            profit_usd = self._estimate_profit_usd(initial_amount, final_amount, cycle[0])
            price_impact_total = sum(q.price_impact_pct for q in quotes)
            
            opportunity = ArbitrageOpportunity(
                cycle=cycle,
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
            logger.debug(f"Cycle {cycle} quote timeout")
            return None
        except Exception as e:
            logger.debug(f"Cycle {cycle} check failed: {e}")
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
        # Simplified: uses SOL price from config for conversion
        # In production, should use price oracle based on token_mint for more accurate calculation
        profit_sol = (amount_out - amount_in) / 1e9
        return profit_sol * self.sol_price_usdc
