"""
Tests for filtering unprofitable opportunities (negative profit).
"""
import pytest
from unittest.mock import AsyncMock
from src.arbitrage_finder import ArbitrageFinder, ArbitrageOpportunity
from src.jupiter_client import JupiterQuote


class TestNegativeProfitFiltering:
    """Tests to verify that unprofitable opportunities are filtered out."""
    
    @pytest.fixture
    def mock_jupiter(self):
        """Create a mock JupiterClient."""
        return AsyncMock()
    
    @pytest.fixture
    def finder(self, mock_jupiter):
        """Create an ArbitrageFinder with min_profit_usdc=0.03."""
        cycles = []
        return ArbitrageFinder(
            jupiter_client=mock_jupiter,
            tokens=[],
            min_profit_bps=0,  # Disabled for USDC-first check
            min_profit_usd=0.03,  # Minimum profit in USDC
            max_cycle_length=4,
            max_cycles=100,
            quote_timeout=5.0,
            slippage_bps=50,
            sol_price_usdc=100.0,
            quote_delay_seconds=0.0,
            cycles=cycles
        )
    
    def test_is_valid_rejects_negative_profit_usdc(self, finder):
        """Test: is_valid should reject opportunity with negative profit in USDC."""
        opp = ArbitrageOpportunity(
            cycle=[],
            quotes=[],
            initial_amount=1_000_000,
            final_amount=958_400,  # Loss: -4.16% = -0.0416 USDC
            profit_bps=-416,  # -416 bps
            profit_usd=-0.0416,  # Negative profit in USDC
            price_impact_total=0.5,
            timestamp=1234567890.0
        )
        
        # With min_profit_usdc=0.03, negative profit -0.0416 should be rejected
        # Check: -0.0416 < 0.03 = True -> should return False
        result = opp.is_valid(finder.min_profit_bps, finder.min_profit_usd)
        
        assert result is False, f"Unprofitable opportunity with profit_usd={opp.profit_usd} should not be valid with min_profit_usdc={finder.min_profit_usd}"
    
    def test_is_valid_rejects_zero_profit_when_min_profit_positive(self, finder):
        """Test: is_valid should reject opportunity with zero profit if min_profit_usdc > 0."""
        opp = ArbitrageOpportunity(
            cycle=[],
            quotes=[],
            initial_amount=1_000_000,
            final_amount=1_000_000,  # Zero profit
            profit_bps=0,
            profit_usd=0.0,  # Zero profit
            price_impact_total=0.5,
            timestamp=1234567890.0
        )
        
        # With min_profit_usdc=0.03, zero profit 0.0 should be rejected
        result = opp.is_valid(finder.min_profit_bps, finder.min_profit_usd)
        
        assert result is False, f"Zero profit should not be valid with min_profit_usdc={finder.min_profit_usd}"
    
    def test_is_valid_accepts_positive_profit_above_threshold(self, finder):
        """Test: is_valid should accept opportunity with profit above threshold."""
        opp = ArbitrageOpportunity(
            cycle=[],
            quotes=[],
            initial_amount=1_000_000,
            final_amount=1_050_000,  # Profit: +5% = +0.05 USDC
            profit_bps=500,
            profit_usd=0.05,  # Positive profit > 0.03
            price_impact_total=0.5,
            timestamp=1234567890.0
        )
        
        result = opp.is_valid(finder.min_profit_bps, finder.min_profit_usd)
        
        assert result is True, f"Profit {opp.profit_usd} should be valid with min_profit_usdc={finder.min_profit_usd}"
    
    def test_is_valid_rejects_small_positive_profit_below_threshold(self, finder):
        """Test: is_valid should reject opportunity with small positive profit below threshold."""
        opp = ArbitrageOpportunity(
            cycle=[],
            quotes=[],
            initial_amount=1_000_000,
            final_amount=1_002_000,  # Profit: +0.2% = +0.002 USDC
            profit_bps=20,
            profit_usd=0.002,  # Positive but less than 0.03
            price_impact_total=0.5,
            timestamp=1234567890.0
        )
        
        result = opp.is_valid(finder.min_profit_bps, finder.min_profit_usd)
        
        assert result is False, f"Profit {opp.profit_usd} should not be valid with min_profit_usdc={finder.min_profit_usd}"
    
    def test_is_valid_edge_case_exactly_at_threshold(self, finder):
        """Test: is_valid edge case - profit exactly equals threshold."""
        opp = ArbitrageOpportunity(
            cycle=[],
            quotes=[],
            initial_amount=1_000_000,
            final_amount=1_030_000,  # Profit: +3% = +0.03 USDC
            profit_bps=300,
            profit_usd=0.03,  # Exactly equals threshold
            price_impact_total=0.5,
            timestamp=1234567890.0
        )
        
        # Check: profit_usd >= min_profit_usdc
        # 0.03 >= 0.03 = True, so should be valid
        result = opp.is_valid(finder.min_profit_bps, finder.min_profit_usd)
        
        assert result is True, f"Profit exactly equal to threshold should be valid"
    
    @pytest.mark.asyncio
    async def test_find_opportunities_filters_negative_profit(self, finder, mock_jupiter, usdc_mint, sol_mint, jup_mint):
        """Test: find_opportunities should filter out unprofitable opportunities."""
        # Add cycle that will be unprofitable
        cycle = [usdc_mint, sol_mint, jup_mint, usdc_mint]
        finder.cycles = [cycle]
        
        # Mock quotes for unprofitable cycle
        quote1 = JupiterQuote(
            input_mint=usdc_mint,
            output_mint=sol_mint,
            in_amount=1_000_000,  # 1 USDC
            out_amount=10_000_000,  # 0.01 SOL
            price_impact_pct=0.1,
            route_plan=[]
        )
        quote2 = JupiterQuote(
            input_mint=sol_mint,
            output_mint=jup_mint,
            in_amount=10_000_000,
            out_amount=20_000_000,
            price_impact_pct=0.2,
            route_plan=[]
        )
        quote3 = JupiterQuote(
            input_mint=jup_mint,
            output_mint=usdc_mint,
            in_amount=20_000_000,
            out_amount=958_400,  # 0.9584 USDC (loss -4.16%)
            price_impact_pct=0.2,
            route_plan=[]
        )
        
        mock_jupiter.get_quote.side_effect = [quote1, quote2, quote3]
        
        opportunities = await finder.find_opportunities(usdc_mint, 1_000_000)
        
        # Unprofitable opportunity should be filtered out
        assert len(opportunities) == 0, "Unprofitable opportunities should be filtered out"
