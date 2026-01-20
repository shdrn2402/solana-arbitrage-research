"""
Tests for arbitrage_finder.py - 2-swap execution plans architecture.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from src.arbitrage_finder import (
    ArbitrageFinder, 
    ArbitrageOpportunity, 
    ExecutionPlan, 
    ExecutionLeg
)
from src.jupiter_client import JupiterQuote


class TestExecutionLeg:
    """Tests for ExecutionLeg dataclass."""
    
    def test_execution_leg_creation(self, sol_mint, usdc_mint):
        """Test ExecutionLeg can be created with valid values."""
        leg = ExecutionLeg(
            from_mint=usdc_mint,
            to_mint=sol_mint,
            max_hops=1
        )
        assert leg.from_mint == usdc_mint
        assert leg.to_mint == sol_mint
        assert leg.max_hops == 1
    
    def test_execution_leg_rejects_non_1hop(self, sol_mint, usdc_mint):
        """Test ExecutionLeg raises error if max_hops != 1."""
        with pytest.raises(ValueError, match="max_hops must be 1"):
            ExecutionLeg(
                from_mint=usdc_mint,
                to_mint=sol_mint,
                max_hops=2  # Invalid: must be 1
            )


class TestExecutionPlan:
    """Tests for ExecutionPlan dataclass."""
    
    def test_execution_plan_creation(self, sol_mint, usdc_mint):
        """Test ExecutionPlan can be created with valid 2-leg cycle."""
        leg1 = ExecutionLeg(from_mint=usdc_mint, to_mint=sol_mint, max_hops=1)
        leg2 = ExecutionLeg(from_mint=sol_mint, to_mint=usdc_mint, max_hops=1)
        plan = ExecutionPlan(
            cycle_mints=[usdc_mint, sol_mint, usdc_mint],
            legs=[leg1, leg2],
            atomic=True,
            use_shared_accounts=False
        )
        assert len(plan.legs) == 2
        assert len(plan.cycle_mints) == 3
        assert plan.atomic is True
        assert plan.use_shared_accounts is False
    
    def test_execution_plan_rejects_wrong_leg_count(self, sol_mint, usdc_mint):
        """Test ExecutionPlan raises error if legs count != 2."""
        leg1 = ExecutionLeg(from_mint=usdc_mint, to_mint=sol_mint, max_hops=1)
        with pytest.raises(ValueError, match="must have exactly 2 legs"):
            ExecutionPlan(
                cycle_mints=[usdc_mint, sol_mint, usdc_mint],
                legs=[leg1],  # Only 1 leg - invalid
                atomic=True,
                use_shared_accounts=False
            )
    
    def test_execution_plan_rejects_wrong_cycle_length(self, sol_mint, usdc_mint):
        """Test ExecutionPlan raises error if cycle_mints length != 3."""
        leg1 = ExecutionLeg(from_mint=usdc_mint, to_mint=sol_mint, max_hops=1)
        leg2 = ExecutionLeg(from_mint=sol_mint, to_mint=usdc_mint, max_hops=1)
        with pytest.raises(ValueError, match="must have 3 tokens"):
            ExecutionPlan(
                cycle_mints=[usdc_mint, sol_mint],  # Only 2 tokens - invalid
                legs=[leg1, leg2],
                atomic=True,
                use_shared_accounts=False
            )
    
    def test_execution_plan_rejects_start_end_mismatch(self, sol_mint, usdc_mint, jup_mint):
        """Test ExecutionPlan raises error if start != end token."""
        leg1 = ExecutionLeg(from_mint=usdc_mint, to_mint=sol_mint, max_hops=1)
        leg2 = ExecutionLeg(from_mint=sol_mint, to_mint=jup_mint, max_hops=1)
        with pytest.raises(ValueError, match="must start and end with the same token"):
            ExecutionPlan(
                cycle_mints=[usdc_mint, sol_mint, jup_mint],  # Start != end
                legs=[leg1, leg2],
                atomic=True,
                use_shared_accounts=False
            )
    
    def test_execution_plan_rejects_non_atomic(self, sol_mint, usdc_mint):
        """Test ExecutionPlan raises error if atomic=False."""
        leg1 = ExecutionLeg(from_mint=usdc_mint, to_mint=sol_mint, max_hops=1)
        leg2 = ExecutionLeg(from_mint=sol_mint, to_mint=usdc_mint, max_hops=1)
        with pytest.raises(ValueError, match="must be atomic"):
            ExecutionPlan(
                cycle_mints=[usdc_mint, sol_mint, usdc_mint],
                legs=[leg1, leg2],
                atomic=False,  # Invalid: must be True
                use_shared_accounts=False
            )
    
    def test_execution_plan_rejects_shared_accounts(self, sol_mint, usdc_mint):
        """Test ExecutionPlan raises error if use_shared_accounts=True."""
        leg1 = ExecutionLeg(from_mint=usdc_mint, to_mint=sol_mint, max_hops=1)
        leg2 = ExecutionLeg(from_mint=sol_mint, to_mint=usdc_mint, max_hops=1)
        with pytest.raises(ValueError, match="must have use_shared_accounts=False"):
            ExecutionPlan(
                cycle_mints=[usdc_mint, sol_mint, usdc_mint],
                legs=[leg1, leg2],
                atomic=True,
                use_shared_accounts=True  # Invalid: must be False
            )
    
    def test_execution_plan_get_plan_id(self, sol_mint, usdc_mint):
        """Test ExecutionPlan.get_plan_id generates unique identifier."""
        leg1 = ExecutionLeg(from_mint=usdc_mint, to_mint=sol_mint, max_hops=1)
        leg2 = ExecutionLeg(from_mint=sol_mint, to_mint=usdc_mint, max_hops=1)
        plan = ExecutionPlan(
            cycle_mints=[usdc_mint, sol_mint, usdc_mint],
            legs=[leg1, leg2],
            atomic=True,
            use_shared_accounts=False
        )
        plan.dex1 = "Raydium"
        plan.dex2 = "Orca"
        
        plan_id = plan.get_plan_id()
        assert "Raydium" in plan_id
        assert "Orca" in plan_id
        assert usdc_mint[:8] in plan_id or "USDC" in plan_id


class TestArbitrageOpportunity:
    """Tests for ArbitrageOpportunity dataclass."""
    
    def test_arbitrage_opportunity_creation(self, sol_mint, usdc_mint):
        """Test ArbitrageOpportunity can be created with execution_plan."""
        leg1 = ExecutionLeg(from_mint=usdc_mint, to_mint=sol_mint, max_hops=1)
        leg2 = ExecutionLeg(from_mint=sol_mint, to_mint=usdc_mint, max_hops=1)
        execution_plan = ExecutionPlan(
            cycle_mints=[usdc_mint, sol_mint, usdc_mint],
            legs=[leg1, leg2],
            atomic=True,
            use_shared_accounts=False
        )
        quotes = []
        opp = ArbitrageOpportunity(
            execution_plan=execution_plan,
            quotes=quotes,
            initial_amount=1_000_000,
            final_amount=1_100_000,
            profit_bps=1000,
            profit_usd=0.1,
            price_impact_total=0.5,
            timestamp=1234567890.0
        )
        assert opp.execution_plan == execution_plan
        assert opp.cycle == [usdc_mint, sol_mint, usdc_mint]  # Backward compatibility
        assert opp.profit_bps == 1000
        assert opp.profit_usd == 0.1
    
    def test_is_valid_passes_both_checks(self, sol_mint, usdc_mint):
        """Test is_valid passes when both checks pass."""
        leg1 = ExecutionLeg(from_mint=usdc_mint, to_mint=sol_mint, max_hops=1)
        leg2 = ExecutionLeg(from_mint=sol_mint, to_mint=usdc_mint, max_hops=1)
        execution_plan = ExecutionPlan(
            cycle_mints=[usdc_mint, sol_mint, usdc_mint],
            legs=[leg1, leg2],
            atomic=True,
            use_shared_accounts=False
        )
        opp = ArbitrageOpportunity(
            execution_plan=execution_plan,
            quotes=[],
            initial_amount=1_000_000,
            final_amount=1_100_000,
            profit_bps=100,  # > min_profit_bps (50)
            profit_usd=1.0,  # > min_profit_usdc (0.1)
            price_impact_total=0.5,
            timestamp=1234567890.0
        )
        assert opp.is_valid(min_profit_bps=50, min_profit_usd=0.1) is True
    
    def test_is_valid_fails_usdc_check(self, sol_mint, usdc_mint):
        """Test is_valid fails when USDC check fails (PRIMARY)."""
        leg1 = ExecutionLeg(from_mint=usdc_mint, to_mint=sol_mint, max_hops=1)
        leg2 = ExecutionLeg(from_mint=sol_mint, to_mint=usdc_mint, max_hops=1)
        execution_plan = ExecutionPlan(
            cycle_mints=[usdc_mint, sol_mint, usdc_mint],
            legs=[leg1, leg2],
            atomic=True,
            use_shared_accounts=False
        )
        opp = ArbitrageOpportunity(
            execution_plan=execution_plan,
            quotes=[],
            initial_amount=1_000_000,
            final_amount=1_100_000,
            profit_bps=100,
            profit_usd=0.05,  # < min_profit_usdc (0.1)
            price_impact_total=0.5,
            timestamp=1234567890.0
        )
        assert opp.is_valid(min_profit_bps=50, min_profit_usd=0.1) is False
    
    def test_is_valid_fails_bps_check(self, sol_mint, usdc_mint):
        """Test is_valid fails when BPS check fails (SECONDARY)."""
        leg1 = ExecutionLeg(from_mint=usdc_mint, to_mint=sol_mint, max_hops=1)
        leg2 = ExecutionLeg(from_mint=sol_mint, to_mint=usdc_mint, max_hops=1)
        execution_plan = ExecutionPlan(
            cycle_mints=[usdc_mint, sol_mint, usdc_mint],
            legs=[leg1, leg2],
            atomic=True,
            use_shared_accounts=False
        )
        opp = ArbitrageOpportunity(
            execution_plan=execution_plan,
            quotes=[],
            initial_amount=1_000_000,
            final_amount=1_100_000,
            profit_bps=30,  # < min_profit_bps (50)
            profit_usd=1.0,  # Passes USDC check
            price_impact_total=0.5,
            timestamp=1234567890.0
        )
        assert opp.is_valid(min_profit_bps=50, min_profit_usd=0.1) is False
    
    def test_is_valid_bps_filter_disabled(self, sol_mint, usdc_mint):
        """Test is_valid passes when BPS filter is disabled (0)."""
        leg1 = ExecutionLeg(from_mint=usdc_mint, to_mint=sol_mint, max_hops=1)
        leg2 = ExecutionLeg(from_mint=sol_mint, to_mint=usdc_mint, max_hops=1)
        execution_plan = ExecutionPlan(
            cycle_mints=[usdc_mint, sol_mint, usdc_mint],
            legs=[leg1, leg2],
            atomic=True,
            use_shared_accounts=False
        )
        opp = ArbitrageOpportunity(
            execution_plan=execution_plan,
            quotes=[],
            initial_amount=1_000_000,
            final_amount=1_100_000,
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
    def finder(self, mock_jupiter, sol_mint, usdc_mint):
        """Create an ArbitrageFinder instance for testing."""
        tokens = [sol_mint, usdc_mint]
        leg1 = ExecutionLeg(from_mint=usdc_mint, to_mint=sol_mint, max_hops=1)
        leg2 = ExecutionLeg(from_mint=sol_mint, to_mint=usdc_mint, max_hops=1)
        execution_plan = ExecutionPlan(
            cycle_mints=[usdc_mint, sol_mint, usdc_mint],
            legs=[leg1, leg2],
            atomic=True,
            use_shared_accounts=False
        )
        execution_plans = [execution_plan]
        return ArbitrageFinder(
            jupiter_client=mock_jupiter,
            tokens=tokens,
            min_profit_bps=50,
            min_profit_usd=0.1,
            max_cycle_length=3,  # 2-swap: 3 tokens
            max_cycles=100,
            quote_timeout=5.0,
            slippage_bps=50,
            sol_price_usdc=100.0,
            quote_delay_seconds=0.0,  # No delay in tests
            execution_plans=execution_plans
        )
    
    def test_arbitrage_finder_initialization(self, finder, mock_jupiter):
        """Test ArbitrageFinder can be initialized."""
        assert finder.jupiter == mock_jupiter
        assert finder.min_profit_bps == 50
        assert finder.min_profit_usd == 0.1
        assert len(finder.execution_plans) == 1
    
    def test_calculate_profit_bps(self, finder):
        """Test calculating profit in basis points."""
        # 10% profit = 1000 bps
        profit_bps = finder._calculate_profit_bps(1_000_000, 1_100_000)
        assert profit_bps == 1000
        
        # 1% profit = 100 bps
        profit_bps = finder._calculate_profit_bps(1_000_000, 1_010_000)
        assert profit_bps == 100
        
        # Zero input
        profit_bps = finder._calculate_profit_bps(0, 1_000_000)
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
    
    def test_enforce_1hop_check_passes_valid_1hop(self, finder, usdc_mint, sol_mint):
        """Test _enforce_1hop_check passes for valid 1-hop route."""
        quote = JupiterQuote(
            input_mint=usdc_mint,
            output_mint=sol_mint,
            in_amount=1_000_000,
            out_amount=10_000_000,
            price_impact_pct=0.1,
            route_plan=[{
                'swapInfo': {
                    'inputMint': usdc_mint,
                    'outputMint': sol_mint,
                    'ammKey': '675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8'
                }
            }]
        )
        
        result = finder._enforce_1hop_check(quote, usdc_mint, sol_mint)
        assert result is True
    
    def test_enforce_1hop_check_rejects_multi_hop(self, finder, usdc_mint, sol_mint):
        """Test _enforce_1hop_check rejects multi-hop route."""
        quote = JupiterQuote(
            input_mint=usdc_mint,
            output_mint=sol_mint,
            in_amount=1_000_000,
            out_amount=10_000_000,
            price_impact_pct=0.1,
            route_plan=[
                {'swapInfo': {'inputMint': usdc_mint, 'outputMint': 'intermediate'}},
                {'swapInfo': {'inputMint': 'intermediate', 'outputMint': sol_mint}}
            ]  # 2 hops - invalid
        )
        
        result = finder._enforce_1hop_check(quote, usdc_mint, sol_mint)
        assert result is False
    
    def test_enforce_1hop_check_rejects_empty_route_plan(self, finder, usdc_mint, sol_mint):
        """Test _enforce_1hop_check rejects empty route_plan."""
        quote = JupiterQuote(
            input_mint=usdc_mint,
            output_mint=sol_mint,
            in_amount=1_000_000,
            out_amount=10_000_000,
            price_impact_pct=0.1,
            route_plan=[]  # Empty - invalid
        )
        
        result = finder._enforce_1hop_check(quote, usdc_mint, sol_mint)
        assert result is False
    
    def test_enforce_1hop_check_rejects_mismatched_mints(self, finder, usdc_mint, sol_mint, jup_mint):
        """Test _enforce_1hop_check rejects route with mismatched mints."""
        quote = JupiterQuote(
            input_mint=usdc_mint,
            output_mint=sol_mint,
            in_amount=1_000_000,
            out_amount=10_000_000,
            price_impact_pct=0.1,
            route_plan=[{
                'swapInfo': {
                    'inputMint': usdc_mint,
                    'outputMint': jup_mint,  # Wrong output mint
                    'ammKey': '675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8'
                }
            }]
        )
        
        result = finder._enforce_1hop_check(quote, usdc_mint, sol_mint)
        assert result is False
    
    @pytest.mark.asyncio
    async def test_check_execution_plan_profitable(self, finder, mock_jupiter, usdc_mint, sol_mint):
        """Test _check_execution_plan returns opportunity for profitable plan."""
        leg1 = ExecutionLeg(from_mint=usdc_mint, to_mint=sol_mint, max_hops=1)
        leg2 = ExecutionLeg(from_mint=sol_mint, to_mint=usdc_mint, max_hops=1)
        execution_plan = ExecutionPlan(
            cycle_mints=[usdc_mint, sol_mint, usdc_mint],
            legs=[leg1, leg2],
            atomic=True,
            use_shared_accounts=False
        )
        
        # Mock quotes for each leg (1-hop each)
        quote1 = JupiterQuote(
            input_mint=usdc_mint,
            output_mint=sol_mint,
            in_amount=1_000_000,  # 1 USDC
            out_amount=10_000_000,  # 0.01 SOL
            price_impact_pct=0.1,
            route_plan=[{
                'swapInfo': {
                    'inputMint': usdc_mint,
                    'outputMint': sol_mint,
                    'ammKey': '675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8'
                }
            }]
        )
        quote2 = JupiterQuote(
            input_mint=sol_mint,
            output_mint=usdc_mint,
            in_amount=10_000_000,
            out_amount=1_100_000,  # 1.1 USDC (profit!)
            price_impact_pct=0.2,
            route_plan=[{
                'swapInfo': {
                    'inputMint': sol_mint,
                    'outputMint': usdc_mint,
                    'ammKey': '9W959DqEETiGZocYWCQPaJ6sBmUzgfxXfqGeTEdp3aQP'
                }
            }]
        )
        
        mock_jupiter.get_quote.side_effect = [quote1, quote2]
        
        opportunity = await finder._check_execution_plan(execution_plan, 1_000_000)
        
        assert opportunity is not None
        assert opportunity.execution_plan == execution_plan
        assert opportunity.initial_amount == 1_000_000
        assert opportunity.final_amount == 1_100_000
        assert opportunity.profit_bps > 0
        assert len(opportunity.quotes) == 2
    
    @pytest.mark.asyncio
    async def test_check_execution_plan_rejects_multi_hop(self, finder, mock_jupiter, usdc_mint, sol_mint):
        """Test _check_execution_plan rejects plan with multi-hop quote."""
        leg1 = ExecutionLeg(from_mint=usdc_mint, to_mint=sol_mint, max_hops=1)
        leg2 = ExecutionLeg(from_mint=sol_mint, to_mint=usdc_mint, max_hops=1)
        execution_plan = ExecutionPlan(
            cycle_mints=[usdc_mint, sol_mint, usdc_mint],
            legs=[leg1, leg2],
            atomic=True,
            use_shared_accounts=False
        )
        
        # Mock quote with multi-hop route (should be rejected)
        quote1 = JupiterQuote(
            input_mint=usdc_mint,
            output_mint=sol_mint,
            in_amount=1_000_000,
            out_amount=10_000_000,
            price_impact_pct=0.1,
            route_plan=[
                {'swapInfo': {'inputMint': usdc_mint, 'outputMint': 'intermediate'}},
                {'swapInfo': {'inputMint': 'intermediate', 'outputMint': sol_mint}}
            ]  # 2 hops - should be rejected
        )
        
        mock_jupiter.get_quote.return_value = quote1
        
        opportunity = await finder._check_execution_plan(execution_plan, 1_000_000)
        
        assert opportunity is None
    
    @pytest.mark.asyncio
    async def test_check_execution_plan_rejects_high_price_impact(self, finder, mock_jupiter, usdc_mint, sol_mint):
        """Test _check_execution_plan rejects plan with high price impact."""
        leg1 = ExecutionLeg(from_mint=usdc_mint, to_mint=sol_mint, max_hops=1)
        leg2 = ExecutionLeg(from_mint=sol_mint, to_mint=usdc_mint, max_hops=1)
        execution_plan = ExecutionPlan(
            cycle_mints=[usdc_mint, sol_mint, usdc_mint],
            legs=[leg1, leg2],
            atomic=True,
            use_shared_accounts=False
        )
        
        # Mock quotes with high price impact (>5%)
        quote1 = JupiterQuote(
            input_mint=usdc_mint,
            output_mint=sol_mint,
            in_amount=1_000_000,
            out_amount=10_000_000,
            price_impact_pct=6.0,  # > 5% - should be rejected
            route_plan=[{
                'swapInfo': {
                    'inputMint': usdc_mint,
                    'outputMint': sol_mint,
                    'ammKey': '675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8'
                }
            }]
        )
        quote2 = JupiterQuote(
            input_mint=sol_mint,
            output_mint=usdc_mint,
            in_amount=10_000_000,
            out_amount=1_100_000,
            price_impact_pct=0.2,
            route_plan=[{
                'swapInfo': {
                    'inputMint': sol_mint,
                    'outputMint': usdc_mint,
                    'ammKey': '9W959DqEETiGZocYWCQPaJ6sBmUzgfxXfqGeTEdp3aQP'
                }
            }]
        )
        
        mock_jupiter.get_quote.side_effect = [quote1, quote2]
        
        opportunity = await finder._check_execution_plan(execution_plan, 1_000_000)
        
        assert opportunity is None
    
    @pytest.mark.asyncio
    async def test_check_execution_plan_no_quote(self, finder, mock_jupiter, usdc_mint, sol_mint):
        """Test _check_execution_plan returns None when quote fails."""
        leg1 = ExecutionLeg(from_mint=usdc_mint, to_mint=sol_mint, max_hops=1)
        leg2 = ExecutionLeg(from_mint=sol_mint, to_mint=usdc_mint, max_hops=1)
        execution_plan = ExecutionPlan(
            cycle_mints=[usdc_mint, sol_mint, usdc_mint],
            legs=[leg1, leg2],
            atomic=True,
            use_shared_accounts=False
        )
        mock_jupiter.get_quote.return_value = None
        
        opportunity = await finder._check_execution_plan(execution_plan, 1_000_000)
        assert opportunity is None
    
    @pytest.mark.asyncio
    async def test_check_execution_plan_zero_output(self, finder, mock_jupiter, usdc_mint, sol_mint):
        """Test _check_execution_plan returns None when quote has zero output."""
        leg1 = ExecutionLeg(from_mint=usdc_mint, to_mint=sol_mint, max_hops=1)
        leg2 = ExecutionLeg(from_mint=sol_mint, to_mint=usdc_mint, max_hops=1)
        execution_plan = ExecutionPlan(
            cycle_mints=[usdc_mint, sol_mint, usdc_mint],
            legs=[leg1, leg2],
            atomic=True,
            use_shared_accounts=False
        )
        
        quote = JupiterQuote(
            input_mint=usdc_mint,
            output_mint=sol_mint,
            in_amount=1_000_000,
            out_amount=0,  # Zero output
            price_impact_pct=0.1,
            route_plan=[{
                'swapInfo': {
                    'inputMint': usdc_mint,
                    'outputMint': sol_mint,
                    'ammKey': '675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8'
                }
            }]
        )
        mock_jupiter.get_quote.return_value = quote
        
        opportunity = await finder._check_execution_plan(execution_plan, 1_000_000)
        assert opportunity is None
    
    @pytest.mark.asyncio
    async def test_find_opportunities_no_execution_plans(self, finder):
        """Test find_opportunities returns empty list when no execution plans configured."""
        finder.execution_plans = []
        opportunities = await finder.find_opportunities("token1", 1_000_000)
        assert opportunities == []
    
    @pytest.mark.asyncio
    async def test_find_opportunities_filters_by_profit(self, finder, mock_jupiter, usdc_mint, sol_mint):
        """Test find_opportunities filters opportunities by profit."""
        leg1 = ExecutionLeg(from_mint=usdc_mint, to_mint=sol_mint, max_hops=1)
        leg2 = ExecutionLeg(from_mint=sol_mint, to_mint=usdc_mint, max_hops=1)
        execution_plan = ExecutionPlan(
            cycle_mints=[usdc_mint, sol_mint, usdc_mint],
            legs=[leg1, leg2],
            atomic=True,
            use_shared_accounts=False
        )
        finder.execution_plans = [execution_plan]
        
        # Mock profitable quotes
        quote1 = JupiterQuote(
            input_mint=usdc_mint,
            output_mint=sol_mint,
            in_amount=1_000_000,
            out_amount=10_000_000,
            price_impact_pct=0.1,
            route_plan=[{
                'swapInfo': {
                    'inputMint': usdc_mint,
                    'outputMint': sol_mint,
                    'ammKey': '675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8'
                }
            }]
        )
        quote2 = JupiterQuote(
            input_mint=sol_mint,
            output_mint=usdc_mint,
            in_amount=10_000_000,
            out_amount=1_200_000,  # 1.2 USDC (20% profit = 2000 bps, $0.2 profit)
            price_impact_pct=0.2,
            route_plan=[{
                'swapInfo': {
                    'inputMint': sol_mint,
                    'outputMint': usdc_mint,
                    'ammKey': '9W959DqEETiGZocYWCQPaJ6sBmUzgfxXfqGeTEdp3aQP'
                }
            }]
        )
        
        mock_jupiter.get_quote.side_effect = [quote1, quote2]
        
        opportunities = await finder.find_opportunities(usdc_mint, 1_000_000)
        
        assert len(opportunities) == 1
        assert opportunities[0].profit_bps > 0
        assert opportunities[0].profit_usd > 0.1
    
    @pytest.mark.asyncio
    async def test_find_opportunities_filters_low_profit(self, finder, mock_jupiter, usdc_mint, sol_mint):
        """Test find_opportunities filters out opportunities with low profit."""
        leg1 = ExecutionLeg(from_mint=usdc_mint, to_mint=sol_mint, max_hops=1)
        leg2 = ExecutionLeg(from_mint=sol_mint, to_mint=usdc_mint, max_hops=1)
        execution_plan = ExecutionPlan(
            cycle_mints=[usdc_mint, sol_mint, usdc_mint],
            legs=[leg1, leg2],
            atomic=True,
            use_shared_accounts=False
        )
        finder.execution_plans = [execution_plan]
        
        # Mock quotes with very small profit (below min_profit_usdc)
        quote1 = JupiterQuote(
            input_mint=usdc_mint,
            output_mint=sol_mint,
            in_amount=1_000_000,
            out_amount=10_000_000,
            price_impact_pct=0.1,
            route_plan=[{
                'swapInfo': {
                    'inputMint': usdc_mint,
                    'outputMint': sol_mint,
                    'ammKey': '675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8'
                }
            }]
        )
        quote2 = JupiterQuote(
            input_mint=sol_mint,
            output_mint=usdc_mint,
            in_amount=10_000_000,
            out_amount=1_000_050,  # Tiny profit (0.00005 USDC)
            price_impact_pct=0.2,
            route_plan=[{
                'swapInfo': {
                    'inputMint': sol_mint,
                    'outputMint': usdc_mint,
                    'ammKey': '9W959DqEETiGZocYWCQPaJ6sBmUzgfxXfqGeTEdp3aQP'
                }
            }]
        )
        
        mock_jupiter.get_quote.side_effect = [quote1, quote2]
        
        opportunities = await finder.find_opportunities(usdc_mint, 1_000_000)
        
        # Should be filtered out due to low profit
        assert len(opportunities) == 0
    
    @pytest.mark.asyncio
    async def test_find_opportunities_with_callback(self, finder, mock_jupiter, usdc_mint, sol_mint):
        """Test find_opportunities calls callback when opportunity found."""
        leg1 = ExecutionLeg(from_mint=usdc_mint, to_mint=sol_mint, max_hops=1)
        leg2 = ExecutionLeg(from_mint=sol_mint, to_mint=usdc_mint, max_hops=1)
        execution_plan = ExecutionPlan(
            cycle_mints=[usdc_mint, sol_mint, usdc_mint],
            legs=[leg1, leg2],
            atomic=True,
            use_shared_accounts=False
        )
        finder.execution_plans = [execution_plan]
        
        # Mock profitable quotes
        quote1 = JupiterQuote(
            input_mint=usdc_mint,
            output_mint=sol_mint,
            in_amount=1_000_000,
            out_amount=10_000_000,
            price_impact_pct=0.1,
            route_plan=[{
                'swapInfo': {
                    'inputMint': usdc_mint,
                    'outputMint': sol_mint,
                    'ammKey': '675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8'
                }
            }]
        )
        quote2 = JupiterQuote(
            input_mint=sol_mint,
            output_mint=usdc_mint,
            in_amount=10_000_000,
            out_amount=1_200_000,  # Profitable
            price_impact_pct=0.2,
            route_plan=[{
                'swapInfo': {
                    'inputMint': sol_mint,
                    'outputMint': usdc_mint,
                    'ammKey': '9W959DqEETiGZocYWCQPaJ6sBmUzgfxXfqGeTEdp3aQP'
                }
            }]
        )
        
        mock_jupiter.get_quote.side_effect = [quote1, quote2]
        
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
