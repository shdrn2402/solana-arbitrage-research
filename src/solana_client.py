"""
Solana RPC client for balance checks, simulation, and transaction sending.
"""
import asyncio
import base58
import base64
import logging
from typing import Optional, Dict, Any, List
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.hash import Hash
from solders.rpc.responses import GetBalanceResp
from solders.transaction import Transaction, VersionedTransaction
from solders.address_lookup_table_account import AddressLookupTableAccount
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed, Processed
from solana.rpc.types import TxOpts

logger = logging.getLogger(__name__)


class SolanaClient:
    """Client for Solana RPC operations."""
    
    def __init__(self, rpc_url: str, wallet_keypair: Optional[Keypair] = None):
        self.rpc_url = rpc_url
        self.client = AsyncClient(rpc_url)
        self.wallet = wallet_keypair
    
    async def get_balance(self, pubkey: Optional[Pubkey] = None) -> int:
        """
        Get SOL balance in lamports.
        
        Args:
            pubkey: Public key (defaults to wallet)
        
        Returns:
            Balance in lamports
        """
        if pubkey is None:
            if self.wallet is None:
                raise ValueError("No wallet or pubkey provided")
            pubkey = self.wallet.pubkey()
        
        try:
            resp: GetBalanceResp = await self.client.get_balance(pubkey, commitment=Confirmed)
            return resp.value
        except Exception as e:
            logger.error(f"Error getting balance: {e}")
            return 0
    
    async def get_current_slot(self) -> Optional[int]:
        """
        Get current slot from Solana RPC.
        
        Note: Slot is different from block height. Use get_current_block_height()
        for comparing with Jupiter's lastValidBlockHeight.
        
        Returns:
            Current slot number (int) if successful, None if error occurred
        """
        try:
            result = await self.client.get_slot(commitment=Confirmed)
            if result.value is not None:
                logger.debug(f"Current slot: {result.value}")
                return result.value
            else:
                logger.warning("get_slot returned None")
                return None
        except Exception as e:
            logger.error(f"Error getting current slot: {e}")
            return None
    
    async def get_current_block_height(self) -> Optional[int]:
        """
        Get current block height from Solana RPC.
        
        Used to validate Jupiter lastValidBlockHeight.
        Block height is different from slot - use this for quote expiry checks.
        
        Returns:
            Current block height (int) if successful, None if error occurred
        """
        try:
            result = await self.client.get_block_height(commitment=Confirmed)
            if result.value is not None:
                logger.debug(f"Current block height: {result.value}")
                return result.value
            logger.warning("get_block_height returned None")
            return None
        except Exception as e:
            logger.error(f"Error getting block height: {e}")
            return None
    
    async def simulate_transaction(
        self,
        transaction_base64: str,
        commitment: str = "confirmed"
    ) -> Optional[Dict[str, Any]]:
        """
        Simulate a transaction.
        
        Args:
            transaction_base64: Base64-encoded transaction
            commitment: Commitment level
        
        Returns:
            Simulation result dict or None
        """
        try:
            # Decode transaction (Jupiter API returns base64, not base58)
            # Jupiter API returns VersionedTransaction, not legacy Transaction
            tx_bytes = base64.b64decode(transaction_base64)
            
            # Try VersionedTransaction first (Jupiter API format)
            try:
                transaction = VersionedTransaction.from_bytes(tx_bytes)
            except Exception:
                # Fallback to legacy Transaction if VersionedTransaction fails
                transaction = Transaction.from_bytes(tx_bytes)
            
            # Simulate
            result = await self.client.simulate_transaction(
                transaction,
                commitment=commitment
            )
            
            # Always return simulation result dict, even on error
            # This allows higher-level code to access logs for debugging
            sim_result = {
                "err": result.value.err,
                "logs": result.value.logs or [],
                "accounts": result.value.accounts,
                "units_consumed": result.value.units_consumed,
                "return_data": result.value.return_data
            }
            
            if result.value.err:
                # Log concise warning with error code (full logs will be printed by caller)
                logger.warning(f"Simulation error: {result.value.err}")
            
            return sim_result
            
        except Exception as e:
            logger.error(f"Error simulating transaction: {e}")
            return None
    
    async def simulate_versioned_transaction(
        self,
        tx: VersionedTransaction,
        commitment: str = "confirmed"
    ) -> Optional[Dict[str, Any]]:
        """
        Simulate a VersionedTransaction.
        
        Args:
            tx: VersionedTransaction object (already signed)
            commitment: Commitment level
        
        Returns:
            Simulation result dict with err, logs, accounts, units_consumed, return_data, or None
        """
        try:
            # Simulate VersionedTransaction directly
            result = await self.client.simulate_transaction(
                tx,
                commitment=commitment
            )
            
            # Always return simulation result dict, even on error
            # This allows higher-level code to access logs for debugging
            sim_result = {
                "err": result.value.err,
                "logs": result.value.logs or [],
                "accounts": result.value.accounts,
                "units_consumed": result.value.units_consumed,
                "return_data": result.value.return_data
            }
            
            if result.value.err:
                # Log concise warning with error code (full logs will be printed by caller)
                logger.warning(f"Simulation error: {result.value.err}")
            
            return sim_result
            
        except Exception as e:
            logger.error(f"Error simulating VersionedTransaction: {e}")
            return None
    
    async def send_versioned_transaction(
        self,
        tx: VersionedTransaction,
        skip_preflight: bool = True,
        max_retries: int = 3
    ) -> Optional[str]:
        """
        Send a VersionedTransaction.
        
        Args:
            tx: VersionedTransaction object (already signed)
            skip_preflight: Skip preflight checks (default: True, since we simulate before sending)
            max_retries: Maximum retries on failure
        
        Returns:
            Transaction signature (base58 string) if successful, None otherwise
        """
        try:
            # Transaction is already signed, just send it
            for attempt in range(max_retries):
                try:
                    opts = TxOpts(
                        skip_preflight=skip_preflight,
                        max_retries=0
                    )
                    result = await self.client.send_transaction(tx, opts=opts)
                    
                    if result.value:
                        sig = str(result.value)
                        logger.debug(f"Transaction sent: {sig}")
                        return sig
                    else:
                        logger.warning(f"Transaction send returned no signature (attempt {attempt + 1})")
                        
                except Exception as e:
                    logger.warning(f"Transaction send attempt {attempt + 1} failed: {e}")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(0.5)
                    else:
                        raise
            
            return None
            
        except Exception as e:
            logger.error(f"Error sending VersionedTransaction: {e}")
            return None
    
    async def send_transaction(
        self,
        transaction_base64: str,
        skip_preflight: bool = False,
        max_retries: int = 3
    ) -> Optional[str]:
        """
        Send a transaction.
        
        Args:
            transaction_base64: Base64-encoded transaction (Jupiter API format)
            skip_preflight: Skip preflight checks
            max_retries: Maximum retry attempts
        
        Returns:
            Transaction signature (base58) or None
        """
        try:
            # Decode transaction (Jupiter API returns base64, not base58)
            # Jupiter API returns VersionedTransaction, not legacy Transaction
            tx_bytes = base64.b64decode(transaction_base64)
            
            # Try VersionedTransaction first (Jupiter API format)
            try:
                transaction = VersionedTransaction.from_bytes(tx_bytes)
            except Exception:
                # Fallback to legacy Transaction if VersionedTransaction fails
                transaction = Transaction.from_bytes(tx_bytes)
            
            # Sign if wallet is available
            if self.wallet:
                # Avoid isinstance(transaction, VersionedTransaction) here:
                # in unit tests VersionedTransaction may be patched to a mock (not a type),
                # which makes isinstance() blow up. Instead, try the v0 signing form first.
                try:
                    transaction.sign([self.wallet])  # VersionedTransaction expects a list of signers
                except TypeError:
                    transaction.sign(self.wallet)  # Legacy Transaction signing
            
            # Send with retries
            for attempt in range(max_retries):
                try:
                    opts = TxOpts(
                        skip_preflight=skip_preflight,
                        max_retries=0  # We handle retries ourselves
                    )
                    result = await self.client.send_transaction(transaction, opts=opts)
                    
                    if result.value:
                        sig = str(result.value)
                        logger.info(f"Transaction sent: {sig}")
                        return sig
                    else:
                        logger.warning(f"Transaction send returned no signature (attempt {attempt + 1})")
                        
                except Exception as e:
                    logger.warning(f"Transaction send attempt {attempt + 1} failed: {e}")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(0.5)
                    else:
                        raise
            
            return None
            
        except Exception as e:
            logger.error(f"Error sending transaction: {e}")
            return None
    
    async def confirm_transaction_processed(
        self,
        signature: str,
        timeout: float = 2.0
    ) -> bool:
        """
        Wait for transaction confirmation at 'processed' commitment level (fast, non-final).
        
        Args:
            signature: Transaction signature
            timeout: Timeout in seconds (short, default 2.0)
        
        Returns:
            True if processed, False otherwise
        """
        try:
            result = await self.client.confirm_transaction(
                signature,
                commitment="processed",
                timeout=timeout
            )
            return result.value[0].confirmation_status is not None
        except Exception as e:
            logger.debug(f"Transaction not processed within {timeout}s: {e}")
            return False
    
    async def confirm_transaction(
        self,
        signature: str,
        commitment: str = "confirmed",
        timeout: float = 30.0
    ) -> bool:
        """
        Wait for transaction confirmation.
        
        Args:
            signature: Transaction signature
            commitment: Commitment level
            timeout: Timeout in seconds
        
        Returns:
            True if confirmed, False otherwise
        """
        try:
            result = await self.client.confirm_transaction(
                signature,
                commitment=commitment,
                timeout=timeout
            )
            return result.value[0].confirmation_status is not None
        except Exception as e:
            logger.error(f"Error confirming transaction: {e}")
            return False
    
    async def get_address_lookup_table_accounts(
        self,
        addresses: List[str]
    ) -> List[AddressLookupTableAccount]:
        """
        Get Address Lookup Table (ALT) accounts from Solana RPC.
        
        Args:
            addresses: List of ALT addresses (base58 strings)
        
        Returns:
            List of AddressLookupTableAccount objects
        
        Raises:
            Exception: If any ALT account cannot be loaded
        """
        if not addresses:
            return []
        
        alt_accounts = []
        for alt_address in addresses:
            try:
                pubkey = Pubkey.from_string(alt_address)
                # Get account info for ALT
                account_info = await self.client.get_account_info(pubkey, commitment=Confirmed)
                
                if account_info.value is None:
                    raise ValueError(f"ALT account {alt_address} not found")
                
                # Parse ALT account data
                # ALT account data format: [version, deactivation_slot, last_extended_slot, last_extended_slot_start_index, authority, padding, addresses...]
                alt_account = AddressLookupTableAccount.from_bytes(account_info.value.data)
                alt_accounts.append(alt_account)
                
                logger.debug(f"Loaded ALT account: {alt_address} with {len(alt_account.addresses)} addresses")
            except Exception as e:
                logger.error(f"Failed to load ALT account {alt_address}: {e}")
                raise ValueError(f"Cannot load ALT account {alt_address}: {e}") from e
        
        return alt_accounts
    
    async def get_recent_blockhash(self) -> Optional[Hash]:
        """
        Get recent blockhash for transaction building.
        
        Returns:
            Recent blockhash as Hash object, or None if failed
        """
        try:
            result = await self.client.get_latest_blockhash(commitment=Confirmed)
            if result.value:
                return result.value.blockhash  # Already a Hash object
            return None
        except Exception as e:
            logger.error(f"Error getting recent blockhash: {e}")
            return None
    
    async def close(self):
        """Close RPC client."""
        await self.client.close()
