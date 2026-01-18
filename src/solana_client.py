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
from solders.address_lookup_table_account import AddressLookupTableAccount, AddressLookupTable
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed, Processed
from solana.rpc.types import TxOpts

logger = logging.getLogger(__name__)


class SolanaClient:
    """Client for Solana RPC operations with failover support."""
    
    def __init__(self, rpc_url: str, wallet_keypair: Optional[Keypair] = None, fallback_rpc_url: Optional[str] = None):
        self.rpc_url_primary = rpc_url
        self.rpc_url_fallback = fallback_rpc_url
        self._active_rpc_url = rpc_url
        self._failover_used = False  # Track if failover has been used (for logging)
        self.client = AsyncClient(rpc_url)
        self.wallet = wallet_keypair
    
    async def _switch_to_fallback(self, reason: str) -> bool:
        """
        Switch to fallback RPC if available.
        
        Args:
            reason: Reason for failover (for logging)
        
        Returns:
            True if switched to fallback, False if no fallback available
        """
        if self.rpc_url_fallback and self._active_rpc_url == self.rpc_url_primary:
            # Log only on first failover
            if not self._failover_used:
                # Extract domain for logging (don't log full URL)
                primary_domain = self.rpc_url_primary.split('//')[1].split('/')[0] if '//' in self.rpc_url_primary else self.rpc_url_primary
                fallback_domain = self.rpc_url_fallback.split('//')[1].split('/')[0] if '//' in self.rpc_url_fallback else self.rpc_url_fallback
                logger.warning(
                    f"RPC failover: PRIMARY ({primary_domain}) -> FALLBACK ({fallback_domain}), reason: {reason}"
                )
                self._failover_used = True
            
            # Close old client and create new one with fallback URL
            try:
                await self.client.close()
            except Exception:
                pass  # Ignore errors when closing old client
            
            self._active_rpc_url = self.rpc_url_fallback
            self.client = AsyncClient(self.rpc_url_fallback)
            return True
        return False
    
    def _is_failover_error(self, error: Exception) -> bool:
        """
        Check if error should trigger failover.
        
        Args:
            error: Exception to check
        
        Returns:
            True if error should trigger failover
        """
        error_str = str(error).lower()
        error_type = type(error).__name__
        
        # Check for rate limit / quota errors
        if '429' in error_str or 'rate limit' in error_str or 'quota' in error_str or 'exceeded' in error_str:
            return True
        
        # Check for timeout / network errors
        if 'timeout' in error_str or 'timed out' in error_str:
            return True
        if error_type in ('ConnectError', 'ConnectTimeout', 'NetworkError', 'TimeoutError'):
            return True
        
        # Check for "unexpected end of file" (common ALT loading issue)
        if 'unexpected end of file' in error_str or 'eof' in error_str:
            return True
        
        # Check for connection errors
        if 'connection' in error_str or 'network' in error_str:
            return True
        
        return False
    
    async def _with_failover(self, coro_func, *args, **kwargs):
        """
        Execute coroutine with failover support.
        
        Args:
            coro_func: Coroutine function to execute
            *args, **kwargs: Arguments to pass to coro_func
        
        Returns:
            Result from coro_func
        
        Raises:
            Exception: If both primary and fallback fail
        """
        # Try primary first
        try:
            return await coro_func(*args, **kwargs)
        except Exception as e:
            # Check if this is a failover-triggering error
            if self._is_failover_error(e) and await self._switch_to_fallback(str(e)):
                # Retry once with fallback
                try:
                    return await coro_func(*args, **kwargs)
                except Exception as e2:
                    # Both failed - raise the original error
                    logger.error(f"Both primary and fallback RPC failed. Last error: {e2}")
                    raise e2 from e
            else:
                # Not a failover error or no fallback - raise original error
                raise
    
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
        Simulate a VersionedTransaction with failover support.
        
        Args:
            tx: VersionedTransaction object (already signed)
            commitment: Commitment level
        
        Returns:
            Simulation result dict with err, logs, accounts, units_consumed, return_data, or None
        """
        async def _simulate():
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
        
        try:
            # Execute with failover support
            return await self._with_failover(_simulate)
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
        Send a VersionedTransaction with failover support.
        
        Args:
            tx: VersionedTransaction object (already signed)
            skip_preflight: Skip preflight checks (default: True, since we simulate before sending)
            max_retries: Maximum retries on failure
        
        Returns:
            Transaction signature (base58 string) if successful, None otherwise
        """
        async def _send():
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
        
        try:
            # Execute with failover support
            return await self._with_failover(_send)
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
        Get Address Lookup Table (ALT) accounts from Solana RPC with failover support.
        
        Args:
            addresses: List of ALT addresses (base58 strings)
        
        Returns:
            List of AddressLookupTableAccount objects
        
        Raises:
            Exception: If any ALT account cannot be loaded (after failover if available)
        """
        if not addresses:
            return []
        
        # Helper function to check if bytes look like ASCII-base64
        B64_CHARS = set(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=\n\r\t ")
        
        def looks_like_ascii_base64(data: bytes) -> bool:
            """Check if bytes contain ASCII-base64 string."""
            s = data.strip()
            if not s:
                return False
            try:
                s.decode("ascii")
            except UnicodeDecodeError:
                return False
            if any(c not in B64_CHARS for c in s):
                return False
            if len(s) % 4 != 0:
                return False
            return True
        
        async def _load_alt_accounts():
            alt_accounts = []
            for alt_address in addresses:
                try:
                    pubkey = Pubkey.from_string(alt_address)
                    # Get account info for ALT with explicit base64 encoding
                    # This ensures RPC returns predictable format: list ["<base64>", "base64"]
                    try:
                        account_info = await self.client.get_account_info(
                            pubkey, 
                            commitment=Confirmed, 
                            encoding="base64"
                        )
                    except TypeError:
                        # Fallback if solana-py version doesn't support encoding parameter
                        account_info = await self.client.get_account_info(pubkey, commitment=Confirmed)
                    
                    if account_info.value is None:
                        raise ValueError(f"ALT account {alt_address} not found")
                    
                    # Normalize account data to bytes
                    # solana-py may return data as bytes, base64 string, or list ["<base64>", "<encoding>"]
                    raw = account_info.value.data
                    data_bytes = None
                    used_base64_fallback_for_bytes = False
                    
                    if isinstance(raw, list) and len(raw) > 0 and isinstance(raw[0], str):
                        # List format: ["<base64>", "<encoding>"]
                        data_bytes = base64.b64decode(raw[0])
                        logger.debug(f"ALT {alt_address}: decoded from list format (encoding: {raw[1] if len(raw) > 1 else 'unknown'})")
                    elif isinstance(raw, str):
                        # Base64 string format
                        data_bytes = base64.b64decode(raw)
                        logger.debug(f"ALT {alt_address}: decoded from base64 string")
                    elif isinstance(raw, (bytes, bytearray)):
                        # Bytes format - try as raw bytes first, fallback to base64-ASCII if needed
                        data_bytes = bytes(raw)
                        try:
                            # Try parsing as raw bytes first
                            table = AddressLookupTable.deserialize(data_bytes)
                            alt_account = AddressLookupTableAccount(pubkey, table.addresses)
                            alt_accounts.append(alt_account)
                            logger.debug(f"Loaded ALT account: {alt_address} with {len(alt_account.addresses)} addresses (raw bytes)")
                            continue
                        except Exception:
                            # If parsing fails, check if bytes contain ASCII-base64
                            if looks_like_ascii_base64(data_bytes):
                                decoded = base64.b64decode(data_bytes.strip(), validate=True)
                                data_bytes = decoded
                                used_base64_fallback_for_bytes = True
                                logger.debug(f"ALT {alt_address}: decoded from bytes containing ASCII-base64")
                            else:
                                # Not base64, re-raise original error
                                raise
                    else:
                        raise TypeError(f"Unexpected ALT data type: {type(raw)} (expected bytes, str, or list)")
                    
                    # Parse ALT account data using AddressLookupTable.deserialize for on-chain account data
                    table = AddressLookupTable.deserialize(data_bytes)
                    alt_account = AddressLookupTableAccount(pubkey, table.addresses)
                    alt_accounts.append(alt_account)
                    
                    logger.debug(f"Loaded ALT account: {alt_address} with {len(alt_account.addresses)} addresses")
                except Exception as e:
                    # Log data type and size for debugging (without full content)
                    raw = None
                    data_type = "unknown"
                    data_len = "unknown"
                    used_base64_fallback_log = False
                    
                    if 'account_info' in locals() and account_info.value and hasattr(account_info.value, 'data'):
                        raw = account_info.value.data
                        data_type = type(raw).__name__
                        if isinstance(raw, (bytes, bytearray)):
                            data_len = len(raw)
                        elif isinstance(raw, str):
                            data_len = len(raw)
                        elif isinstance(raw, list) and len(raw) > 0:
                            data_len = f"list[{len(raw)}]"
                            if isinstance(raw[0], str):
                                data_len += f", first_elem_len={len(raw[0])}"
                    
                    # Check if used_base64_fallback_for_bytes was set in current scope
                    if 'used_base64_fallback_for_bytes' in locals():
                        used_base64_fallback_log = used_base64_fallback_for_bytes
                    
                    logger.error(
                        f"Failed to load ALT account {alt_address}: {e} "
                        f"(data_type={data_type}, data_len={data_len}, "
                        f"used_base64_fallback_for_bytes={used_base64_fallback_log})"
                    )
                    raise ValueError(f"Cannot load ALT account {alt_address}: {e}") from e
            
            return alt_accounts
        
        # Execute with failover support
        return await self._with_failover(_load_alt_accounts)
    
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
