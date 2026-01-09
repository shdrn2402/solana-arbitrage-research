0# RiskConfig Synchronization Summary

## Declared Fields in RiskConfig

```python
@dataclass
class RiskConfig:
    max_position_size_percent: float = 10.0
    max_position_size_absolute_usdc: float = 100.0  # in USDC
    min_profit_usdc: float = 0.1  # PRIMARY: minimum profit in USDC
    min_profit_bps: int = 50  # SECONDARY: optional filter
    max_slippage_bps: int = 50
    max_active_positions: int = 1
    sol_price_usdc: float = 100.0  # SOL price in USDC for conversion
```

## Usage in Code

### src/main.py
- ✅ `risk_config.max_position_size_percent` (line 170)
- ✅ `risk_config.max_position_size_absolute_usdc` (line 168)
- ✅ `risk_config.min_profit_usdc` (line 148)
- ✅ `risk_config.min_profit_bps` (line 147)
- ✅ `risk_config.sol_price_usdc` (line 168)

### src/risk_manager.py (via self.config)
- ✅ `self.config.max_active_positions` (line 75)
- ✅ `self.config.max_position_size_percent` (line 85)
- ✅ `self.config.max_position_size_absolute_usdc` (line 92)
- ✅ `self.config.sol_price_usdc` (line 91)
- ✅ `self.config.min_profit_usdc` (line 98)
- ✅ `self.config.min_profit_bps` (line 103)
- ✅ `self.config.max_slippage_bps` (line 108)

## Fixes

1. **src/main.py line 169**: 
   - Was: `risk_config.max_position_size_absolute` (does not exist)
   - Now: `max_position_absolute_sol_calc` (calculated from `max_position_size_absolute_usdc / sol_price_usdc`)

## Consistency Check

All attributes used in code exist in RiskConfig.
All attribute accesses use correct names.
