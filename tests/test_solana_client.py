"""
Tests for solana_client.py
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import base64
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.rpc.responses import GetBalanceResp
from src.solana_client import SolanaClient


class TestSolanaClient:
    """Tests for SolanaClient class."""
    
    @pytest.fixture
    def keypair(self):
        """Create a keypair for testing."""
        return Keypair()
    
    @pytest.fixture
    def client(self, keypair):
        """Create a SolanaClient instance for testing."""
        return SolanaClient("https://api.mainnet-beta.solana.com", keypair)
    
    @pytest.fixture
    def client_no_wallet(self):
        """Create a SolanaClient without wallet."""
        return SolanaClient("https://api.mainnet-beta.solana.com", None)
    
    def test_solana_client_initialization(self, client, keypair):
        """Test SolanaClient can be initialized."""
        assert client.rpc_url_primary == "https://api.mainnet-beta.solana.com"
        assert client.wallet == keypair
    
    def test_solana_client_no_wallet(self, client_no_wallet):
        """Test SolanaClient can be initialized without wallet."""
        assert client_no_wallet.wallet is None
    
    @pytest.mark.asyncio
    async def test_get_balance_success(self, client, keypair):
        """Test get_balance returns balance on success."""
        # Create mock response with value attribute
        mock_response = MagicMock()
        mock_response.value = 1_000_000_000  # 1 SOL
        
        with patch.object(client.client, 'get_balance', return_value=mock_response):
            balance = await client.get_balance()
            
            assert balance == 1_000_000_000
    
    @pytest.mark.asyncio
    async def test_get_balance_with_pubkey(self, client_no_wallet):
        """Test get_balance with explicit pubkey."""
        pubkey = Keypair().pubkey()
        # Create mock response with value attribute
        mock_response = MagicMock()
        mock_response.value = 500_000_000
        
        with patch.object(client_no_wallet.client, 'get_balance', return_value=mock_response):
            balance = await client_no_wallet.get_balance(pubkey)
            
            assert balance == 500_000_000
    
    @pytest.mark.asyncio
    async def test_get_balance_no_wallet_no_pubkey(self, client_no_wallet):
        """Test get_balance raises error when no wallet and no pubkey."""
        with pytest.raises(ValueError, match="No wallet or pubkey provided"):
            await client_no_wallet.get_balance()
    
    @pytest.mark.asyncio
    async def test_get_balance_error(self, client, keypair):
        """Test get_balance returns 0 on error."""
        with patch.object(client.client, 'get_balance', side_effect=Exception("RPC error")):
            balance = await client.get_balance()
            
            assert balance == 0
    
    @pytest.mark.asyncio
    async def test_get_current_slot_success(self, client):
        """Test get_current_slot returns slot on success."""
        mock_response = MagicMock()
        mock_response.value = 12345
        
        with patch.object(client.client, 'get_slot', return_value=mock_response):
            slot = await client.get_current_slot()
            
            assert slot == 12345
    
    @pytest.mark.asyncio
    async def test_get_current_slot_failure(self, client):
        """Test get_current_slot returns None on failure."""
        with patch.object(client.client, 'get_slot', side_effect=Exception("RPC error")):
            slot = await client.get_current_slot()
            
            assert slot is None
    
    @pytest.mark.asyncio
    async def test_get_current_block_height_success(self, client):
        """Test get_current_block_height returns block height on success."""
        mock_response = MagicMock()
        mock_response.value = 12345
        
        with patch.object(client.client, 'get_block_height', return_value=mock_response):
            block_height = await client.get_current_block_height()
            
            assert block_height == 12345
    
    @pytest.mark.asyncio
    async def test_get_current_block_height_failure(self, client):
        """Test get_current_block_height returns None on failure."""
        with patch.object(client.client, 'get_block_height', side_effect=Exception("RPC error")):
            block_height = await client.get_current_block_height()
            
            assert block_height is None
    
    @pytest.mark.asyncio
    async def test_simulate_transaction_success(self, client):
        """Test simulate_transaction returns result on success."""
        # Create a mock transaction (base64 encoded)
        mock_tx_bytes = b"mock_transaction_bytes"
        mock_tx_base64 = base64.b64encode(mock_tx_bytes).decode()
        
        # Create mock simulation result with value attribute
        mock_sim_result = MagicMock()
        mock_sim_result.err = None
        mock_sim_result.logs = ["Program log: test"]
        mock_sim_result.accounts = None
        mock_sim_result.units_consumed = 1000
        mock_sim_result.return_data = None
        
        mock_response = MagicMock()
        mock_response.value = mock_sim_result
        
        with patch('src.solana_client.VersionedTransaction') as mock_versioned_tx:
            mock_versioned_tx.from_bytes.return_value = MagicMock()
            
            with patch.object(client.client, 'simulate_transaction', return_value=mock_response):
                result = await client.simulate_transaction(mock_tx_base64)
                
                assert result is not None
                assert result["err"] is None
                assert len(result["logs"]) == 1
    
    @pytest.mark.asyncio
    async def test_simulate_transaction_with_error(self, client):
        """Test simulate_transaction returns result with error."""
        mock_tx_bytes = b"mock_transaction_bytes"
        mock_tx_base64 = base64.b64encode(mock_tx_bytes).decode()
        
        # Create mock simulation result with error
        mock_sim_result = MagicMock()
        mock_sim_result.err = {"code": 1, "name": "InsufficientFundsForFee"}
        mock_sim_result.logs = ["Program log: error"]
        mock_sim_result.accounts = None
        mock_sim_result.units_consumed = 0
        mock_sim_result.return_data = None
        
        mock_response = MagicMock()
        mock_response.value = mock_sim_result
        
        with patch('src.solana_client.VersionedTransaction') as mock_versioned_tx:
            mock_versioned_tx.from_bytes.return_value = MagicMock()
            
            with patch.object(client.client, 'simulate_transaction', return_value=mock_response):
                result = await client.simulate_transaction(mock_tx_base64)
                
                assert result is not None
                assert result["err"] is not None
                assert result["err"]["code"] == 1
    
    @pytest.mark.asyncio
    async def test_simulate_transaction_failure(self, client):
        """Test simulate_transaction returns None on failure."""
        mock_tx_base64 = base64.b64encode(b"invalid").decode()
        
        with patch('src.solana_client.VersionedTransaction', side_effect=Exception("Decode error")):
            result = await client.simulate_transaction(mock_tx_base64)
            
            assert result is None
    
    @pytest.mark.asyncio
    async def test_send_transaction_success(self, client, keypair):
        """Test send_transaction returns signature on success."""
        mock_tx_bytes = b"mock_transaction_bytes"
        mock_tx_base64 = base64.b64encode(mock_tx_bytes).decode()
        
        mock_sig = "5VERv8NMvzbJMEkV8xnrLkEaWRtSz9CosKDYjCJjBRnbJLgp8uirBgmQpjKhoR4tjF3ZpRzrFmBVGHVuRdUSv8Z"
        
        mock_response = MagicMock()
        mock_response.value = mock_sig
        
        with patch('src.solana_client.VersionedTransaction') as mock_versioned_tx:
            mock_tx = MagicMock()
            mock_versioned_tx.from_bytes.return_value = mock_tx
            
            with patch.object(client.client, 'send_transaction', return_value=mock_response):
                signature = await client.send_transaction(mock_tx_base64)
                
                assert signature == mock_sig
                # Verify transaction was signed
                assert mock_tx.sign.called
    
    @pytest.mark.asyncio
    async def test_send_transaction_no_wallet(self, client_no_wallet):
        """Test send_transaction works without wallet (transaction already signed)."""
        mock_tx_bytes = b"mock_transaction_bytes"
        mock_tx_base64 = base64.b64encode(mock_tx_bytes).decode()
        
        mock_sig = "5VERv8NMvzbJMEkV8xnrLkEaWRtSz9CosKDYjCJjBRnbJLgp8uirBgmQpjKhoR4tjF3ZpRzrFmBVGHVuRdUSv8Z"
        
        mock_response = MagicMock()
        mock_response.value = mock_sig
        
        with patch('src.solana_client.VersionedTransaction') as mock_versioned_tx:
            mock_tx = MagicMock()
            mock_versioned_tx.from_bytes.return_value = mock_tx
            
            with patch.object(client_no_wallet.client, 'send_transaction', return_value=mock_response):
                signature = await client_no_wallet.send_transaction(mock_tx_base64)
                
                assert signature == mock_sig
    
    @pytest.mark.asyncio
    async def test_send_transaction_failure(self, client, keypair):
        """Test send_transaction returns None on failure."""
        mock_tx_base64 = base64.b64encode(b"invalid").decode()
        
        with patch('src.solana_client.VersionedTransaction', side_effect=Exception("Decode error")):
            signature = await client.send_transaction(mock_tx_base64)
            
            assert signature is None
    
    @pytest.mark.asyncio
    async def test_confirm_transaction_success(self, client):
        """Test confirm_transaction returns True on success."""
        mock_sig = "5VERv8NMvzbJMEkV8xnrLkEaWRtSz9CosKDYjCJjBRnbJLgp8uirBgmQpjKhoR4tjF3ZpRzrFmBVGHVuRdUSv8Z"
        
        mock_confirmation = MagicMock()
        mock_confirmation.confirmation_status = "confirmed"
        
        mock_response = MagicMock()
        mock_response.value = [mock_confirmation]
        
        with patch.object(client.client, 'confirm_transaction', return_value=mock_response):
            confirmed = await client.confirm_transaction(mock_sig)
            
            assert confirmed is True
    
    @pytest.mark.asyncio
    async def test_confirm_transaction_failure(self, client):
        """Test confirm_transaction returns False on failure."""
        mock_sig = "5VERv8NMvzbJMEkV8xnrLkEaWRtSz9CosKDYjCJjBRnbJLgp8uirBgmQpjKhoR4tjF3ZpRzrFmBVGHVuRdUSv8Z"
        
        with patch.object(client.client, 'confirm_transaction', side_effect=Exception("RPC error")):
            confirmed = await client.confirm_transaction(mock_sig)
            
            assert confirmed is False
    
    @pytest.mark.asyncio
    async def test_confirm_transaction_processed(self, client):
        """Test confirm_transaction_processed returns True on success."""
        mock_sig = "5VERv8NMvzbJMEkV8xnrLkEaWRtSz9CosKDYjCJjBRnbJLgp8uirBgmQpjKhoR4tjF3ZpRzrFmBVGHVuRdUSv8Z"
        
        mock_confirmation = MagicMock()
        mock_confirmation.confirmation_status = "processed"
        
        mock_response = MagicMock()
        mock_response.value = [mock_confirmation]
        
        with patch.object(client.client, 'confirm_transaction', return_value=mock_response):
            confirmed = await client.confirm_transaction_processed(mock_sig, timeout=2.0)
            
            assert confirmed is True
    
    @pytest.mark.asyncio
    async def test_close(self, client):
        """Test close method closes RPC client."""
        await client.close()
        # Should not raise exception
        assert True
    
    @pytest.mark.asyncio
    async def test_get_address_lookup_table_accounts_list_format(self, client):
        """Test get_address_lookup_table_accounts parses ALT data from list format."""
        from solders.address_lookup_table_account import AddressLookupTableAccount, AddressLookupTable
        
        # Create mock ALT data (minimal valid ALT structure)
        alt_data_bytes = b'\x01' + b'\x00' * 100  # Minimal structure
        alt_data_base64 = base64.b64encode(alt_data_bytes).decode()
        
        # Use valid pubkey instead of invalid base58 string
        pubkey = Pubkey.default()
        alt_address = str(pubkey)
        
        # Mock account info with list format: ["<base64>", "base64"]
        mock_account_value = MagicMock()
        mock_account_value.data = [alt_data_base64, "base64"]
        
        mock_account_info = MagicMock()
        mock_account_info.value = mock_account_value
        
        with patch.object(client.client, 'get_account_info', return_value=mock_account_info):
                # Mock AddressLookupTable.deserialize to return a table with addresses
                mock_table = MagicMock(spec=AddressLookupTable)
                mock_table.addresses = []
                
                with patch('src.solana_client.AddressLookupTable.deserialize', return_value=mock_table):
                    # Mock AddressLookupTableAccount constructor
                    mock_alt_account = MagicMock(spec=AddressLookupTableAccount)
                    mock_alt_account.addresses = []
                    
                    with patch('src.solana_client.AddressLookupTableAccount', return_value=mock_alt_account) as mock_alt_ctor:
                        result = await client.get_address_lookup_table_accounts([alt_address])
                        
                        assert len(result) == 1
                        assert result[0] == mock_alt_account
                        # Verify constructor was called with pubkey and table.addresses
                        mock_alt_ctor.assert_called_once_with(pubkey, mock_table.addresses)
    
    @pytest.mark.asyncio
    async def test_get_address_lookup_table_accounts_string_format(self, client):
        """Test get_address_lookup_table_accounts parses ALT data from base64 string format."""
        from solders.address_lookup_table_account import AddressLookupTableAccount, AddressLookupTable
        
        alt_data_bytes = b'\x01' + b'\x00' * 100
        alt_data_base64 = base64.b64encode(alt_data_bytes).decode()
        
        # Use valid pubkey instead of invalid base58 string
        pubkey = Pubkey.default()
        alt_address = str(pubkey)
        
        # Mock account info with string format (base64)
        mock_account_value = MagicMock()
        mock_account_value.data = alt_data_base64
        
        mock_account_info = MagicMock()
        mock_account_info.value = mock_account_value
        
        with patch.object(client.client, 'get_account_info', return_value=mock_account_info):
                mock_table = MagicMock(spec=AddressLookupTable)
                mock_table.addresses = []
                
                with patch('src.solana_client.AddressLookupTable.deserialize', return_value=mock_table):
                    mock_alt_account = MagicMock(spec=AddressLookupTableAccount)
                    mock_alt_account.addresses = []
                    
                    with patch('src.solana_client.AddressLookupTableAccount', return_value=mock_alt_account) as mock_alt_ctor:
                        result = await client.get_address_lookup_table_accounts([alt_address])
                        
                        assert len(result) == 1
                        assert result[0] == mock_alt_account
                        mock_alt_ctor.assert_called_once_with(pubkey, mock_table.addresses)
    
    @pytest.mark.asyncio
    async def test_get_address_lookup_table_accounts_bytes_with_base64_fallback(self, client):
        """Test get_address_lookup_table_accounts handles bytes containing ASCII-base64 with fallback."""
        from solders.address_lookup_table_account import AddressLookupTableAccount, AddressLookupTable
        
        alt_data_bytes = b'\x01' + b'\x00' * 100
        alt_data_base64_str = base64.b64encode(alt_data_bytes).decode()
        alt_data_base64_bytes = alt_data_base64_str.encode('ascii')
        
        # Use valid pubkey instead of invalid base58 string
        pubkey = Pubkey.default()
        alt_address = str(pubkey)
        
        # Mock account info with bytes format (containing ASCII-base64)
        mock_account_value = MagicMock()
        mock_account_value.data = alt_data_base64_bytes
        
        mock_account_info = MagicMock()
        mock_account_info.value = mock_account_value
        
        with patch.object(client.client, 'get_account_info', return_value=mock_account_info):
                mock_alt_account = MagicMock(spec=AddressLookupTableAccount)
                mock_alt_account.addresses = []
                
                # First call fails (trying raw bytes), second succeeds (after base64 decode)
                with patch('src.solana_client.AddressLookupTable.deserialize') as mock_deserialize:
                    mock_table = MagicMock(spec=AddressLookupTable)
                    mock_table.addresses = []
                    
                    # First call fails with exception, second succeeds with decoded bytes
                    mock_deserialize.side_effect = [
                        Exception("unexpected end of file"),  # First attempt (raw bytes)
                        mock_table  # Second attempt (after base64 decode)
                    ]
                    
                    with patch('src.solana_client.AddressLookupTableAccount', return_value=mock_alt_account) as mock_alt_ctor:
                        result = await client.get_address_lookup_table_accounts([alt_address])
                        
                        # Should have tried twice: once with raw bytes, once with decoded
                        assert mock_deserialize.call_count == 2
                        assert len(result) == 1
                        assert result[0] == mock_alt_account
                        
                        # Verify second call was with decoded bytes
                        second_call_args = mock_deserialize.call_args_list[1]
                        assert second_call_args[0][0] == alt_data_bytes
                        # Verify constructor was called with pubkey and table.addresses
                        mock_alt_ctor.assert_called_once_with(pubkey, mock_table.addresses)
    
    @pytest.mark.asyncio
    async def test_get_address_lookup_table_accounts_bytes_raw_success(self, client):
        """Test get_address_lookup_table_accounts handles raw bytes successfully."""
        from solders.address_lookup_table_account import AddressLookupTableAccount, AddressLookupTable
        
        alt_data_bytes = b'\x01' + b'\x00' * 100
        
        # Use valid pubkey instead of invalid base58 string
        pubkey = Pubkey.default()
        alt_address = str(pubkey)
        
        # Mock account info with raw bytes format
        mock_account_value = MagicMock()
        mock_account_value.data = alt_data_bytes
        
        mock_account_info = MagicMock()
        mock_account_info.value = mock_account_value
        
        with patch.object(client.client, 'get_account_info', return_value=mock_account_info):
                mock_table = MagicMock(spec=AddressLookupTable)
                mock_table.addresses = []
                
                with patch('src.solana_client.AddressLookupTable.deserialize', return_value=mock_table):
                    mock_alt_account = MagicMock(spec=AddressLookupTableAccount)
                    mock_alt_account.addresses = []
                    
                    with patch('src.solana_client.AddressLookupTableAccount', return_value=mock_alt_account) as mock_alt_ctor:
                        result = await client.get_address_lookup_table_accounts([alt_address])
                        
                        assert len(result) == 1
                        assert result[0] == mock_alt_account
                        mock_alt_ctor.assert_called_once_with(pubkey, mock_table.addresses)