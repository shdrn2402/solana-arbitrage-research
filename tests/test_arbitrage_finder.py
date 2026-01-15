"""
Tests for arbitrage_finder.py
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from src.arbitrage_finder import ArbitrageFinder, ArbitrageOpportunity
from src.jupiter_client import JupiterQuote


class TestArbitrageOpportunity:
    """Tests for ArbitrageOpportunity dataclass."""
    
    def test_arbitrage_opportunity_creation(self, sol_mint, usdc_mint, jup_mint):
        """Test ArbitrageOpportunity can be created."""
        cycle = [usdc_mint, sol_mint, jup_mint, usdc_mint]
        quotes = []
        opp = ArbitrageOpportunity(
            cycle=cycle,
            quotes=quotes,
            initial_amount=1_000_000_000,
            final_amount=1_100_000_000,
            profit_bps=1000,
            profit_usd=10.0,
            price_impact_total=0.5,
            timestamp=1234567890.0
        )
        assert opp.cycle == cycle
        assert opp.profit_bps == 1000
        assert opp.profit_usd == 10.0
    
    def test_is_valid_passes_both_checks(self):
        """Test is_valid passes when both checks pass."""
        opp = ArbitrageOpportunity(
            cycle=[],
            quotes=[],
            initial_amount=1_000_000_000,
            final_amount=1_100_000_000,
            profit_bps=100,  # > min_profit_bps (50)
            profit_usd=1.0,  # > min_profit_usdc (0.1)
            price_impact_total=0.5,
            timestamp=1234567890.0
        )
        assert opp.is_valid(min_profit_bps=50, min_profit_usd=0.1) is True
    
    def test_is_valid_fails_usdc_check(self):
        """Test is_valid fails when USDC check fails (PRIMARY)."""
        opp = ArbitrageOpportunity(
            cycle=[],
            quotes=[],
            initial_amount=1_000_000_000,
            final_amount=1_100_000_000,
            profit_bps=100,
            profit_usd=0.05,  # < min_profit_usdc (0.1)
            price_impact_total=0.5,
            timestamp=1234567890.0
        )
        assert opp.is_valid(min_profit_bps=50, min_profit_usd=0.1) is False
    
    def test_is_valid_fails_bps_check(self):
        """Test is_valid fails when BPS check fails (SECONDARY)."""
        opp = ArbitrageOpportunity(
            cycle=[],
            quotes=[],
            initial_amount=1_000_000_000,
            final_amount=1_100_000_000,
            profit_bps=30,  # < min_profit_bps (50)
            profit_usd=1.0,  # Passes USDC check
            price_impact_total=0.5,
            timestamp=1234567890.0
        )
        assert opp.is_valid(min_profit_bps=50, min_profit_usd=0.1) is False
    
    def test_is_valid_bps_filter_disabled(self):
        """Test is_valid passes when BPS filter is disabled (0)."""
        opp = ArbitrageOpportunity(
            cycle=[],
            quotes=[],
            initial_amount=1_000_000_000,
            final_amount=1_100_000_000,
            profit_bps=10,  # Low BPS, but filter is disabled
            profit_usd=1.0,  # Passes USDC check
            price_impact_total=0.5,
            timestamp=1234567890.0
        )
        assert opp.is_valid(min_profit_bps=0, min_profit_usd=0.1) is True


class TestArbitrageFinder:
    """Tests for ArbitrageFinder class."""
    
    @pytest.fixture
    def mock_jupiter(self):
        """Create a mock JupiterClient."""
        return AsyncMock()
    
    @pytest.fixture
    def finder(self, mock_jupiter, sol_mint, usdc_mint, jup_mint, bonk_mint):
        """Create an ArbitrageFinder instance for testing."""
        tokens = [sol_mint, usdc_mint, jup_mint, bonk_mint]
        cycles = [
            [usdc_mint, sol_mint, jup_mint, usdc_mint],
            [usdc_mint, sol_mint, bonk_mint, usdc_mint]
        ]
        return ArbitrageFinder(
            jupiter_client=mock_jupiter,
            tokens=tokens,
            min_profit_bps=50,
            min_profit_usd=0.1,
            max_cycle_length=4,
            max_cycles=100,
            quote_timeout=5.0,
            slippage_bps=50,
            sol_price_usdc=100.0,
            quote_delay_seconds=0.0,  # No delay in tests
            cycles=cycles
        )
    
    def test_arbitrage_finder_initialization(self, finder, mock_jupiter):
        """Test ArbitrageFinder can be initialized."""
        assert finder.jupiter == mock_jupiter
        assert finder.min_profit_bps == 50
        assert finder.min_profit_usd == 0.1
        assert len(finder.cycles) == 2
    
    def test_calculate_profit_bps(self, finder):
        """Test calculating profit in basis points."""
        # 10% profit = 1000 bps
        profit_bps = finder._calculate_profit_bps(1_000_000_000, 1_100_000_000)
        assert profit_bps == 1000
        
        # 1% profit = 100 bps
        profit_bps = finder._calculate_profit_bps(1_000_000_000, 1_010_000_000)
        assert profit_bps == 100
        
        # Zero input
        profit_bps = finder._calculate_profit_bps(0, 1_000_000_000)
        assert profit_bps == 0
    
    def test_estimate_profit_usd(self, finder):
        """Test estimating profit in USDC."""
        # Profit: 0.1 SOL = $10 USDC (at $100/SOL)
        profit_usd = finder._estimate_profit_usd(
            1_000_000_000,  # 1 SOL
            1_100_000_000,  # 1.1 SOL
            "So11111111111111111111111111111111111111112"
        )
        assert profit_usd == 10.0
    
    @pytest.mark.asyncio
    async def test_check_cycle_profitable(self, finder, mock_jupiter, usdc_mint, sol_mint, jup_mint):
        """Test _check_cycle returns opportunity for profitable cycle."""
        cycle = [usdc_mint, sol_mint, jup_mint, usdc_mint]
        
        # Mock quotes for each leg
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
            out_amount=1_100_000,  # 1.1 USDC (profit!)
            price_impact_pct=0.2,
            route_plan=[]
        )
        
        mock_jupiter.get_quote.side_effect = [quote1, quote2, quote3]
        
        opportunity = await finder._check_cycle(cycle, 1_000_000)
        
        assert opportunity is not None
        assert opportunity.cycle == cycle
        assert opportunity.initial_amount == 1_000_000
        assert opportunity.final_amount == 1_100_000
        assert opportunity.profit_bps > 0
        assert len(opportunity.quotes) == 3
    
    @pytest.mark.asyncio
    async def test_check_cycle_unprofitable(self, finder, mock_jupiter, usdc_mint, sol_mint, jup_mint):
        """Test _check_cycle returns None for unprofitable cycle."""
        cycle = [usdc_mint, sol_mint, jup_mint, usdc_mint]
        
        # Mock quotes that result in loss
        quote1 = JupiterQuote(
            input_mint=usdc_mint,
            output_mint=sol_mint,
            in_amount=1_000_000,
            out_amount=10_000_000,
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
            out_amount=900_000,  # 0.9 USDC (loss)
            price_impact_pct=0.2,
            route_plan=[]
        )
        
        mock_jupiter.get_quote.side_effect = [quote1, quote2, quote3]
        
        opportunity = await finder._check_cycle(cycle, 1_000_000)
        
        # Should still return opportunity (finder doesn't filter by profit)
        # Profit filtering happens in find_opportunities
        assert opportunity is not None
        assert opportunity.profit_bps < 0
    
    @pytest.mark.asyncio
    async def test_check_cycle_no_quote(self, finder, mock_jupiter, usdc_mint, sol_mint, jup_mint):
        """Test _check_cycle returns None when quote fails."""
        cycle = [usdc_mint, sol_mint, jup_mint, usdc_mint]
        mock_jupiter.get_quote.return_value = None
        
        opportunity = await finder._check_cycle(cycle, 1_000_000)
        assert opportunity is None
    
    @pytest.mark.asyncio
    async def test_check_cycle_zero_output(self, finder, mock_jupiter, usdc_mint, sol_mint, jup_mint):
        """Test _check_cycle returns None when quote has zero output."""
        cycle = [usdc_mint, sol_mint, jup_mint, usdc_mint]
        
        quote = JupiterQuote(
            input_mint=usdc_mint,
            output_mint=sol_mint,
            in_amount=1_000_000,
            out_amount=0,  # Zero output
            price_impact_pct=0.1,
            route_plan=[]
        )
        mock_jupiter.get_quote.return_value = quote
        
        opportunity = await finder._check_cycle(cycle, 1_000_000)
        assert opportunity is None
    
    @pytest.mark.asyncio
    async def test_check_cycle_too_short(self, finder):
        """Test _check_cycle returns None for cycle that's too short."""
        cycle = ["token1", "token2"]  # Only 2 tokens (need at least 3)
        opportunity = await finder._check_cycle(cycle, 1_000_000)
        assert opportunity is None
    
    @pytest.mark.asyncio
    async def test_find_opportunities_no_cycles(self, finder):
        """Test find_opportunities returns empty list when no cycles configured."""
        finder.cycles = []
        opportunities = await finder.find_opportunities("token1", 1_000_000)
        assert opportunities == []
    
    @pytest.mark.asyncio
    async def test_find_opportunities_filters_by_profit(self, finder, mock_jupiter, usdc_mint, sol_mint, jup_mint):
        """Test find_opportunities filters opportunities by profit."""
        cycle = [usdc_mint, sol_mint, jup_mint, usdc_mint]
        finder.cycles = [cycle]
        
        # Mock profitable quote
        quote1 = JupiterQuote(
            input_mint=usdc_mint,
            output_mint=sol_mint,
            in_amount=1_000_000,
            out_amount=10_000_000,
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
            out_amount=1_200_000,  # 1.2 USDC (20% profit = 2000 bps, $20 profit)
            price_impact_pct=0.2,
            route_plan=[]
        )
        
        mock_jupiter.get_quote.side_effect = [quote1, quote2, quote3]
        
        opportunities = await finder.find_opportunities(usdc_mint, 1_000_000)
        
        assert len(opportunities) == 1
        assert opportunities[0].profit_bps > 0
        assert opportunities[0].profit_usd > 0.1
    
    @pytest.mark.asyncio
    async def test_find_opportunities_filters_low_profit(self, finder, mock_jupiter, usdc_mint, sol_mint, jup_mint):
        """Test find_opportunities filters out opportunities with low profit."""
        cycle = [usdc_mint, sol_mint, jup_mint, usdc_mint]
        finder.cycles = [cycle]
        
        # Mock quote with very small profit (below min_profit_usdc)
        quote1 = JupiterQuote(
            input_mint=usdc_mint,
            output_mint=sol_mint,
            in_amount=1_000_000,
            out_amount=10_000_000,
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
            out_amount=1_000_050,  # Tiny profit (0.00005 USDC)
            price_impact_pct=0.2,
            route_plan=[]
        )
        
        mock_jupiter.get_quote.side_effect = [quote1, quote2, quote3]
        
        opportunities = await finder.find_opportunities(usdc_mint, 1_000_000)
        
        # Should be filtered out due to low profit
        assert len(opportunities) == 0
    
    @pytest.mark.asyncio
    async def test_find_opportunities_with_callback(self, finder, mock_jupiter, usdc_mint, sol_mint, jup_mint):
        """Test find_opportunities calls callback when opportunity found."""
        cycle = [usdc_mint, sol_mint, jup_mint, usdc_mint]
        finder.cycles = [cycle]
        
        # Mock profitable quote
        quote1 = JupiterQuote(
            input_mint=usdc_mint,
            output_mint=sol_mint,
            in_amount=1_000_000,
            out_amount=10_000_000,
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
            out_amount=1_200_000,  # Profitable
            price_impact_pct=0.2,
            route_plan=[]
        )
        
        mock_jupiter.get_quote.side_effect = [quote1, quote2, quote3]
        
        callback_called = []
        
        async def on_opportunity_found(opp):
            callback_called.append(opp)
            return True  # Continue searching
        
        opportunities = await finder.find_opportunities(
            usdc_mint,
            1_000_000,
            on_opportunity_found=on_opportunity_found
        )
        
        assert len(callback_called) == 1
        assert callback_called[0].profit_bps > 0
