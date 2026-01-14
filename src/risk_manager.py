"""
Risk & Capital Management module.
Enforces all trading limits and risk controls.
"""
import logging
from typing import Optional, Dict, Any, Tuple
from dataclasses import dataclass
from .utils import get_terminal_colors

# Get terminal colors (empty if output is redirected)
colors = get_terminal_colors()
# Logger will be initialized in main() after .env is loaded
logger = logging.getLogger(__name__)


@dataclass
class RiskConfig:
    """Risk management configuration.
    
    All absolute limits are in USDC for consistency.
    SOL amounts are converted to USDC using sol_price_usdc.
    """
    max_position_size_percent: float
    max_position_size_absolute_usdc: float  # in USDC
    min_profit_usdc: float  # PRIMARY: minimum profit in USDC (absolute)
    min_profit_bps: int  # SECONDARY: optional filter in basis points
    max_slippage_bps: int  # maximum allowed slippage
    max_active_positions: int
    sol_price_usdc: float  # SOL price in USDC for conversion


@dataclass
class Position:
    """Represents an active trading position."""
    input_mint: str
    output_mint: str
    amount_in: int
    expected_amount_out: int
    status: str  # 'pending', 'executing', 'completed', 'failed'
    timestamp: float


class RiskManager:
    """Manages risk and capital for trading operations."""
    
    def __init__(self, config: RiskConfig):
        self.config = config
        self.active_positions: Dict[str, Position] = {}
        self.wallet_balance: int = 0  # in lamports
        self.locked_balance: int = 0  # balance locked in active positions
    
    def update_wallet_balance(self, balance_lamports: int):
        """Update wallet balance from network."""
        self.wallet_balance = balance_lamports
        logger.info(f"{colors['GREEN']}Wallet balance updated!{colors['RESET']}")
    
    def get_available_balance(self) -> int:
        """Get available balance (total - locked)."""
        return max(0, self.wallet_balance - self.locked_balance)
    
    def can_open_position(
        self,
        amount_in: int,
        expected_profit_bps: int,
        slippage_bps: int,
        expected_profit_usdc: float = 0.0
    ) -> Tuple[bool, Optional[str]]:
        """
        Check if a position can be opened.
        
        All limits are enforced in USDC for consistency.
        
        Returns:
            (can_open: bool, reason: Optional[str])
        """
        # Check active positions limit
        active_count = len([p for p in self.active_positions.values() 
                           if p.status in ['pending', 'executing']])
        if active_count >= self.config.max_active_positions:
            return False, f"Max active positions reached: {active_count}/{self.config.max_active_positions}"
        
        # Check available balance
        available = self.get_available_balance()
        if amount_in > available:
            return False, f"Insufficient balance: need {amount_in/1e9:.4f} SOL, have {available/1e9:.4f} SOL"
        
        # Check position size limits (percentage)
        position_percent = (amount_in / self.wallet_balance * 100) if self.wallet_balance > 0 else 0
        if position_percent > self.config.max_position_size_percent:
            return False, (f"Position size exceeds limit: {position_percent:.2f}% > "
                          f"{self.config.max_position_size_percent}%")
        
        # Check position size limits (absolute) - converted to USDC
        position_sol = amount_in / 1e9
        position_usdc = position_sol * self.config.sol_price_usdc
        if position_usdc > self.config.max_position_size_absolute_usdc:
            return False, (f"Position size exceeds absolute limit: ${position_usdc:.2f} USDC > "
                          f"${self.config.max_position_size_absolute_usdc} USDC")
        
        # PRIMARY: Check minimum profit in USDC (absolute)
        # This is the main safety check - profit must be meaningful in absolute terms
        if expected_profit_usdc < self.config.min_profit_usdc:
            return False, (f"Profit too low: ${expected_profit_usdc:.4f} USDC < "
                          f"${self.config.min_profit_usdc} USDC (PRIMARY CHECK)")
        
        # SECONDARY: Optional bps filter (can be disabled by setting to 0)
        if self.config.min_profit_bps > 0 and expected_profit_bps < self.config.min_profit_bps:
            return False, (f"Profit too low: {expected_profit_bps} bps < "
                          f"{self.config.min_profit_bps} bps (SECONDARY FILTER)")
        
        # Check slippage
        if slippage_bps > self.config.max_slippage_bps:
            return False, (f"Slippage too high: {slippage_bps} bps > "
                          f"{self.config.max_slippage_bps} bps")
        
        return True, None
    
    def lock_balance(self, position_id: str, amount: int):
        """Lock balance for a position."""
        self.locked_balance += amount
        logger.debug(f"Locked {amount/1e9:.4f} SOL for position {position_id}")
    
    def unlock_balance(self, position_id: str, amount: int):
        """Unlock balance for a position."""
        self.locked_balance = max(0, self.locked_balance - amount)
        logger.debug(f"Unlocked {amount/1e9:.4f} SOL for position {position_id}")
    
    def add_position(
        self,
        position_id: str,
        input_mint: str,
        output_mint: str,
        amount_in: int,
        expected_amount_out: int
    ):
        """Add a new active position."""
        import time
        position = Position(
            input_mint=input_mint,
            output_mint=output_mint,
            amount_in=amount_in,
            expected_amount_out=expected_amount_out,
            status='pending',
            timestamp=time.time()
        )
        self.active_positions[position_id] = position
        self.lock_balance(position_id, amount_in)
        logger.info(f"{colors['CYAN']}Position {position_id} added:{colors['RESET']} {colors['YELLOW']}{amount_in/1e9:.4f} SOL{colors['RESET']}")
    
    def update_position_status(self, position_id: str, status: str):
        """Update position status."""
        if position_id in self.active_positions:
            self.active_positions[position_id].status = status
            logger.debug(f"Position {position_id} status: {status}")
    
    def remove_position(self, position_id: str):
        """Remove a completed/failed position."""
        if position_id in self.active_positions:
            position = self.active_positions[position_id]
            self.unlock_balance(position_id, position.amount_in)
            del self.active_positions[position_id]
            logger.info(f"Position {position_id} removed")
    
    def get_position(self, position_id: str) -> Optional[Position]:
        """Get position by ID."""
        return self.active_positions.get(position_id)
    
    def calculate_profit_bps(self, amount_in: int, amount_out: int) -> int:
        """Calculate profit in basis points."""
        if amount_in == 0:
            return 0
        profit_pct = ((amount_out - amount_in) / amount_in) * 100
        return int(profit_pct * 100)  # convert to bps
    
    def validate_simulation_result(
        self,
        expected_amount_out: int,
        simulated_amount_out: int,
        max_deviation_bps: int = 100
    ) -> Tuple[bool, Optional[str]]:
        """
        Validate simulation result against expected.
        
        Returns:
            (is_valid: bool, reason: Optional[str])
        """
        if simulated_amount_out == 0:
            return False, "Simulation returned zero output"
        
        deviation_bps = abs(self.calculate_profit_bps(expected_amount_out, simulated_amount_out))
        if deviation_bps > max_deviation_bps:
            return False, (f"Simulation deviation too high: {deviation_bps} bps > "
                          f"{max_deviation_bps} bps")
        
        return True, None
