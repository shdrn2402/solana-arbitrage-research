"""
Pytest configuration and fixtures for Solana Arbitrage Bot tests.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from solders.keypair import Keypair
from solders.pubkey import Pubkey

from src.risk_manager import RiskConfig


@pytest.fixture
def risk_config():
    """Create a default RiskConfig for testing."""
    return RiskConfig(
        max_position_size_percent=10.0,
        max_position_size_absolute_usdc=100.0,
        min_profit_usdc=0.1,
        min_profit_bps=50,
        max_slippage_bps=50,
        max_active_positions=1,
        sol_price_usdc=100.0
    )


@pytest.fixture
def mock_keypair():
    """Create a mock keypair for testing."""
    return Keypair()


@pytest.fixture
def mock_jupiter_client():
    """Create a mock JupiterClient for testing."""
    client = AsyncMock()
    return client


@pytest.fixture
def mock_solana_client():
    """Create a mock SolanaClient for testing."""
    client = AsyncMock()
    return client


@pytest.fixture
def sol_mint():
    """SOL mint address."""
    return "So11111111111111111111111111111111111111112"


@pytest.fixture
def usdc_mint():
    """USDC mint address."""
    return "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


@pytest.fixture
def jup_mint():
    """JUP mint address."""
    return "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN"


@pytest.fixture
def bonk_mint():
    """BONK mint address."""
    return "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
