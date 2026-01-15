"""
Tests for jupiter_client.py
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
from src.jupiter_client import JupiterClient, JupiterQuote, JupiterSwapResponse


class TestJupiterClient:
    """Tests for JupiterClient class."""
    
    @pytest.fixture
    def client(self):
        """Create a JupiterClient instance for testing."""
        return JupiterClient(api_url=None, api_key=None, timeout=10.0)
    
    @pytest.fixture
    def client_with_key(self):
        """Create a JupiterClient instance with API key."""
        return JupiterClient(api_url=None, api_key="test_key", timeout=10.0)
    
    @pytest.fixture
    def client_explicit_url(self):
        """Create a JupiterClient with explicit URL."""
        return JupiterClient(api_url="https://api.jup.ag", api_key=None, timeout=10.0)
    
    def test_jupiter_client_initialization(self, client):
        """Test JupiterClient can be initialized."""
        assert client.api_url is None
        assert client.api_key is None
        assert client.timeout == 10.0
        assert len(client.fallback_endpoints) > 0
    
    def test_jupiter_client_with_api_key(self, client_with_key):
        """Test JupiterClient initialization with API key."""
        assert client_with_key.api_key == "test_key"
        # Should use authenticated endpoints
        assert len(client_with_key.fallback_endpoints) > 0
    
    def test_jupiter_client_explicit_url(self, client_explicit_url):
        """Test JupiterClient with explicit URL (no fallback)."""
        assert client_explicit_url.api_url == "https://api.jup.ag"
        assert len(client_explicit_url.fallback_endpoints) == 0
    
    @pytest.mark.asyncio
    async def test_get_quote_success(self, client, sol_mint, usdc_mint):
        """Test get_quote returns quote on success."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "inputMint": sol_mint,
            "outputMint": usdc_mint,
            "inAmount": "1000000000",
            "outAmount": "100000000",
            "priceImpactPct": 0.5,
            "routePlan": []
        }
        mock_response.raise_for_status = MagicMock()
        
        with patch.object(client.client, 'get', return_value=mock_response):
            # Set working endpoint
            client._working_endpoint = "https://quote-api.jup.ag/v6"
            
            quote = await client.get_quote(sol_mint, usdc_mint, 1_000_000_000)
            
            assert quote is not None
            assert quote.input_mint == sol_mint
            assert quote.output_mint == usdc_mint
            assert quote.in_amount == 1_000_000_000
            assert quote.out_amount == 100_000_000
            assert quote.price_impact_pct == 0.5
    
    @pytest.mark.asyncio
    async def test_get_quote_404_route_not_found(self, client, sol_mint, usdc_mint):
        """Test get_quote handles 404 (route not found) gracefully."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = "Route not found"
        
        http_error = httpx.HTTPStatusError(
            "404 Not Found",
            request=MagicMock(),
            response=mock_response
        )
        
        with patch.object(client.client, 'get', side_effect=http_error):
            client._working_endpoint = "https://quote-api.jup.ag/v6"
            
            quote = await client.get_quote(sol_mint, usdc_mint, 1_000_000_000)
            
            # Should return None for 404 (valid API response, no route available)
            assert quote is None
    
    @pytest.mark.asyncio
    async def test_get_quote_401_unauthorized(self, client, sol_mint, usdc_mint):
        """Test get_quote handles 401 (unauthorized) and marks endpoint as tried."""
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"
        
        http_error = httpx.HTTPStatusError(
            "401 Unauthorized",
            request=MagicMock(),
            response=mock_response
        )
        
        with patch.object(client.client, 'get', side_effect=http_error):
            endpoint = "https://quote-api.jup.ag/v6"
            client._working_endpoint = endpoint
            
            quote = await client.get_quote(sol_mint, usdc_mint, 1_000_000_000)
            
            assert quote is None
            assert endpoint in client._tried_endpoints
    
    @pytest.mark.asyncio
    async def test_get_quote_connection_error(self, client, sol_mint, usdc_mint):
        """Test get_quote handles connection errors and tries next endpoint."""
        connection_error = httpx.ConnectError("Connection failed")
        
        with patch.object(client.client, 'get', side_effect=connection_error):
            # Set up fallback endpoints
            client.fallback_endpoints = [
                "https://quote-api.jup.ag/v6",
                "https://api.jup.ag"
            ]
            
            quote = await client.get_quote(sol_mint, usdc_mint, 1_000_000_000)
            
            # Should return None after all endpoints fail
            assert quote is None
    
    @pytest.mark.asyncio
    async def test_get_swap_transaction_success(self, client, sol_mint, usdc_mint):
        """Test get_swap_transaction returns swap response on success."""
        quote = JupiterQuote(
            input_mint=sol_mint,
            output_mint=usdc_mint,
            in_amount=1_000_000_000,
            out_amount=100_000_000,
            price_impact_pct=0.5,
            route_plan=[]
        )
        
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "swapTransaction": "base64_encoded_tx",
            "lastValidBlockHeight": 12345
        }
        mock_response.raise_for_status = MagicMock()
        
        with patch.object(client.client, 'post', return_value=mock_response):
            client._working_endpoint = "https://api.jup.ag"
            
            swap_response = await client.get_swap_transaction(
                quote,
                "user_pubkey",
                priority_fee_lamports=10000,
                slippage_bps=50
            )
            
            assert swap_response is not None
            assert swap_response.swap_transaction == "base64_encoded_tx"
            assert swap_response.last_valid_block_height == 12345
            assert swap_response.priority_fee_lamports == 10000
    
    @pytest.mark.asyncio
    async def test_get_swap_transaction_failure(self, client, sol_mint, usdc_mint):
        """Test get_swap_transaction returns None on failure."""
        quote = JupiterQuote(
            input_mint=sol_mint,
            output_mint=usdc_mint,
            in_amount=1_000_000_000,
            out_amount=100_000_000,
            price_impact_pct=0.5,
            route_plan=[]
        )
        
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        
        http_error = httpx.HTTPStatusError(
            "500 Internal Server Error",
            request=MagicMock(),
            response=mock_response
        )
        
        with patch.object(client.client, 'post', side_effect=http_error):
            client._working_endpoint = "https://api.jup.ag"
            
            swap_response = await client.get_swap_transaction(
                quote,
                "user_pubkey"
            )
            
            assert swap_response is None
    
    @pytest.mark.asyncio
    async def test_get_sol_price_usdc_success(self, client):
        """Test get_sol_price_usdc returns price on success."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "inputMint": "So11111111111111111111111111111111111111112",
            "outputMint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
            "inAmount": "1000000000",
            "outAmount": "100000000",  # 100 USDC for 1 SOL
            "priceImpactPct": 0.1,
            "routePlan": []
        }
        mock_response.raise_for_status = MagicMock()
        
        with patch.object(client.client, 'get', return_value=mock_response):
            client._working_endpoint = "https://quote-api.jup.ag/v6"
            
            price = await client.get_sol_price_usdc(slippage_bps=10)
            
            assert price == 100.0  # 100 USDC per SOL
    
    @pytest.mark.asyncio
    async def test_get_sol_price_usdc_failure(self, client):
        """Test get_sol_price_usdc returns None on failure."""
        with patch.object(client.client, 'get', side_effect=httpx.ConnectError("Connection failed")):
            client._working_endpoint = "https://quote-api.jup.ag/v6"
            
            price = await client.get_sol_price_usdc()
            
            assert price is None
    
    @pytest.mark.asyncio
    async def test_get_sol_price_usdc_return_full_quote(self, client):
        """Test get_sol_price_usdc returns full quote when requested."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "inputMint": "So11111111111111111111111111111111111111112",
            "outputMint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
            "inAmount": "1000000000",
            "outAmount": "100000000",
            "priceImpactPct": 0.1,
            "routePlan": []
        }
        mock_response.raise_for_status = MagicMock()
        
        with patch.object(client.client, 'get', return_value=mock_response):
            client._working_endpoint = "https://quote-api.jup.ag/v6"
            
            quote = await client.get_sol_price_usdc(
                slippage_bps=10,
                return_full_quote=True
            )
            
            assert isinstance(quote, JupiterQuote)
            assert quote.out_amount == 100_000_000
    
    @pytest.mark.asyncio
    async def test_get_tokens_success(self, client):
        """Test get_tokens returns token list on success."""
        mock_response = MagicMock()
        mock_response.json.return_value = [
            {"address": "token1", "symbol": "TOKEN1"},
            {"address": "token2", "symbol": "TOKEN2"}
        ]
        mock_response.raise_for_status = MagicMock()
        
        with patch.object(client.client, 'get', return_value=mock_response):
            client._working_endpoint = "https://api.jup.ag"
            
            tokens = await client.get_tokens()
            
            assert tokens is not None
            assert len(tokens) == 2
    
    @pytest.mark.asyncio
    async def test_get_tokens_failure(self, client):
        """Test get_tokens returns None on failure."""
        with patch.object(client.client, 'get', side_effect=httpx.ConnectError("Connection failed")):
            client._working_endpoint = "https://api.jup.ag"
            
            tokens = await client.get_tokens()
            
            assert tokens is None
    
    @pytest.mark.asyncio
    async def test_close(self, client):
        """Test close method closes HTTP client."""
        await client.close()
        # Should not raise exception
        assert True
