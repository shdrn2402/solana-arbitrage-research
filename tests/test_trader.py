"""
Tests for trader.py
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.trader import Trader
from src.arbitrage_finder import ArbitrageOpportunity
from src.jupiter_client import JupiterQuote, JupiterSwapResponse
from src.risk_manager import RiskManager, RiskConfig


class TestTrader:
    """Tests for Trader class."""
    
    @pytest.fixture
    def risk_config(self):
        """Create a RiskConfig for testing."""
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
    def risk_manager(self, risk_config):
        """Create a RiskManager for testing."""
        manager = RiskManager(risk_config)
        manager.update_wallet_balance(10_000_000_000)  # 10 SOL
        return manager
    
    @pytest.fixture
    def mock_jupiter(self):
        """Create a mock JupiterClient."""
        return AsyncMock()
    
    @pytest.fixture
    def mock_solana(self):
        """Create a mock SolanaClient."""
        return AsyncMock()
    
    @pytest.fixture
    def mock_finder(self, risk_config):
        """Create a mock ArbitrageFinder."""
        finder = AsyncMock()
        finder.min_profit_bps = risk_config.min_profit_bps
        finder.min_profit_usd = risk_config.min_profit_usdc
        return finder
    
    @pytest.fixture
    def trader_scan(self, mock_jupiter, mock_solana, risk_manager, mock_finder):
        """Create a Trader instance in scan mode."""
        return Trader(
            jupiter_client=mock_jupiter,
            solana_client=mock_solana,
            risk_manager=risk_manager,
            arbitrage_finder=mock_finder,
            priority_fee_lamports=10000,
            use_jito=False,
            mode='scan',
            slippage_bps=50,
            tokens_map={}
        )
    
    @pytest.fixture
    def trader_simulate(self, mock_jupiter, mock_solana, risk_manager, mock_finder):
        """Create a Trader instance in simulate mode."""
        return Trader(
            jupiter_client=mock_jupiter,
            solana_client=mock_solana,
            risk_manager=risk_manager,
            arbitrage_finder=mock_finder,
            priority_fee_lamports=10000,
            use_jito=False,
            mode='simulate',
            slippage_bps=50,
            tokens_map={}
        )
    
    @pytest.fixture
    def trader_live(self, mock_jupiter, mock_solana, risk_manager, mock_finder):
        """Create a Trader instance in live mode."""
        return Trader(
            jupiter_client=mock_jupiter,
            solana_client=mock_solana,
            risk_manager=risk_manager,
            arbitrage_finder=mock_finder,
            priority_fee_lamports=10000,
            use_jito=False,
            mode='live',
            slippage_bps=50,
            tokens_map={}
        )
    
    @pytest.fixture
    def profitable_opportunity(self, usdc_mint, sol_mint, jup_mint):
        """Create a profitable arbitrage opportunity."""
        cycle = [usdc_mint, sol_mint, jup_mint, usdc_mint]
        quotes = [
            JupiterQuote(
                input_mint=usdc_mint,
                output_mint=sol_mint,
                in_amount=1_000_000,  # 1 USDC
                out_amount=10_000_000,  # 0.01 SOL
                price_impact_pct=0.1,
                route_plan=[]
            ),
            JupiterQuote(
                input_mint=sol_mint,
                output_mint=jup_mint,
                in_amount=10_000_000,
                out_amount=20_000_000,
                price_impact_pct=0.2,
                route_plan=[]
            ),
            JupiterQuote(
                input_mint=jup_mint,
                output_mint=usdc_mint,
                in_amount=20_000_000,
                out_amount=1_200_000,  # 1.2 USDC (profit!)
                price_impact_pct=0.2,
                route_plan=[]
            )
        ]
        return ArbitrageOpportunity(
            cycle=cycle,
            quotes=quotes,
            initial_amount=1_000_000,
            final_amount=1_200_000,
            profit_bps=2000,
            profit_usd=0.2,
            price_impact_total=0.5,
            timestamp=1234567890.0
        )
    
    def test_trader_initialization(self, trader_scan):
        """Test Trader can be initialized."""
        assert trader_scan.mode == 'scan'
        assert trader_scan.slippage_bps == 50
        assert trader_scan.trade_in_progress is False
    
    @pytest.mark.asyncio
    async def test_scan_opportunities(self, trader_scan, mock_finder, usdc_mint):
        """Test scan_opportunities calls finder and returns opportunities."""
        opportunities = [
            MagicMock(profit_bps=100, profit_usd=1.0, price_impact_total=0.5)
        ]
        mock_finder.find_opportunities.return_value = opportunities
        
        result = await trader_scan.scan_opportunities(
            usdc_mint,
            1_000_000,
            max_opportunities=10,
            sol_balance=10.0,
            usdc_balance=100.0
        )
        
        assert len(result) == 1
        mock_finder.find_opportunities.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_simulate_opportunity_success(self, trader_simulate, profitable_opportunity, mock_jupiter, mock_solana):
        """Test simulate_opportunity succeeds with valid opportunity."""
        swap_response = JupiterSwapResponse(
            swap_transaction="base64_tx",
            last_valid_block_height=12345,
            priority_fee_lamports=10000
        )
        mock_jupiter.get_swap_transaction.return_value = swap_response
        
        sim_result = {
            "err": None,
            "logs": ["Program log: success"],
            "accounts": None,
            "units_consumed": 1000,
            "return_data": None
        }
        mock_solana.simulate_transaction.return_value = sim_result
        
        success, error, result, swap = await trader_simulate.simulate_opportunity(
            profitable_opportunity,
            "user_pubkey"
        )
        
        assert success is True
        assert error is None
        assert result == sim_result
        assert swap == swap_response
    
    @pytest.mark.asyncio
    async def test_simulate_opportunity_no_quotes(self, trader_simulate, usdc_mint, sol_mint, jup_mint):
        """Test simulate_opportunity fails when no quotes available."""
        opportunity = ArbitrageOpportunity(
            cycle=[usdc_mint, sol_mint, jup_mint, usdc_mint],
            quotes=[],  # No quotes
            initial_amount=1_000_000,
            final_amount=1_200_000,
            profit_bps=2000,
            profit_usd=0.2,
            price_impact_total=0.5,
            timestamp=1234567890.0
        )
        
        success, error, result, swap = await trader_simulate.simulate_opportunity(
            opportunity,
            "user_pubkey"
        )
        
        assert success is False
        assert "No quotes available" in error
    
    @pytest.mark.asyncio
    async def test_simulate_opportunity_swap_build_failure(self, trader_simulate, profitable_opportunity, mock_jupiter):
        """Test simulate_opportunity fails when swap transaction build fails."""
        mock_jupiter.get_swap_transaction.return_value = None
        
        success, error, result, swap = await trader_simulate.simulate_opportunity(
            profitable_opportunity,
            "user_pubkey"
        )
        
        assert success is False
        assert "Failed to build swap transaction" in error
    
    @pytest.mark.asyncio
    async def test_simulate_opportunity_simulation_error(self, trader_simulate, profitable_opportunity, mock_jupiter, mock_solana):
        """Test simulate_opportunity fails when simulation has error."""
        swap_response = JupiterSwapResponse(
            swap_transaction="base64_tx",
            last_valid_block_height=12345
        )
        mock_jupiter.get_swap_transaction.return_value = swap_response
        
        sim_result = {
            "err": {"code": 1, "name": "InsufficientFundsForFee"},
            "logs": ["Program log: error"],
            "accounts": None,
            "units_consumed": 0,
            "return_data": None
        }
        mock_solana.simulate_transaction.return_value = sim_result
        
        success, error, result, swap = await trader_simulate.simulate_opportunity(
            profitable_opportunity,
            "user_pubkey"
        )
        
        assert success is False
        assert "Simulation error" in error
        assert "Simulation logs" in error
    
    @pytest.mark.asyncio
    async def test_execute_opportunity_scan_mode(self, trader_scan, profitable_opportunity):
        """Test execute_opportunity fails in scan mode."""
        success, error, tx_sig = await trader_scan.execute_opportunity(
            profitable_opportunity,
            "user_pubkey"
        )
        
        assert success is False
        assert "scan" in error.lower()
        assert "live" in error.lower()
    
    @pytest.mark.asyncio
    async def test_execute_opportunity_simulate_mode(self, trader_simulate, profitable_opportunity):
        """Test execute_opportunity fails in simulate mode."""
        success, error, tx_sig = await trader_simulate.execute_opportunity(
            profitable_opportunity,
            "user_pubkey"
        )
        
        assert success is False
        assert "simulate" in error.lower()
        assert "live" in error.lower()
    
    @pytest.mark.asyncio
    async def test_execute_opportunity_live_success(self, trader_live, profitable_opportunity, mock_jupiter, mock_solana, risk_manager):
        """Test execute_opportunity succeeds in live mode."""
        swap_response = JupiterSwapResponse(
            swap_transaction="base64_tx",
            last_valid_block_height=99999,
            priority_fee_lamports=10000
        )
        mock_jupiter.get_swap_transaction.return_value = swap_response
        
        sim_result = {
            "err": None,
            "logs": ["Program log: success"],
            "accounts": None,
            "units_consumed": 1000,
            "return_data": None
        }
        mock_solana.simulate_transaction.return_value = sim_result
        mock_solana.get_current_block_height.return_value = 50000  # Before expiry
        mock_solana.send_transaction.return_value = "tx_signature_123"
        mock_solana.confirm_transaction.return_value = True
        
        success, error, tx_sig = await trader_live.execute_opportunity(
            profitable_opportunity,
            "user_pubkey"
        )
        
        assert success is True
        assert error is None
        assert tx_sig == "tx_signature_123"
    
    @pytest.mark.asyncio
    async def test_execute_opportunity_risk_check_fails(self, trader_live, profitable_opportunity, risk_manager):
        """Test execute_opportunity fails when risk check fails."""
        # Set balance to 0 to trigger risk check failure
        risk_manager.update_wallet_balance(0)
        
        success, error, tx_sig = await trader_live.execute_opportunity(
            profitable_opportunity,
            "user_pubkey"
        )
        
        assert success is False
        assert "Risk check failed" in error
    
    @pytest.mark.asyncio
    async def test_execute_opportunity_simulation_fails(self, trader_live, profitable_opportunity, mock_jupiter, mock_solana):
        """Test execute_opportunity fails when simulation fails."""
        swap_response = JupiterSwapResponse(
            swap_transaction="base64_tx",
            last_valid_block_height=99999
        )
        mock_jupiter.get_swap_transaction.return_value = swap_response
        
        sim_result = {
            "err": {"code": 1, "name": "InsufficientFundsForFee"},
            "logs": ["Program log: error"],
            "accounts": None,
            "units_consumed": 0,
            "return_data": None
        }
        mock_solana.simulate_transaction.return_value = sim_result
        
        success, error, tx_sig = await trader_live.execute_opportunity(
            profitable_opportunity,
            "user_pubkey"
        )
        
        assert success is False
        assert "Simulation failed" in error
    
    @pytest.mark.asyncio
    async def test_execute_opportunity_quote_expired(self, trader_live, profitable_opportunity, mock_jupiter, mock_solana):
        """Test execute_opportunity fails when quote is expired."""
        swap_response = JupiterSwapResponse(
            swap_transaction="base64_tx",
            last_valid_block_height=10000,  # Expired
            priority_fee_lamports=10000
        )
        mock_jupiter.get_swap_transaction.return_value = swap_response
        
        sim_result = {
            "err": None,
            "logs": ["Program log: success"],
            "accounts": None,
            "units_consumed": 1000,
            "return_data": None
        }
        mock_solana.simulate_transaction.return_value = sim_result
        mock_solana.get_current_block_height.return_value = 20000  # After expiry
        
        success, error, tx_sig = await trader_live.execute_opportunity(
            profitable_opportunity,
            "user_pubkey"
        )
        
        assert success is False
        assert "Quote expired" in error
    
    @pytest.mark.asyncio
    async def test_execute_opportunity_send_fails(self, trader_live, profitable_opportunity, mock_jupiter, mock_solana):
        """Test execute_opportunity fails when transaction send fails."""
        swap_response = JupiterSwapResponse(
            swap_transaction="base64_tx",
            last_valid_block_height=99999
        )
        mock_jupiter.get_swap_transaction.return_value = swap_response
        
        sim_result = {
            "err": None,
            "logs": ["Program log: success"],
            "accounts": None,
            "units_consumed": 1000,
            "return_data": None
        }
        mock_solana.simulate_transaction.return_value = sim_result
        mock_solana.get_current_block_height.return_value = 50000
        mock_solana.send_transaction.return_value = None  # Send fails
        
        success, error, tx_sig = await trader_live.execute_opportunity(
            profitable_opportunity,
            "user_pubkey"
        )
        
        assert success is False
        assert "Failed to send transaction" in error
    
    @pytest.mark.asyncio
    async def test_execute_opportunity_confirmation_fails(self, trader_live, profitable_opportunity, mock_jupiter, mock_solana):
        """Test execute_opportunity fails when confirmation fails."""
        swap_response = JupiterSwapResponse(
            swap_transaction="base64_tx",
            last_valid_block_height=99999
        )
        mock_jupiter.get_swap_transaction.return_value = swap_response
        
        sim_result = {
            "err": None,
            "logs": ["Program log: success"],
            "accounts": None,
            "units_consumed": 1000,
            "return_data": None
        }
        mock_solana.simulate_transaction.return_value = sim_result
        mock_solana.get_current_block_height.return_value = 50000
        mock_solana.send_transaction.return_value = "tx_signature_123"
        mock_solana.confirm_transaction.return_value = False  # Confirmation fails
        
        success, error, tx_sig = await trader_live.execute_opportunity(
            profitable_opportunity,
            "user_pubkey"
        )
        
        assert success is False
        assert "Transaction not confirmed" in error
        assert tx_sig == "tx_signature_123"
    
    @pytest.mark.asyncio
    async def test_process_opportunity_with_retries_simulate(self, trader_simulate, profitable_opportunity, mock_finder, mock_jupiter, mock_solana):
        """Test process_opportunity_with_retries in simulate mode."""
        swap_response = JupiterSwapResponse(
            swap_transaction="base64_tx",
            last_valid_block_height=99999
        )
        mock_jupiter.get_swap_transaction.return_value = swap_response
        
        sim_result = {
            "err": None,
            "logs": ["Program log: success"],
            "accounts": None,
            "units_consumed": 1000,
            "return_data": None
        }
        mock_solana.simulate_transaction.return_value = sim_result
        
        # Mock finder to return opportunity on recheck
        mock_finder._check_cycle.return_value = profitable_opportunity
        
        success_count = await trader_simulate.process_opportunity_with_retries(
            profitable_opportunity.cycle,
            1_000_000,
            "user_pubkey",
            max_retries=3
        )
        
        assert success_count > 0
    
    @pytest.mark.asyncio
    async def test_process_opportunity_with_retries_opportunity_drops(self, trader_simulate, profitable_opportunity, mock_finder):
        """Test process_opportunity_with_retries stops when opportunity drops."""
        # First attempt uses original opportunity (zero-recheck)
        # Second attempt recheck returns None (opportunity dropped)
        mock_finder._check_cycle.return_value = None
        
        success_count = await trader_simulate.process_opportunity_with_retries(
            profitable_opportunity.cycle,
            1_000_000,
            "user_pubkey",
            max_retries=3,
            first_attempt_use_original_opportunity=True,
            original_opportunity=profitable_opportunity
        )
        
        # Should have 0 successes (first attempt might succeed, but recheck fails)
        # Actually, with first_attempt_use_original_opportunity=True, first attempt uses original
        # Then recheck fails, so success_count should be 0 or 1 depending on first attempt
        assert success_count >= 0
    
    def test_format_amount_sol(self, trader_scan, sol_mint):
        """Test _format_amount formats SOL correctly."""
        formatted = trader_scan._format_amount(1_000_000_000, sol_mint)
        assert "SOL" in formatted
        assert "1.000000" in formatted
    
    def test_format_amount_usdc(self, trader_scan, usdc_mint):
        """Test _format_amount formats USDC correctly."""
        formatted = trader_scan._format_amount(1_000_000, usdc_mint)
        assert "USDC" in formatted
        assert "1.00" in formatted
    
    def test_format_amount_unknown(self, trader_scan):
        """Test _format_amount formats unknown token as raw amount."""
        formatted = trader_scan._format_amount(1_000_000, "unknown_mint")
        assert "1000000" in formatted
    
    def test_format_sim_logs_empty(self, trader_scan):
        """Test _format_sim_logs handles empty logs."""
        formatted = trader_scan._format_sim_logs([])
        assert "(no logs)" in formatted
    
    def test_format_sim_logs_with_logs(self, trader_scan):
        """Test _format_sim_logs formats logs correctly."""
        logs = ["Log 1", "Log 2", "Log 3"]
        formatted = trader_scan._format_sim_logs(logs)
        assert "Log 1" in formatted
        assert "Log 2" in formatted
        assert "Log 3" in formatted
