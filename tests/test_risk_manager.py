"""
Tests for risk_manager.py
"""
import pytest
from src.risk_manager import RiskManager, RiskConfig, Position


class TestRiskConfig:
    """Tests for RiskConfig dataclass."""
    
    def test_risk_config_creation(self, risk_config):
        """Test RiskConfig can be created with valid values."""
        assert risk_config.max_position_size_percent == 10.0
        assert risk_config.max_position_size_absolute_usdc == 100.0
        assert risk_config.min_profit_usdc == 0.1
        assert risk_config.min_profit_bps == 50
        assert risk_config.max_slippage_bps == 50
        assert risk_config.max_active_positions == 1
        assert risk_config.sol_price_usdc == 100.0


class TestRiskManager:
    """Tests for RiskManager class."""
    
    def test_risk_manager_initialization(self, risk_config):
        """Test RiskManager can be initialized."""
        manager = RiskManager(risk_config)
        assert manager.config == risk_config
        assert manager.wallet_balances == {}
        assert manager.locked_balances == {}
        assert len(manager.active_positions) == 0
    
    def test_update_wallet_balances(self, risk_config, sol_mint, usdc_mint):
        """Test updating wallet balances."""
        manager = RiskManager(risk_config)
        balances = {
            sol_mint: 1_000_000_000,  # 1 SOL in lamports
            usdc_mint: 100_000_000  # 100 USDC in smallest units (6 decimals)
        }
        manager.update_wallet_balances(balances)
        assert manager.wallet_balances[sol_mint] == 1_000_000_000
        assert manager.wallet_balances[usdc_mint] == 100_000_000
    
    def test_get_available_balance_sol(self, risk_config, sol_mint):
        """Test getting available SOL balance."""
        manager = RiskManager(risk_config)
        manager.update_wallet_balances({sol_mint: 1_000_000_000})  # 1 SOL
        manager.lock_balance(sol_mint, "pos1", 100_000_000)  # 0.1 SOL
        assert manager.get_available_balance(sol_mint) == 900_000_000  # 0.9 SOL
    
    def test_get_available_balance_usdc(self, risk_config, usdc_mint):
        """Test getting available USDC balance."""
        manager = RiskManager(risk_config)
        manager.update_wallet_balances({usdc_mint: 100_000_000})  # 100 USDC
        manager.lock_balance(usdc_mint, "pos1", 10_000_000)  # 10 USDC
        assert manager.get_available_balance(usdc_mint) == 90_000_000  # 90 USDC
    
    def test_get_available_balance_no_locked(self, risk_config, sol_mint):
        """Test available balance when nothing is locked."""
        manager = RiskManager(risk_config)
        manager.update_wallet_balances({sol_mint: 1_000_000_000})
        assert manager.get_available_balance(sol_mint) == 1_000_000_000
    
    def test_get_available_balance_negative(self, risk_config, sol_mint):
        """Test available balance doesn't go negative."""
        manager = RiskManager(risk_config)
        manager.update_wallet_balances({sol_mint: 100_000_000})
        manager.lock_balance(sol_mint, "pos1", 200_000_000)  # Lock more than available
        assert manager.get_available_balance(sol_mint) == 0
    
    def test_get_available_balance_unknown_mint(self, risk_config):
        """Test available balance for unknown mint returns 0."""
        manager = RiskManager(risk_config)
        assert manager.get_available_balance("unknown_mint") == 0
    
    def test_can_open_position_sol_success(self, risk_config, sol_mint):
        """Test can_open_position returns True for valid SOL-base position."""
        manager = RiskManager(risk_config)
        manager.update_wallet_balances({sol_mint: 10_000_000_000})  # 10 SOL
        
        # Position: 1 SOL (10% of balance), profit 100 bps, $1 profit
        can_open, reason = manager.can_open_position(
            base_mint=sol_mint,
            amount_in=1_000_000_000,
            expected_profit_bps=100,
            slippage_bps=50,
            expected_profit_usdc=1.0
        )
        assert can_open is True
        assert reason is None
    
    def test_can_open_position_usdc_success(self, risk_config, usdc_mint):
        """Test can_open_position returns True for valid USDC-base position."""
        manager = RiskManager(risk_config)
        manager.update_wallet_balances({usdc_mint: 1_000_000_000})  # 1000 USDC (1e6 * 1000)
        
        # Position: 100 USDC (10% of balance), profit 100 bps, $1 profit
        can_open, reason = manager.can_open_position(
            base_mint=usdc_mint,
            amount_in=100_000_000,  # 100 USDC
            expected_profit_bps=100,
            slippage_bps=50,
            expected_profit_usdc=1.0
        )
        assert can_open is True
        assert reason is None
    
    def test_can_open_position_max_active_positions(self, risk_config, sol_mint):
        """Test can_open_position fails when max active positions reached."""
        manager = RiskManager(risk_config)
        manager.update_wallet_balances({sol_mint: 10_000_000_000})
        
        # Add one active position
        manager.add_position("pos1", sol_mint, "mint2", 1_000_000_000, 1_100_000_000, base_mint=sol_mint)
        
        # Try to open another position
        can_open, reason = manager.can_open_position(
            base_mint=sol_mint,
            amount_in=1_000_000_000,
            expected_profit_bps=100,
            slippage_bps=50,
            expected_profit_usdc=1.0
        )
        assert can_open is False
        assert "Max active positions" in reason
    
    def test_can_open_position_insufficient_sol_balance(self, risk_config, sol_mint):
        """Test can_open_position fails when SOL balance is insufficient."""
        manager = RiskManager(risk_config)
        manager.update_wallet_balances({sol_mint: 500_000_000})  # 0.5 SOL
        
        can_open, reason = manager.can_open_position(
            base_mint=sol_mint,
            amount_in=1_000_000_000,  # 1 SOL (more than available)
            expected_profit_bps=100,
            slippage_bps=50,
            expected_profit_usdc=1.0
        )
        assert can_open is False
        assert "Insufficient SOL balance" in reason
    
    def test_can_open_position_insufficient_usdc_balance(self, risk_config, usdc_mint, sol_mint):
        """Test can_open_position fails when USDC balance is insufficient, even with SOL."""
        manager = RiskManager(risk_config)
        # Have plenty of SOL, but not enough USDC
        manager.update_wallet_balances({
            sol_mint: 10_000_000_000,  # 10 SOL
            usdc_mint: 50_000_000  # 50 USDC
        })
        
        can_open, reason = manager.can_open_position(
            base_mint=usdc_mint,
            amount_in=100_000_000,  # 100 USDC (more than available)
            expected_profit_bps=100,
            slippage_bps=50,
            expected_profit_usdc=1.0
        )
        assert can_open is False
        assert "Insufficient USDC balance" in reason
        # Verify SOL balance doesn't help
        assert manager.get_available_balance(sol_mint) == 10_000_000_000
    
    def test_can_open_position_exceeds_percent_limit_sol(self, risk_config, sol_mint):
        """Test can_open_position fails when position exceeds percentage limit (SOL)."""
        manager = RiskManager(risk_config)
        manager.update_wallet_balances({sol_mint: 1_000_000_000})  # 1 SOL
        
        # Try to open position > 10% of balance
        can_open, reason = manager.can_open_position(
            base_mint=sol_mint,
            amount_in=200_000_000,  # 0.2 SOL (20% of balance)
            expected_profit_bps=100,
            slippage_bps=50,
            expected_profit_usdc=1.0
        )
        assert can_open is False
        assert "Position size exceeds limit" in reason
    
    def test_can_open_position_exceeds_percent_limit_usdc(self, risk_config, usdc_mint):
        """Test can_open_position fails when position exceeds percentage limit (USDC)."""
        # Use config with high absolute limit to test percentage limit
        config = RiskConfig(
            max_position_size_percent=10.0,
            max_position_size_absolute_usdc=5000.0,  # Very high absolute limit to avoid triggering
            min_profit_usdc=0.1,
            min_profit_bps=50,
            max_slippage_bps=50,
            max_active_positions=1,
            sol_price_usdc=100.0
        )
        manager = RiskManager(config)
        manager.update_wallet_balances({usdc_mint: 10_000_000_000})  # 10000 USDC
        
        # Try to open position > 10% of balance (20% = 2000 USDC)
        # This should fail on percentage limit, not absolute (2000 < 5000)
        can_open, reason = manager.can_open_position(
            base_mint=usdc_mint,
            amount_in=2_000_000_000,  # 2000 USDC (20% of balance, < $5000 absolute limit)
            expected_profit_bps=100,
            slippage_bps=50,
            expected_profit_usdc=1.0
        )
        assert can_open is False
        assert "Position size exceeds limit" in reason
        assert "%" in reason  # Should mention percentage
    
    def test_can_open_position_exceeds_absolute_limit_sol(self, risk_config, sol_mint):
        """Test can_open_position fails when position exceeds absolute limit (SOL)."""
        manager = RiskManager(risk_config)
        manager.update_wallet_balances({sol_mint: 10_000_000_000})  # 10 SOL
        
        # Try to open position > $100 USDC (absolute limit)
        # 2 SOL * $100 = $200 > $100 limit
        can_open, reason = manager.can_open_position(
            base_mint=sol_mint,
            amount_in=2_000_000_000,  # 2 SOL
            expected_profit_bps=100,
            slippage_bps=50,
            expected_profit_usdc=1.0
        )
        assert can_open is False
        assert "Position size exceeds absolute limit" in reason
    
    def test_can_open_position_exceeds_absolute_limit_usdc(self, risk_config, usdc_mint):
        """Test can_open_position fails when position exceeds absolute limit (USDC)."""
        manager = RiskManager(risk_config)
        manager.update_wallet_balances({usdc_mint: 10_000_000_000})  # 10000 USDC
        
        # Try to open position > $100 USDC (absolute limit)
        can_open, reason = manager.can_open_position(
            base_mint=usdc_mint,
            amount_in=200_000_000,  # 200 USDC > $100 limit
            expected_profit_bps=100,
            slippage_bps=50,
            expected_profit_usdc=1.0
        )
        assert can_open is False
        assert "Position size exceeds absolute limit" in reason
    
    def test_can_open_position_profit_too_low_usdc(self, risk_config, sol_mint):
        """Test can_open_position fails when profit is too low (USDC check)."""
        manager = RiskManager(risk_config)
        manager.update_wallet_balances({sol_mint: 10_000_000_000})
        
        can_open, reason = manager.can_open_position(
            base_mint=sol_mint,
            amount_in=1_000_000_000,
            expected_profit_bps=100,
            slippage_bps=50,
            expected_profit_usdc=0.05  # Less than min_profit_usdc (0.1)
        )
        assert can_open is False
        assert "Profit too low" in reason
        assert "PRIMARY CHECK" in reason
    
    def test_can_open_position_profit_too_low_bps(self, risk_config, sol_mint):
        """Test can_open_position fails when profit is too low (BPS check)."""
        manager = RiskManager(risk_config)
        manager.update_wallet_balances({sol_mint: 10_000_000_000})
        
        can_open, reason = manager.can_open_position(
            base_mint=sol_mint,
            amount_in=1_000_000_000,
            expected_profit_bps=30,  # Less than min_profit_bps (50)
            slippage_bps=50,
            expected_profit_usdc=1.0
        )
        assert can_open is False
        assert "Profit too low" in reason
        assert "SECONDARY FILTER" in reason
    
    def test_can_open_position_bps_filter_disabled(self, risk_config, sol_mint):
        """Test can_open_position allows position when BPS filter is disabled (0)."""
        # Create config with min_profit_bps=0 (disabled)
        config = RiskConfig(
            max_position_size_percent=10.0,
            max_position_size_absolute_usdc=100.0,
            min_profit_usdc=0.1,
            min_profit_bps=0,  # Disabled
            max_slippage_bps=50,
            max_active_positions=1,
            sol_price_usdc=100.0
        )
        manager = RiskManager(config)
        manager.update_wallet_balances({sol_mint: 10_000_000_000})
        
        # Should pass even with low BPS if USDC check passes
        can_open, reason = manager.can_open_position(
            base_mint=sol_mint,
            amount_in=1_000_000_000,
            expected_profit_bps=10,  # Low BPS, but filter is disabled
            slippage_bps=50,
            expected_profit_usdc=1.0  # Passes USDC check
        )
        assert can_open is True
    
    def test_can_open_position_slippage_too_high(self, risk_config, sol_mint):
        """Test can_open_position fails when slippage is too high."""
        manager = RiskManager(risk_config)
        manager.update_wallet_balances({sol_mint: 10_000_000_000})
        
        can_open, reason = manager.can_open_position(
            base_mint=sol_mint,
            amount_in=1_000_000_000,
            expected_profit_bps=100,
            slippage_bps=100,  # Exceeds max_slippage_bps (50)
            expected_profit_usdc=1.0
        )
        assert can_open is False
        assert "Slippage too high" in reason
    
    def test_can_open_position_unsupported_base_mint(self, risk_config):
        """Test can_open_position fails for unsupported base mint (not SOL/USDC)."""
        manager = RiskManager(risk_config)
        manager.update_wallet_balances({"unknown_mint": 1_000_000_000})
        
        can_open, reason = manager.can_open_position(
            base_mint="unknown_mint",
            amount_in=1_000_000_000,
            expected_profit_bps=100,
            slippage_bps=50,
            expected_profit_usdc=1.0
        )
        assert can_open is False
        assert "Unsupported base mint" in reason
        assert "without price oracle" in reason
    
    def test_lock_balance_sol(self, risk_config, sol_mint):
        """Test locking SOL balance for a position."""
        manager = RiskManager(risk_config)
        manager.update_wallet_balances({sol_mint: 1_000_000_000})
        manager.lock_balance(sol_mint, "pos1", 100_000_000)
        assert manager.locked_balances[sol_mint] == 100_000_000
    
    def test_lock_balance_usdc(self, risk_config, usdc_mint):
        """Test locking USDC balance for a position."""
        manager = RiskManager(risk_config)
        manager.update_wallet_balances({usdc_mint: 1_000_000_000})
        manager.lock_balance(usdc_mint, "pos1", 100_000_000)
        assert manager.locked_balances[usdc_mint] == 100_000_000
    
    def test_unlock_balance_sol(self, risk_config, sol_mint):
        """Test unlocking SOL balance."""
        manager = RiskManager(risk_config)
        manager.update_wallet_balances({sol_mint: 1_000_000_000})
        manager.lock_balance(sol_mint, "pos1", 100_000_000)
        manager.unlock_balance(sol_mint, "pos1", 100_000_000)
        assert manager.locked_balances.get(sol_mint, 0) == 0
    
    def test_unlock_balance_usdc(self, risk_config, usdc_mint):
        """Test unlocking USDC balance."""
        manager = RiskManager(risk_config)
        manager.update_wallet_balances({usdc_mint: 1_000_000_000})
        manager.lock_balance(usdc_mint, "pos1", 100_000_000)
        manager.unlock_balance(usdc_mint, "pos1", 100_000_000)
        assert manager.locked_balances.get(usdc_mint, 0) == 0
    
    def test_unlock_balance_negative(self, risk_config, sol_mint):
        """Test unlocking balance doesn't go negative."""
        manager = RiskManager(risk_config)
        manager.locked_balances[sol_mint] = 50_000_000
        manager.unlock_balance(sol_mint, "pos1", 100_000_000)  # Unlock more than locked
        assert manager.locked_balances[sol_mint] == 0
    
    def test_add_position_sol(self, risk_config, sol_mint):
        """Test adding a new SOL-base position."""
        manager = RiskManager(risk_config)
        manager.update_wallet_balances({sol_mint: 1_000_000_000})
        
        manager.add_position(
            "pos1",
            sol_mint,
            "output_mint",
            100_000_000,
            110_000_000,
            base_mint=sol_mint
        )
        
        assert "pos1" in manager.active_positions
        position = manager.active_positions["pos1"]
        assert position.input_mint == sol_mint
        assert position.output_mint == "output_mint"
        assert position.amount_in == 100_000_000
        assert position.expected_amount_out == 110_000_000
        assert position.status == "pending"
        assert position.base_mint == sol_mint
        assert manager.locked_balances[sol_mint] == 100_000_000
    
    def test_add_position_usdc(self, risk_config, usdc_mint):
        """Test adding a new USDC-base position."""
        manager = RiskManager(risk_config)
        manager.update_wallet_balances({usdc_mint: 1_000_000_000})
        
        manager.add_position(
            "pos1",
            usdc_mint,
            "output_mint",
            100_000_000,
            110_000_000,
            base_mint=usdc_mint
        )
        
        assert "pos1" in manager.active_positions
        position = manager.active_positions["pos1"]
        assert position.base_mint == usdc_mint
        assert manager.locked_balances[usdc_mint] == 100_000_000
    
    def test_add_position_base_mint_fallback(self, risk_config, sol_mint):
        """Test add_position uses input_mint as base_mint fallback."""
        manager = RiskManager(risk_config)
        manager.update_wallet_balances({sol_mint: 1_000_000_000})
        
        manager.add_position(
            "pos1",
            sol_mint,  # input_mint
            "output_mint",
            100_000_000,
            110_000_000
            # base_mint not provided, should use input_mint
        )
        
        position = manager.active_positions["pos1"]
        assert position.base_mint == sol_mint  # Should use input_mint as fallback
    
    def test_update_position_status(self, risk_config, sol_mint):
        """Test updating position status."""
        manager = RiskManager(risk_config)
        manager.add_position("pos1", sol_mint, "mint2", 100_000_000, 110_000_000, base_mint=sol_mint)
        manager.update_position_status("pos1", "executing")
        assert manager.active_positions["pos1"].status == "executing"
    
    def test_remove_position_sol(self, risk_config, sol_mint):
        """Test removing a SOL-base position."""
        manager = RiskManager(risk_config)
        manager.update_wallet_balances({sol_mint: 1_000_000_000})
        manager.add_position("pos1", sol_mint, "mint2", 100_000_000, 110_000_000, base_mint=sol_mint)
        
        manager.remove_position("pos1")
        
        assert "pos1" not in manager.active_positions
        assert manager.locked_balances.get(sol_mint, 0) == 0
    
    def test_remove_position_usdc(self, risk_config, usdc_mint):
        """Test removing a USDC-base position."""
        manager = RiskManager(risk_config)
        manager.update_wallet_balances({usdc_mint: 1_000_000_000})
        manager.add_position("pos1", usdc_mint, "mint2", 100_000_000, 110_000_000, base_mint=usdc_mint)
        
        manager.remove_position("pos1")
        
        assert "pos1" not in manager.active_positions
        assert manager.locked_balances.get(usdc_mint, 0) == 0
    
    def test_get_position(self, risk_config, sol_mint):
        """Test getting a position by ID."""
        manager = RiskManager(risk_config)
        manager.add_position("pos1", sol_mint, "mint2", 100_000_000, 110_000_000, base_mint=sol_mint)
        
        position = manager.get_position("pos1")
        assert position is not None
        assert position.amount_in == 100_000_000
        
        # Non-existent position
        assert manager.get_position("pos2") is None
    
    def test_calculate_profit_bps(self, risk_config):
        """Test calculating profit in basis points."""
        manager = RiskManager(risk_config)
        
        # 10% profit = 1000 bps
        profit_bps = manager.calculate_profit_bps(1_000_000_000, 1_100_000_000)
        assert profit_bps == 1000
        
        # 1% profit = 100 bps
        profit_bps = manager.calculate_profit_bps(1_000_000_000, 1_010_000_000)
        assert profit_bps == 100
        
        # Zero input
        profit_bps = manager.calculate_profit_bps(0, 1_000_000_000)
        assert profit_bps == 0
    
    def test_validate_simulation_result_success(self, risk_config):
        """Test validating simulation result with acceptable deviation."""
        manager = RiskManager(risk_config)
        
        # Expected: 1.1 SOL, Simulated: 1.105 SOL (0.5% deviation = 50 bps)
        is_valid, reason = manager.validate_simulation_result(
            expected_amount_out=1_100_000_000,
            simulated_amount_out=1_105_000_000,
            max_deviation_bps=100
        )
        assert is_valid is True
        assert reason is None
    
    def test_validate_simulation_result_zero_output(self, risk_config):
        """Test validating simulation result with zero output."""
        manager = RiskManager(risk_config)
        
        is_valid, reason = manager.validate_simulation_result(
            expected_amount_out=1_100_000_000,
            simulated_amount_out=0,
            max_deviation_bps=100
        )
        assert is_valid is False
        assert "zero output" in reason
    
    def test_validate_simulation_result_high_deviation(self, risk_config):
        """Test validating simulation result with high deviation."""
        manager = RiskManager(risk_config)
        
        # Expected: 1.1 SOL, Simulated: 1.2 SOL (9.09% deviation = 909 bps)
        is_valid, reason = manager.validate_simulation_result(
            expected_amount_out=1_100_000_000,
            simulated_amount_out=1_200_000_000,
            max_deviation_bps=100
        )
        assert is_valid is False
        assert "deviation too high" in reason
