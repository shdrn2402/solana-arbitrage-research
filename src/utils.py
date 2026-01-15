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
        'GREEN': '\033[92m' if use_color else '',   # For numbers/amounts
        'CYAN': '\033[96m' if use_color else '',    # For labels/names
        'YELLOW': '\033[93m' if use_color else '',  # For important values (prices, profits)
        'RED': '\033[91m' if use_color else '',     # For errors/warnings
        'RESET': '\033[0m' if use_color else ''     # Reset color
    }
