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
        assert manager.wallet_balance == 0
        assert manager.locked_balance == 0
        assert len(manager.active_positions) == 0
    
    def test_update_wallet_balance(self, risk_config):
        """Test updating wallet balance."""
        manager = RiskManager(risk_config)
        manager.update_wallet_balance(1_000_000_000)  # 1 SOL in lamports
        assert manager.wallet_balance == 1_000_000_000
    
    def test_get_available_balance(self, risk_config):
        """Test getting available balance."""
        manager = RiskManager(risk_config)
        manager.update_wallet_balance(1_000_000_000)  # 1 SOL
        manager.lock_balance("pos1", 100_000_000)  # 0.1 SOL
        assert manager.get_available_balance() == 900_000_000  # 0.9 SOL
    
    def test_get_available_balance_no_locked(self, risk_config):
        """Test available balance when nothing is locked."""
        manager = RiskManager(risk_config)
        manager.update_wallet_balance(1_000_000_000)
        assert manager.get_available_balance() == 1_000_000_000
    
    def test_get_available_balance_negative(self, risk_config):
        """Test available balance doesn't go negative."""
        manager = RiskManager(risk_config)
        manager.update_wallet_balance(100_000_000)
        manager.lock_balance("pos1", 200_000_000)  # Lock more than available
        assert manager.get_available_balance() == 0
    
    def test_can_open_position_success(self, risk_config):
        """Test can_open_position returns True for valid position."""
        manager = RiskManager(risk_config)
        manager.update_wallet_balance(10_000_000_000)  # 10 SOL
        
        # Position: 1 SOL (10% of balance), profit 100 bps, $1 profit
        can_open, reason = manager.can_open_position(
            amount_in=1_000_000_000,
            expected_profit_bps=100,
            slippage_bps=50,
            expected_profit_usdc=1.0
        )
        assert can_open is True
        assert reason is None
    
    def test_can_open_position_max_active_positions(self, risk_config):
        """Test can_open_position fails when max active positions reached."""
        manager = RiskManager(risk_config)
        manager.update_wallet_balance(10_000_000_000)
        
        # Add one active position
        manager.add_position("pos1", "mint1", "mint2", 1_000_000_000, 1_100_000_000)
        
        # Try to open another position
        can_open, reason = manager.can_open_position(
            amount_in=1_000_000_000,
            expected_profit_bps=100,
            slippage_bps=50,
            expected_profit_usdc=1.0
        )
        assert can_open is False
        assert "Max active positions" in reason
    
    def test_can_open_position_insufficient_balance(self, risk_config):
        """Test can_open_position fails when balance is insufficient."""
        manager = RiskManager(risk_config)
        manager.update_wallet_balance(500_000_000)  # 0.5 SOL
        
        can_open, reason = manager.can_open_position(
            amount_in=1_000_000_000,  # 1 SOL (more than available)
            expected_profit_bps=100,
            slippage_bps=50,
            expected_profit_usdc=1.0
        )
        assert can_open is False
        assert "Insufficient balance" in reason
    
    def test_can_open_position_exceeds_percent_limit(self, risk_config):
        """Test can_open_position fails when position exceeds percentage limit."""
        manager = RiskManager(risk_config)
        manager.update_wallet_balance(1_000_000_000)  # 1 SOL
        
        # Try to open position > 10% of balance
        can_open, reason = manager.can_open_position(
            amount_in=200_000_000,  # 0.2 SOL (20% of balance)
            expected_profit_bps=100,
            slippage_bps=50,
            expected_profit_usdc=1.0
        )
        assert can_open is False
        assert "Position size exceeds limit" in reason
    
    def test_can_open_position_exceeds_absolute_limit(self, risk_config):
        """Test can_open_position fails when position exceeds absolute limit."""
        manager = RiskManager(risk_config)
        manager.update_wallet_balance(10_000_000_000)  # 10 SOL
        
        # Try to open position > $100 USDC (absolute limit)
        # 2 SOL * $100 = $200 > $100 limit
        can_open, reason = manager.can_open_position(
            amount_in=2_000_000_000,  # 2 SOL
            expected_profit_bps=100,
            slippage_bps=50,
            expected_profit_usdc=1.0
        )
        assert can_open is False
        assert "Position size exceeds absolute limit" in reason
    
    def test_can_open_position_profit_too_low_usdc(self, risk_config):
        """Test can_open_position fails when profit is too low (USDC check)."""
        manager = RiskManager(risk_config)
        manager.update_wallet_balance(10_000_000_000)
        
        can_open, reason = manager.can_open_position(
            amount_in=1_000_000_000,
            expected_profit_bps=100,
            slippage_bps=50,
            expected_profit_usdc=0.05  # Less than min_profit_usdc (0.1)
        )
        assert can_open is False
        assert "Profit too low" in reason
        assert "PRIMARY CHECK" in reason
    
    def test_can_open_position_profit_too_low_bps(self, risk_config):
        """Test can_open_position fails when profit is too low (BPS check)."""
        manager = RiskManager(risk_config)
        manager.update_wallet_balance(10_000_000_000)
        
        can_open, reason = manager.can_open_position(
            amount_in=1_000_000_000,
            expected_profit_bps=30,  # Less than min_profit_bps (50)
            slippage_bps=50,
            expected_profit_usdc=1.0
        )
        assert can_open is False
        assert "Profit too low" in reason
        assert "SECONDARY FILTER" in reason
    
    def test_can_open_position_bps_filter_disabled(self, risk_config):
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
        manager.update_wallet_balance(10_000_000_000)
        
        # Should pass even with low BPS if USDC check passes
        can_open, reason = manager.can_open_position(
            amount_in=1_000_000_000,
            expected_profit_bps=10,  # Low BPS, but filter is disabled
            slippage_bps=50,
            expected_profit_usdc=1.0  # Passes USDC check
        )
        assert can_open is True
    
    def test_can_open_position_slippage_too_high(self, risk_config):
        """Test can_open_position fails when slippage is too high."""
        manager = RiskManager(risk_config)
        manager.update_wallet_balance(10_000_000_000)
        
        can_open, reason = manager.can_open_position(
            amount_in=1_000_000_000,
            expected_profit_bps=100,
            slippage_bps=100,  # Exceeds max_slippage_bps (50)
            expected_profit_usdc=1.0
        )
        assert can_open is False
        assert "Slippage too high" in reason
    
    def test_lock_balance(self, risk_config):
        """Test locking balance for a position."""
        manager = RiskManager(risk_config)
        manager.update_wallet_balance(1_000_000_000)
        manager.lock_balance("pos1", 100_000_000)
        assert manager.locked_balance == 100_000_000
    
    def test_unlock_balance(self, risk_config):
        """Test unlocking balance."""
        manager = RiskManager(risk_config)
        manager.update_wallet_balance(1_000_000_000)
        manager.lock_balance("pos1", 100_000_000)
        manager.unlock_balance("pos1", 100_000_000)
        assert manager.locked_balance == 0
    
    def test_unlock_balance_negative(self, risk_config):
        """Test unlocking balance doesn't go negative."""
        manager = RiskManager(risk_config)
        manager.locked_balance = 50_000_000
        manager.unlock_balance("pos1", 100_000_000)  # Unlock more than locked
        assert manager.locked_balance == 0
    
    def test_add_position(self, risk_config):
        """Test adding a new position."""
        manager = RiskManager(risk_config)
        manager.update_wallet_balance(1_000_000_000)
        
        manager.add_position(
            "pos1",
            "input_mint",
            "output_mint",
            100_000_000,
            110_000_000
        )
        
        assert "pos1" in manager.active_positions
        position = manager.active_positions["pos1"]
        assert position.input_mint == "input_mint"
        assert position.output_mint == "output_mint"
        assert position.amount_in == 100_000_000
        assert position.expected_amount_out == 110_000_000
        assert position.status == "pending"
        assert manager.locked_balance == 100_000_000
    
    def test_update_position_status(self, risk_config):
        """Test updating position status."""
        manager = RiskManager(risk_config)
        manager.add_position("pos1", "mint1", "mint2", 100_000_000, 110_000_000)
        manager.update_position_status("pos1", "executing")
        assert manager.active_positions["pos1"].status == "executing"
    
    def test_remove_position(self, risk_config):
        """Test removing a position."""
        manager = RiskManager(risk_config)
        manager.update_wallet_balance(1_000_000_000)
        manager.add_position("pos1", "mint1", "mint2", 100_000_000, 110_000_000)
        
        manager.remove_position("pos1")
        
        assert "pos1" not in manager.active_positions
        assert manager.locked_balance == 0
    
    def test_get_position(self, risk_config):
        """Test getting a position by ID."""
        manager = RiskManager(risk_config)
        manager.add_position("pos1", "mint1", "mint2", 100_000_000, 110_000_000)
        
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
