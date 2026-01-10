"""
Arbitrage opportunity finder.
Searches for A -> B -> C -> A cycles (3-leg cycles) with profit.
"""
import asyncio
import logging
from typing import List, Dict, Optional, Tuple, AsyncIterator
from dataclasses import dataclass
import time

from .jupiter_client import JupiterClient, JupiterQuote

logger = logging.getLogger(__name__)


@dataclass
class ArbitrageOpportunity:
    """Represents an arbitrage opportunity."""
    cycle: List[str]  # [token1, token2, token3, token1] - 3-leg cycle with 4 elements
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
        cycles: List[List[str]],  # Cycles in symbol format [["USDC", "SOL", "JUP", "USDC"], ...]
        tokens_config: Dict[str, str],  # Dictionary mapping symbol -> address
        min_profit_bps: int = 50,
        min_profit_usd: float = 0.1,
        max_cycle_length: int = 4,
        max_cycles: int = 100,
        quote_timeout: float = 5.0,
        slippage_bps: int = 50,
        sol_price_usdc: float = 100.0,
        quote_delay_seconds: float = 1.0
    ):
        """
        Initialize arbitrage finder.
        
        Args:
            jupiter_client: Jupiter API client
            tokens: List of token mint addresses
            cycles: List of cycles in symbol format (from config.json)
            tokens_config: Dictionary mapping token symbols to addresses
        """
        self.jupiter = jupiter_client
        self.tokens = tokens
        self.cycles_symbols = cycles  # Cycles in symbol format
        self.tokens_config = tokens_config
        self.min_profit_bps = min_profit_bps
        self.min_profit_usd = min_profit_usd
        self.max_cycle_length = max_cycle_length
        self.max_cycles = max_cycles
        self.quote_timeout = quote_timeout
        self.slippage_bps = slippage_bps
        self.sol_price_usdc = sol_price_usdc
        self.quote_delay_seconds = quote_delay_seconds
        
        # Convert cycles from symbols to addresses
        self.cycles_addresses = self.convert_cycles_to_addresses(cycles, tokens_config)
    
    def convert_cycles_to_addresses(self, cycles_symbols: List[List[str]], tokens_config: Dict[str, str]) -> List[List[str]]:
        """
        Convert cycles from symbols to addresses.
        
        Args:
            cycles_symbols: Cycles in symbol format [["USDC", "SOL", "JUP", "USDC"], ...]
            tokens_config: Dictionary mapping symbol -> address
        
        Returns:
            Cycles in address format [["address1", "address2", ...], ...]
        
        Raises:
            ValueError: If any token symbol is not found in tokens_config
        """
        cycles_addresses = []
        for cycle_symbols in cycles_symbols:
            cycle_addresses = []
            for symbol in cycle_symbols:
                if symbol not in tokens_config:
                    raise ValueError(f"Token {symbol} not found in config.tokens")
                cycle_addresses.append(tokens_config[symbol])
            cycles_addresses.append(cycle_addresses)
        return cycles_addresses
    
    async def find_opportunities(
        self,
        start_token: str,
        amount: int,
        max_opportunities: int = 10
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
        
        # Use cycles from config (already converted to addresses in init)
        cycles = self.cycles_addresses
        
        logger.info(f"Searching {len(cycles)} cycles for arbitrage opportunities...")
        
        # Check each cycle sequentially (no parallelism) with delays to avoid quota burn
        for cycle in cycles:
            result = await self._check_cycle(cycle, amount)
            
            if result and result.is_valid(self.min_profit_bps, self.min_profit_usd):
                opportunities.append(result)
            
            # Delay between cycles for rate limiting (configurable via QUOTE_DELAY_SECONDS, default: 1.0 sec for 60 req/min)
            await asyncio.sleep(self.quote_delay_seconds)
        
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
        
        # Use cycles from config (already converted to addresses in init)
        cycles = self.cycles_addresses
        
        logger.info(f"Searching {len(cycles)} cycles for arbitrage opportunities (stream mode)...")
        
        for cycle in cycles:
            result = await self._check_cycle(cycle, amount)
            
            if result and result.is_valid(self.min_profit_bps, self.min_profit_usd):
                found_count += 1
                yield result
                
                if found_count >= max_opportunities:
                    break
            
            # Delay between cycles for rate limiting (configurable via QUOTE_DELAY_SECONDS, default: 1.0 sec for 60 req/min)
            await asyncio.sleep(self.quote_delay_seconds)
    
    async def _check_cycle(
        self,
        cycle: List[str],
        initial_amount: int
    ) -> Optional[ArbitrageOpportunity]:
        """
        Check if a cycle is profitable.
        
        Args:
            cycle: List of token mints [A, B, C, A] (3-leg cycle, 4 elements total)
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
                if i < len(cycle) - 2:  # Don't delay after last leg
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
