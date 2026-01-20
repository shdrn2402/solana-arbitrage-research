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
    base_mint: str  # Base token mint (first token in cycle, used for locking balance)


class RiskManager:
    """Manages risk and capital for trading operations."""
    
    # Token mint addresses
    SOL_MINT = "So11111111111111111111111111111111111111112"
    USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    
    def __init__(self, config: RiskConfig):
        self.config = config
        self.active_positions: Dict[str, Position] = {}
        self.wallet_balances: Dict[str, int] = {}  # mint -> amount in smallest units
        self.locked_balances: Dict[str, int] = {}  # mint -> locked amount in smallest units
    
    def update_wallet_balances(self, balances_by_mint: Dict[str, int]):
        """Update wallet balances from network.
        
        Args:
            balances_by_mint: Dictionary mapping mint address to amount in smallest units
        """
        self.wallet_balances = balances_by_mint.copy()
        # Log at DEBUG level to avoid spam (balances are updated frequently)
        logger.debug(f"{colors['DIM']}Wallet balances updated!{colors['RESET']}")
    
    def get_available_balance(self, mint: str) -> int:
        """Get available balance for a specific mint (total - locked).
        
        Args:
            mint: Token mint address
            
        Returns:
            Available balance in smallest units
        """
        wallet_balance = self.wallet_balances.get(mint, 0)
        locked_balance = self.locked_balances.get(mint, 0)
        return max(0, wallet_balance - locked_balance)
    
    def can_open_position(
        self,
        base_mint: str,
        amount_in: int,
        expected_profit_bps: int,
        slippage_bps: int,
        expected_profit_usdc: float = 0.0
    ) -> Tuple[bool, Optional[str]]:
        """
        Check if a position can be opened.
        
        All limits are enforced in USDC for consistency.
        Supports SOL-base and USDC-base cycles. Other bases are rejected for live mode.
        
        Args:
            base_mint: Base token mint address (first token in cycle)
            amount_in: Input amount in smallest units (lamports for SOL, units for USDC)
            expected_profit_bps: Expected profit in basis points
            slippage_bps: Expected slippage in basis points
            expected_profit_usdc: Expected profit in USDC
            
        Returns:
            (can_open: bool, reason: Optional[str])
        """
        # Check active positions limit
        active_count = len([p for p in self.active_positions.values() 
                           if p.status in ['pending', 'executing']])
        if active_count >= self.config.max_active_positions:
            return False, f"Max active positions reached: {active_count}/{self.config.max_active_positions}"
        
        # Check available balance for base token
        available = self.get_available_balance(base_mint)
        if amount_in > available:
            # Format error message with appropriate token name and decimals
            if base_mint == self.SOL_MINT:
                return False, (f"Insufficient SOL balance: need {amount_in/1e9:.4f} SOL, "
                              f"have {available/1e9:.4f} SOL")
            elif base_mint == self.USDC_MINT:
                return False, (f"Insufficient USDC balance: need {amount_in/1e6:.2f} USDC, "
                              f"have {available/1e6:.2f} USDC")
            else:
                return False, f"Insufficient balance for token {base_mint[:8]}...: need {amount_in}, have {available}"

        # Check position size limits (absolute) - converted to USDC
        if base_mint == self.SOL_MINT:
            position_sol = amount_in / 1e9
            position_usdc = position_sol * self.config.sol_price_usdc
        elif base_mint == self.USDC_MINT:
            position_usdc = amount_in / 1e6
        else:
            # For other tokens, we need an oracle to convert to USDC
            # For now, reject in live mode (can be called from execute_opportunity)
            return False, f"Unsupported base mint {base_mint[:8]}... for live mode without price oracle"
        
        if position_usdc > self.config.max_position_size_absolute_usdc:
            return False, (f"Position size exceeds absolute limit: ${position_usdc:.2f} USDC > "
                          f"${self.config.max_position_size_absolute_usdc} USDC")

        # Check position size limits (percentage) - relative to base token balance
        base_balance = self.wallet_balances.get(base_mint, 0)
        if base_balance > 0:
            position_percent = (amount_in / base_balance * 100)
            if position_percent > self.config.max_position_size_percent:
                return False, (f"Position size exceeds limit: {position_percent:.2f}% > "
                              f"{self.config.max_position_size_percent}%")
        
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
    
    def lock_balance(self, mint: str, position_id: str, amount: int):
        """Lock balance for a position.
        
        Args:
            mint: Token mint address
            position_id: Position identifier
            amount: Amount to lock in smallest units
        """
        if mint not in self.locked_balances:
            self.locked_balances[mint] = 0
        self.locked_balances[mint] += amount
        
        # Format log message based on token type
        if mint == self.SOL_MINT:
            logger.debug(f"Locked {amount/1e9:.4f} SOL for position {position_id}")
        elif mint == self.USDC_MINT:
            logger.debug(f"Locked {amount/1e6:.2f} USDC for position {position_id}")
        else:
            logger.debug(f"Locked {amount} units of {mint[:8]}... for position {position_id}")
    
    def unlock_balance(self, mint: str, position_id: str, amount: int):
        """Unlock balance for a position.
        
        Args:
            mint: Token mint address
            position_id: Position identifier
            amount: Amount to unlock in smallest units
        """
        if mint not in self.locked_balances:
            self.locked_balances[mint] = 0
        self.locked_balances[mint] = max(0, self.locked_balances[mint] - amount)
        
        # Format log message based on token type
        if mint == self.SOL_MINT:
            logger.debug(f"Unlocked {amount/1e9:.4f} SOL for position {position_id}")
        elif mint == self.USDC_MINT:
            logger.debug(f"Unlocked {amount/1e6:.2f} USDC for position {position_id}")
        else:
            logger.debug(f"Unlocked {amount} units of {mint[:8]}... for position {position_id}")
    
    def add_position(
        self,
        position_id: str,
        input_mint: str,
        output_mint: str,
        amount_in: int,
        expected_amount_out: int,
        base_mint: Optional[str] = None
    ):
        """Add a new active position.
        
        Args:
            position_id: Unique position identifier
            input_mint: Input token mint address
            output_mint: Output token mint address
            amount_in: Input amount in smallest units
            expected_amount_out: Expected output amount in smallest units
            base_mint: Base token mint (first token in cycle). If None, uses input_mint.
        """
        import time
        # Use base_mint if provided, otherwise use input_mint as fallback
        actual_base_mint = base_mint if base_mint is not None else input_mint
        
        position = Position(
            input_mint=input_mint,
            output_mint=output_mint,
            amount_in=amount_in,
            expected_amount_out=expected_amount_out,
            status='pending',
            timestamp=time.time(),
            base_mint=actual_base_mint
        )
        self.active_positions[position_id] = position
        self.lock_balance(actual_base_mint, position_id, amount_in)
        
        # Format log message based on token type
        if actual_base_mint == self.SOL_MINT:
            logger.info(f"{colors['CYAN']}Position {position_id} added:{colors['RESET']} {colors['YELLOW']}{amount_in/1e9:.4f} SOL{colors['RESET']}")
        elif actual_base_mint == self.USDC_MINT:
            logger.info(f"{colors['CYAN']}Position {position_id} added:{colors['RESET']} {colors['YELLOW']}{amount_in/1e6:.2f} USDC{colors['RESET']}")
        else:
            logger.info(f"{colors['CYAN']}Position {position_id} added:{colors['RESET']} {colors['YELLOW']}{amount_in} units{colors['RESET']} ({actual_base_mint[:8]}...)")
    
    def update_position_status(self, position_id: str, status: str):
        """Update position status."""
        if position_id in self.active_positions:
            self.active_positions[position_id].status = status
            logger.debug(f"Position {position_id} status: {status}")
    
    def remove_position(self, position_id: str):
        """Remove a completed/failed position."""
        if position_id in self.active_positions:
            position = self.active_positions[position_id]
            # Use base_mint for unlocking (stored in position)
            base_mint = getattr(position, 'base_mint', position.input_mint)
            self.unlock_balance(base_mint, position_id, position.amount_in)
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
