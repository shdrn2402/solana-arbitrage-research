"""
Tests for config validation - ensuring 4-element cycles are skipped.
"""
import pytest
from src.arbitrage_finder import ExecutionPlan, ExecutionLeg
from src.main import main as main_func


class TestConfigValidation:
    """Tests for config.json validation."""
    
    def test_execution_plan_rejects_4_element_cycle(self, sol_mint, usdc_mint, jup_mint):
        """Test that ExecutionPlan rejects 4-element cycles (only 3-element [A,B,A] allowed)."""
        leg1 = ExecutionLeg(from_mint=usdc_mint, to_mint=sol_mint, max_hops=1)
        leg2 = ExecutionLeg(from_mint=sol_mint, to_mint=jup_mint, max_hops=1)
        
        # Try to create plan with 4-element cycle (should fail)
        with pytest.raises(ValueError, match="must have 3 tokens"):
            ExecutionPlan(
                cycle_mints=[usdc_mint, sol_mint, jup_mint, usdc_mint],  # 4 elements - invalid
                legs=[leg1, leg2],
                atomic=True,
                use_shared_accounts=False
            )
    
    def test_execution_plan_accepts_3_element_cycle(self, sol_mint, usdc_mint):
        """Test that ExecutionPlan accepts valid 3-element cycle [A,B,A]."""
        leg1 = ExecutionLeg(from_mint=usdc_mint, to_mint=sol_mint, max_hops=1)
        leg2 = ExecutionLeg(from_mint=sol_mint, to_mint=usdc_mint, max_hops=1)
        
        # Should succeed with 3-element cycle
        plan = ExecutionPlan(
            cycle_mints=[usdc_mint, sol_mint, usdc_mint],  # 3 elements - valid
            legs=[leg1, leg2],
            atomic=True,
            use_shared_accounts=False
        )
        
        assert len(plan.cycle_mints) == 3
        assert plan.cycle_mints[0] == plan.cycle_mints[2]  # Start == end
    
    def test_execution_plan_requires_start_equals_end(self, sol_mint, usdc_mint, jup_mint):
        """Test that ExecutionPlan requires cycle to start and end with same token."""
        leg1 = ExecutionLeg(from_mint=usdc_mint, to_mint=sol_mint, max_hops=1)
        leg2 = ExecutionLeg(from_mint=sol_mint, to_mint=jup_mint, max_hops=1)
        
        # Try to create plan where start != end (should fail)
        with pytest.raises(ValueError, match="must start and end with the same token"):
            ExecutionPlan(
                cycle_mints=[usdc_mint, sol_mint, jup_mint],  # Start != end - invalid
                legs=[leg1, leg2],
                atomic=True,
                use_shared_accounts=False
            )
    
    def test_execution_plan_supports_usdc_sol_usdc(self, sol_mint, usdc_mint):
        """Test that USDC->SOL->USDC execution plan is supported."""
        leg1 = ExecutionLeg(from_mint=usdc_mint, to_mint=sol_mint, max_hops=1)
        leg2 = ExecutionLeg(from_mint=sol_mint, to_mint=usdc_mint, max_hops=1)
        
        plan = ExecutionPlan(
            cycle_mints=[usdc_mint, sol_mint, usdc_mint],
            legs=[leg1, leg2],
            atomic=True,
            use_shared_accounts=False
        )
        
        assert plan.cycle_mints[0] == usdc_mint
        assert plan.cycle_mints[1] == sol_mint
        assert plan.cycle_mints[2] == usdc_mint
    
    def test_execution_plan_supports_sol_usdc_sol(self, sol_mint, usdc_mint):
        """Test that SOL->USDC->SOL execution plan is supported."""
        leg1 = ExecutionLeg(from_mint=sol_mint, to_mint=usdc_mint, max_hops=1)
        leg2 = ExecutionLeg(from_mint=usdc_mint, to_mint=sol_mint, max_hops=1)
        
        plan = ExecutionPlan(
            cycle_mints=[sol_mint, usdc_mint, sol_mint],
            legs=[leg1, leg2],
            atomic=True,
            use_shared_accounts=False
        )
        
        assert plan.cycle_mints[0] == sol_mint
        assert plan.cycle_mints[1] == usdc_mint
        assert plan.cycle_mints[2] == sol_mint
