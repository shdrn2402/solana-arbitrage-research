"""
Utility functions for the Solana arbitrage bot.
"""
import sys
from typing import Dict


def get_terminal_colors() -> Dict[str, str]:
    """
    Get ANSI color codes for terminal output.
    
    Returns empty strings if output is not a TTY (e.g., redirected to file).
    This ensures log files remain clean without ANSI escape codes.
    
    Returns:
        Dictionary with color codes: GREEN, CYAN, YELLOW, RED, RESET
    """
    use_color = sys.stdout.isatty()
    return {
        'GREEN': '\033[92m' if use_color else '',   # Neutral numeric values (balances, quantities, config values)
        'CYAN': '\033[96m' if use_color else '',    # Identifiers and routes (tokens, paths, modes)
        'YELLOW': '\033[93m' if use_color else '',  # Key economic signals (prices, profit, bps, thresholds)
        'RED': '\033[91m' if use_color else '',     # Errors, failures, negative profit or risk violations
        'DIM': '\033[90m' if use_color else '',     # Secondary / service messages (callbacks, start/stop, low-importance logs)
        'RESET': '\033[0m' if use_color else ''     # Reset color
    }
