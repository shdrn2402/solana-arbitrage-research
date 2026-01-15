"""
Tests for utils.py
"""
import sys
import io
from unittest.mock import patch
import pytest
from src.utils import get_terminal_colors


class TestGetTerminalColors:
    """Tests for get_terminal_colors function."""
    
    def test_get_terminal_colors_with_tty(self):
        """Test get_terminal_colors returns color codes when stdout is a TTY."""
        with patch('sys.stdout.isatty', return_value=True):
            colors = get_terminal_colors()
            assert colors['GREEN'] == '\033[92m'
            assert colors['CYAN'] == '\033[96m'
            assert colors['YELLOW'] == '\033[93m'
            assert colors['RED'] == '\033[91m'
            assert colors['RESET'] == '\033[0m'
    
    def test_get_terminal_colors_without_tty(self):
        """Test get_terminal_colors returns empty strings when stdout is not a TTY."""
        with patch('sys.stdout.isatty', return_value=False):
            colors = get_terminal_colors()
            assert colors['GREEN'] == ''
            assert colors['CYAN'] == ''
            assert colors['YELLOW'] == ''
            assert colors['RED'] == ''
            assert colors['RESET'] == ''
    
    def test_get_terminal_colors_all_keys_present(self):
        """Test get_terminal_colors returns all required keys."""
        colors = get_terminal_colors()
        required_keys = ['GREEN', 'CYAN', 'YELLOW', 'RED', 'RESET']
        assert all(key in colors for key in required_keys)
