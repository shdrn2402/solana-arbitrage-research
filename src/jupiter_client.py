"""
Jupiter API Client for quotes and swap transactions.
"""
import httpx
import time
import asyncio
from typing import Dict, List, Optional, Any, Tuple, Union
from dataclasses import dataclass
from contextlib import asynccontextmanager
import logging

logger = logging.getLogger(__name__)


class RateLimiter:
    """
    Token bucket rate limiter for Jupiter API requests.
    
    Ensures strict rate limiting: 1 request per second by default.
    Supports burst mode for fast processing of opportunities.
    """
    
    def __init__(self, requests_per_second: float = 1.0):
        """
        Initialize rate limiter.
        
        Args:
            requests_per_second: Maximum requests per second (default: 1.0)
        """
        self.requests_per_second = requests_per_second
        self.min_interval = 1.0 / requests_per_second if requests_per_second > 0 else 0.0
        self._last_request_time = 0.0
        self._burst_mode = False
        self._lock = asyncio.Lock()
    
    async def acquire(self):
        """
        Wait until a request can be made (respecting rate limit).
        
        In burst mode, this returns immediately without waiting.
        """
        async with self._lock:
            if self._burst_mode:
                # Burst mode: no rate limiting
                return
            
            current_time = time.monotonic()
            time_since_last = current_time - self._last_request_time
            
            if time_since_last < self.min_interval:
                wait_time = self.min_interval - time_since_last
                await asyncio.sleep(wait_time)
            
            self._last_request_time = time.monotonic()
    
    @asynccontextmanager
    async def burst(self):
        """
        Context manager for burst mode (temporarily disable rate limiting).
        
        Usage:
            async with limiter.burst():
                # All requests here bypass rate limiting
                await make_requests()
        """
        async with self._lock:
            old_burst = self._burst_mode
            self._burst_mode = True
        try:
            yield
        finally:
            async with self._lock:
                self._burst_mode = old_burst
    
    def pause(self):
        """Temporarily pause rate limiting (enter burst mode)."""
        self._burst_mode = True
    
    def resume(self):
        """Resume rate limiting (exit burst mode)."""
        self._burst_mode = False


@dataclass
class JupiterQuote:
    """Quote response from Jupiter API."""
    input_mint: str
    output_mint: str
    in_amount: int
    out_amount: int
    price_impact_pct: float
    route_plan: List[Dict[str, Any]]
    context_slot: Optional[int] = None
    time_taken: Optional[float] = None


@dataclass
class JupiterSwapResponse:
    """Swap transaction response from Jupiter API."""
    swap_transaction: str
    last_valid_block_height: int
    priority_fee_lamports: Optional[int] = None


@dataclass
class SwapAccountMeta:
    """Account metadata for swap instruction."""
    pubkey: str
    is_signer: bool
    is_writable: bool


@dataclass
class SwapInstruction:
    """Single swap instruction from Jupiter API."""
    program_id: str
    accounts: List[SwapAccountMeta]
    data: str


@dataclass
class JupiterSwapInstructionsResponse:
    """Swap instructions response from Jupiter API."""
    setup_instructions: List[SwapInstruction]
    swap_instruction: SwapInstruction
    cleanup_instruction: Optional[SwapInstruction]
    address_lookup_tables: List[str]  # ALT addresses
    last_valid_block_height: int
    priority_fee_lamports: Optional[int] = None


class JupiterClient:
    """Client for Jupiter Aggregator API with deterministic fallback."""
    
    # Public endpoints (no authentication required) - ordered by preference
    PUBLIC_ENDPOINTS = [
        "https://quote-api.jup.ag/v6",
        # Add alternative public endpoints here if available
    ]
    
    # Authenticated endpoints (require API key) - ordered by preference
    AUTH_ENDPOINTS = [
        "https://api.jup.ag",
        # Add alternative authenticated endpoints here if available
    ]
    
    def __init__(
        self,
        api_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: float = 10.0,
        requests_per_second: float = 1.0,
        max_retries_on_429: int = 3,
        backoff_base_seconds: float = 1.0,
        backoff_max_seconds: float = 30.0
    ):
        """
        Initialize Jupiter API client.
        
        Args:
            api_url: Explicit API URL (overrides fallback). If None, uses fallback list.
            api_key: Jupiter API key for authenticated requests. If provided, will be used in headers.
            timeout: Request timeout in seconds.
            requests_per_second: Rate limit for Jupiter API requests (default: 1.0 req/sec)
            max_retries_on_429: Maximum retries on 429 rate limit error (default: 3)
            backoff_base_seconds: Base backoff time for 429 retries (default: 1.0)
            backoff_max_seconds: Maximum backoff time for 429 retries (default: 30.0)
        """
        if api_url:
            # Explicit URL provided - use it directly (no fallback)
            self.api_url = api_url.rstrip('/')
            self.fallback_endpoints = []
        else:
            # Use fallback mechanism - will try endpoints in order
            self.api_url = None  # Will be set on first successful connection
            # If API key provided, use authenticated endpoints; otherwise public endpoints
            if api_key:
                self.fallback_endpoints = self.AUTH_ENDPOINTS.copy()
            else:
                self.fallback_endpoints = self.PUBLIC_ENDPOINTS.copy()
        
        self.api_key = api_key
        self.timeout = timeout
        
        # Rate limiting
        self.rate_limiter = RateLimiter(requests_per_second=requests_per_second)
        self.max_retries_on_429 = max_retries_on_429
        self.backoff_base_seconds = backoff_base_seconds
        self.backoff_max_seconds = backoff_max_seconds
        
        # Setup HTTP client with headers if API key is provided
        headers = {}
        if api_key:
            # Jupiter API expects API key in x-api-key header, not Authorization
            headers["x-api-key"] = api_key
        
        self.client = httpx.AsyncClient(timeout=timeout, headers=headers)
        self._tried_endpoints = set()  # Track endpoints already tried (DNS/401 failures)
        self._working_endpoint = None  # Cache working endpoint
        self._working_swap_endpoint = None  # Separate cache for swap instructions endpoint
    
    async def _try_get_quote_from_endpoint(
        self,
        endpoint: str,
        params: Dict[str, Any]
    ) -> tuple[Optional[JupiterQuote], Optional[str]]:
        """
        Try to get quote from a specific endpoint.
        
        Returns:
            (quote, error_type) where error_type is:
            - None: success
            - 'dns': DNS/connection error (can try next endpoint)
            - '401': Unauthorized (endpoint requires auth, don't retry)
            - 'other': Other error (don't retry)
        """
        # Apply rate limiting (unless in burst mode)
        await self.rate_limiter.acquire()
        
        # Use correct endpoint path: /swap/v1/quote (current working Jupiter API endpoint)
        # Remove any trailing /v6 or /v1 from endpoint base URL
        base_url = endpoint.rstrip('/v6').rstrip('/v1').rstrip('/')
        url = f"{base_url}/swap/v1/quote"
        start_time = time.time()
        
        # Retry on 429 with exponential backoff
        for attempt in range(self.max_retries_on_429 + 1):
            try:
                response = await self.client.get(url, params=params)
                response.raise_for_status()
                data = response.json()
                
                time_taken = time.time() - start_time
                
                quote = JupiterQuote(
                    input_mint=data.get("inputMint", params["inputMint"]),
                    output_mint=data.get("outputMint", params["outputMint"]),
                    in_amount=int(data.get("inAmount", params["amount"])),
                    out_amount=int(data.get("outAmount", 0)),
                    price_impact_pct=float(data.get("priceImpactPct", 0)),
                    route_plan=data.get("routePlan", []),
                    context_slot=data.get("contextSlot"),
                    time_taken=time_taken
                )
                
                # Cache working endpoint
                self._working_endpoint = endpoint
                logger.debug(f"Quote from {endpoint}: {params['inputMint'][:8]}... -> {params['outputMint'][:8]}... "
                            f"in={quote.in_amount} out={quote.out_amount} "
                            f"impact={quote.price_impact_pct:.2f}%")
                
                return quote, None
                
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    # Rate limit exceeded - retry with backoff
                    if attempt < self.max_retries_on_429:
                        # Check for Retry-After header
                        retry_after = e.response.headers.get("Retry-After")
                        if retry_after:
                            try:
                                wait_time = float(retry_after)
                            except ValueError:
                                wait_time = self.backoff_base_seconds * (2 ** attempt)
                        else:
                            # Exponential backoff
                            wait_time = min(
                                self.backoff_base_seconds * (2 ** attempt),
                                self.backoff_max_seconds
                            )
                        
                        logger.warning(
                            f"Rate limit exceeded (429) from {endpoint}, "
                            f"retrying in {wait_time:.1f}s (attempt {attempt + 1}/{self.max_retries_on_429})"
                        )
                        await asyncio.sleep(wait_time)
                        continue
                    else:
                        # Max retries reached - don't mark endpoint as dead (429 is temporary)
                        logger.error(f"Rate limit exceeded (429) from {endpoint} after {self.max_retries_on_429} retries")
                        return None, '429'
                
                if e.response.status_code == 401:
                    # 401 = endpoint requires auth
                    if self.api_key:
                        # We have API key but still got 401 - key might be invalid
                        self._tried_endpoints.add(endpoint)
                        logger.error(f"Endpoint {endpoint} returned 401 even with API key. Key may be invalid.")
                        return None, '401'
                    else:
                        # No API key - mark as tried and don't retry (for scan mode without key)
                        self._tried_endpoints.add(endpoint)
                        logger.warning(f"Endpoint {endpoint} requires authentication (401). No API key provided.")
                        return None, '401'
                elif e.response.status_code == 404:
                    # 404 = route not found (no route available for this pair)
                    # This is a valid API response, not a transport error - don't mark endpoint as failed
                    logger.debug(f"Route not found for {params.get('inputMint', '')[:8]}... -> {params.get('outputMint', '')[:8]}... (404)")
                    return None, '404'
                else:
                    # Other HTTP errors - don't retry this endpoint
                    self._tried_endpoints.add(endpoint)
                    logger.warning(f"Jupiter quote failed from {endpoint}: {e.response.status_code} - {e.response.text}")
                    return None, 'other'
                    
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.NetworkError) as e:
                # DNS/network error - can try next endpoint
                logger.debug(f"Connection error for {endpoint} (DNS/network): {e}. Will try next endpoint if available.")
                return None, 'dns'
                
            except Exception as e:
                # Unexpected error - don't retry
                self._tried_endpoints.add(endpoint)
                logger.error(f"Unexpected error getting quote from {endpoint}: {e}")
                return None, 'other'
    
    async def get_quote(
        self,
        input_mint: str,
        output_mint: str,
        amount: int,
        slippage_bps: int = 50,
        only_direct_routes: bool = False,
        as_legacy: bool = False
    ) -> Optional[JupiterQuote]:
        """
        Get a quote for swapping tokens with deterministic fallback.
        
        Tries endpoints in order:
        1. Working endpoint (if previously found)
        2. Explicit api_url (if set)
        3. Fallback endpoints (if available)
        
        Stops on first success or when all endpoints exhausted.
        Does not retry endpoints that returned 401 (requires auth).
        
        Args:
            input_mint: Input token mint address
            output_mint: Output token mint address
            amount: Amount in smallest unit (lamports for SOL)
            slippage_bps: Slippage in basis points (1 bps = 0.01%)
            only_direct_routes: Only return direct routes
            as_legacy: Return legacy format
        
        Returns:
            JupiterQuote or None if all endpoints failed
        """
        params = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": str(amount),
            "slippageBps": slippage_bps,
            "onlyDirectRoutes": str(only_direct_routes).lower(),
            "asLegacyTransaction": str(as_legacy).lower()
        }
        
        # Build list of endpoints to try (in order)
        endpoints_to_try = []
        
        # 1. Try working endpoint first (if we found one before)
        if self._working_endpoint:
            endpoints_to_try.append(self._working_endpoint)
        
        # 2. Try explicit api_url (if set, not using fallback)
        if self.api_url and self.api_url not in endpoints_to_try:
            endpoints_to_try.append(self.api_url)
        
        # 3. Try fallback endpoints (excluding already tried and already in list)
        for endpoint in self.fallback_endpoints:
            if endpoint not in endpoints_to_try and endpoint not in self._tried_endpoints:
                endpoints_to_try.append(endpoint)
        
        # Try each endpoint in order
        for endpoint in endpoints_to_try:
            quote, error_type = await self._try_get_quote_from_endpoint(endpoint, params)
            
            if quote is not None:
                return quote
            
            # If DNS error, continue to next endpoint
            # If 404 (route not found), continue to next endpoint (valid API response, no route available)
            # If 401 or other error, endpoint is marked as tried and won't be retried
            if error_type == 'dns' or error_type == '404':
                continue
            # For 401 and other errors, endpoint is already marked as tried in _try_get_quote_from_endpoint
        
        # All endpoints exhausted
        if not endpoints_to_try:
            logger.error("No Jupiter API endpoints available to try")
        else:
            logger.warning(f"All Jupiter quote endpoints exhausted. "
                          f"Tried: {len(endpoints_to_try)} endpoints. "
                          f"Either no routes found for requested pairs, or API temporarily unavailable.")
        
        return None
    
    async def get_swap_transaction(
        self,
        quote: JupiterQuote,
        user_public_key: str,
        priority_fee_lamports: int = 0,
        wrap_unwrap_sol: bool = True,
        dynamic_compute_unit_limit: bool = True,
        dynamic_slippage: Optional[Dict[str, Any]] = None,
        slippage_bps: int = 50
    ) -> Optional[JupiterSwapResponse]:
        """
        Get swap transaction from Jupiter API.
        
        Args:
            quote: JupiterQuote object
            user_public_key: User's public key (base58)
            priority_fee_lamports: Priority fee in lamports
            wrap_unwrap_sol: Auto wrap/unwrap SOL
            dynamic_compute_unit_limit: Use dynamic compute unit limit
            dynamic_slippage: Dynamic slippage configuration
            slippage_bps: Slippage in basis points (default: 50)
        
        Returns:
            JupiterSwapResponse or None if swap transaction build fails
        """
        # Build payload according to Jupiter API v6 format
        payload = {
            "quoteResponse": {
                "inputMint": quote.input_mint,
                "inAmount": str(quote.in_amount),
                "outputMint": quote.output_mint,
                "outAmount": str(quote.out_amount),
                "otherAmountThreshold": str(quote.out_amount),
                "swapMode": "ExactIn",
                "slippageBps": slippage_bps,
                "priceImpactPct": quote.price_impact_pct,
                "routePlan": quote.route_plan
            },
            "userPublicKey": user_public_key,
            "wrapUnwrapSOL": wrap_unwrap_sol,
            "dynamicComputeUnitLimit": dynamic_compute_unit_limit
        }
        
        # Add priority fee if specified
        if priority_fee_lamports > 0:
            payload["priorityLevelWithMaxLamports"] = {
                "maxLamports": priority_fee_lamports
            }
        
        # Add dynamic slippage if specified
        if dynamic_slippage:
            payload["dynamicSlippage"] = dynamic_slippage
        
        # Apply rate limiting (unless in burst mode)
        await self.rate_limiter.acquire()
        
        # Use working endpoint if available, otherwise use explicit api_url
        endpoint = self._working_endpoint or self.api_url
        if not endpoint:
            logger.error("No Jupiter API endpoint available for swap")
            return None
        
        # Retry on 429 with exponential backoff
        for attempt in range(self.max_retries_on_429 + 1):
            try:
                # Use correct endpoint path: /swap/v1/swap
                base_url = endpoint.rstrip('/v6').rstrip('/v1').rstrip('/')
                swap_url = f"{base_url}/swap/v1/swap"
                response = await self.client.post(swap_url, json=payload)
                response.raise_for_status()
                data = response.json()
                
                last_valid_block_height = data.get("lastValidBlockHeight", 0)
                if "lastValidBlockHeight" not in data:
                    logger.debug("lastValidBlockHeight not found in Jupiter API response, using 0 as default")
                
                swap_response = JupiterSwapResponse(
                    swap_transaction=data.get("swapTransaction", ""),
                    last_valid_block_height=last_valid_block_height,
                    priority_fee_lamports=priority_fee_lamports
                )
                
                logger.debug(f"Swap transaction built: {len(swap_response.swap_transaction)} bytes, last_valid_block_height: {swap_response.last_valid_block_height}")
                return swap_response
                
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    # Rate limit exceeded - retry with backoff
                    if attempt < self.max_retries_on_429:
                        # Check for Retry-After header
                        retry_after = e.response.headers.get("Retry-After")
                        if retry_after:
                            try:
                                wait_time = float(retry_after)
                            except ValueError:
                                wait_time = self.backoff_base_seconds * (2 ** attempt)
                        else:
                            # Exponential backoff
                            wait_time = min(
                                self.backoff_base_seconds * (2 ** attempt),
                                self.backoff_max_seconds
                            )
                        
                        logger.warning(
                            f"Rate limit exceeded (429) for swap from {endpoint}, "
                            f"retrying in {wait_time:.1f}s (attempt {attempt + 1}/{self.max_retries_on_429})"
                        )
                        await asyncio.sleep(wait_time)
                        continue
                    else:
                        # Max retries reached
                        logger.error(f"Rate limit exceeded (429) for swap from {endpoint} after {self.max_retries_on_429} retries")
                        return None
                
                logger.error(f"Jupiter swap transaction failed: {e.response.status_code} - {e.response.text}")
                return None
            except Exception as e:
                logger.error(f"Error building swap transaction: {e}")
                return None
    
    def _parse_accounts(self, accounts_data: Union[List[str], List[Dict[str, Any]]]) -> List[SwapAccountMeta]:
        """
        Parse accounts from Jupiter API response.
        
        Supports two formats:
        1) List of strings: ["pubkey1", "pubkey2", ...] - raises NotImplementedError
        2) List of objects: [{"pubkey": "...", "isSigner": bool, "isWritable": bool}, ...] - expected format
        
        Args:
            accounts_data: Accounts data from API (list of strings or list of dicts)
        
        Returns:
            List of SwapAccountMeta objects
        
        Raises:
            NotImplementedError: If accounts are in string format (missing meta flags)
        """
        if not accounts_data:
            return []
        
        # Check format: if first element is a string, it's the old format
        if isinstance(accounts_data[0], str):
            raise NotImplementedError(
                "Accounts are in string format (missing isSigner/isWritable flags). "
                "Cannot build Solana Instruction objects. "
                "Jupiter API must return accounts with metadata."
            )
        
        # Parse list of objects with metadata
        parsed_accounts = []
        for account_data in accounts_data:
            if isinstance(account_data, dict):
                parsed_accounts.append(SwapAccountMeta(
                    pubkey=account_data.get("pubkey", ""),
                    is_signer=account_data.get("isSigner", False),
                    is_writable=account_data.get("isWritable", False)
                ))
            else:
                # Unexpected format
                raise ValueError(f"Unexpected account format: {type(account_data)}")
        
        return parsed_accounts
    
    def _get_swap_endpoints_to_try(self) -> List[str]:
        """
        Get list of endpoints to try for swap instructions, in order of preference.
        
        Returns:
            List of endpoint URLs to try
        """
        endpoints_to_try = []
        
        # 1) Try working swap endpoint if cached
        if self._working_swap_endpoint:
            endpoints_to_try.append(self._working_swap_endpoint)
        
        # 2) Try working endpoint (from quote endpoint)
        if self._working_endpoint and self._working_endpoint not in endpoints_to_try:
            endpoints_to_try.append(self._working_endpoint)
        
        # 3) Try explicit api_url if set and not already in list
        if self.api_url and self.api_url not in endpoints_to_try:
            endpoints_to_try.append(self.api_url)
        
        # 4) Try fallback endpoints (swap-capable)
        # Always try both AUTH and PUBLIC endpoints for swap-instructions
        # Different endpoints may support different paths (e.g., /swap-instructions vs /swap/v1/swap)
        swap_capable_endpoints = []
        if self.api_key:
            # If we have API key, prefer AUTH first, then PUBLIC
            swap_capable_endpoints.extend(self.AUTH_ENDPOINTS)
            swap_capable_endpoints.extend(self.PUBLIC_ENDPOINTS)
        else:
            # Try public endpoints first, then authenticated (some work without key)
            swap_capable_endpoints.extend(self.PUBLIC_ENDPOINTS)
            swap_capable_endpoints.extend(self.AUTH_ENDPOINTS)
        
        for endpoint in swap_capable_endpoints:
            if endpoint not in endpoints_to_try:
                endpoints_to_try.append(endpoint)
        
        return endpoints_to_try
    
    async def get_swap_instructions(
        self,
        quote: JupiterQuote,
        user_public_key: str,
        priority_fee_lamports: int = 0,
        wrap_unwrap_sol: bool = True,
        dynamic_compute_unit_limit: bool = True,
        dynamic_slippage: Optional[Dict[str, Any]] = None,
        slippage_bps: int = 50
    ) -> Optional[JupiterSwapInstructionsResponse]:
        """
        Get swap instructions from Jupiter API (for building atomic VersionedTransaction).
        
        This method returns structured instructions instead of a pre-built transaction,
        allowing for atomic multi-leg transaction assembly.
        
        Args:
            quote: JupiterQuote object
            user_public_key: User's public key (base58)
            priority_fee_lamports: Priority fee in lamports
            wrap_unwrap_sol: Auto wrap/unwrap SOL
            dynamic_compute_unit_limit: Use dynamic compute unit limit
            dynamic_slippage: Dynamic slippage configuration
            slippage_bps: Slippage in basis points (default: 50)
        
        Returns:
            JupiterSwapInstructionsResponse with instructions and ALT addresses, or None if failed
        
        Raises:
            NotImplementedError: If Jupiter API doesn't support instructions-only endpoint
        """
        # Build payload similar to get_swap_transaction, but request instructions only
        payload = {
            "quoteResponse": {
                "inputMint": quote.input_mint,
                "inAmount": str(quote.in_amount),
                "outputMint": quote.output_mint,
                "outAmount": str(quote.out_amount),
                "otherAmountThreshold": str(quote.out_amount),
                "swapMode": "ExactIn",
                "slippageBps": slippage_bps,
                "priceImpactPct": quote.price_impact_pct,
                "routePlan": quote.route_plan
            },
            "userPublicKey": user_public_key,
            "wrapUnwrapSOL": wrap_unwrap_sol,
            "dynamicComputeUnitLimit": dynamic_compute_unit_limit,
            # Request instructions instead of full transaction
            "onlyLegs": True,  # This parameter requests instructions only
            # Disable shared accounts for 2-swap cross-AMM (hard requirement)
            "useSharedAccounts": False
        }
        
        # For 2-swap cross-AMM: useSharedAccounts is always False (hard requirement)
        # Do not retry with useSharedAccounts=True even on 400 errors
        allow_shared_accounts_retry = False
        
        # Add priority fee if specified
        if priority_fee_lamports > 0:
            payload["priorityLevelWithMaxLamports"] = {
                "maxLamports": priority_fee_lamports
            }
        
        # Add dynamic slippage if specified
        if dynamic_slippage:
            payload["dynamicSlippage"] = dynamic_slippage
        
        # Get list of endpoints to try (always include both AUTH and PUBLIC)
        endpoints_to_try = self._get_swap_endpoints_to_try()
        if not endpoints_to_try:
            logger.error("No Jupiter API endpoint available for swap instructions")
            return None
        
        # Candidate paths to try (in order of preference)
        candidate_paths = [
            "/swap/v1/swap-instructions",
            "/swap-instructions",
            "/swap/v1/swap"  # Fallback, but don't treat swapTransaction-only as failure
        ]
        
        # Track errors for summary
        error_summary = {
            'endpoints_tried': 0,
            'paths_tried': 0,
            'http_codes': {},
            'swap_transaction_only': 0,
            'network_errors': 0,
            'other_errors': 0
        }
        
        # Try each endpoint in order
        for endpoint in endpoints_to_try:
            error_summary['endpoints_tried'] += 1
            base_url = endpoint.rstrip('/v6').rstrip('/v1').rstrip('/')
            
            # Try each path for this endpoint
            for path in candidate_paths:
                error_summary['paths_tried'] += 1
                swap_url = f"{base_url}{path}"
                
                # Retry on 429 with exponential backoff
                # For 2-swap: useSharedAccounts is always False (no retry with True)
                use_shared_accounts = allow_shared_accounts_retry  # False for 2-swap
                
                for attempt in range(self.max_retries_on_429 + 1):
                    try:
                        # Apply rate limiting before each HTTP POST
                        await self.rate_limiter.acquire()
                        
                        # Use payload as-is (useSharedAccounts already set to False for 2-swap)
                        current_payload = payload.copy()
                        # For 2-swap: never add useSharedAccounts=True (hard requirement)
                        if not use_shared_accounts and "useSharedAccounts" in current_payload:
                            # Keep False (already set)
                            pass
                        elif not use_shared_accounts:
                            # Remove if somehow present (shouldn't happen for 2-swap)
                            current_payload.pop("useSharedAccounts", None)
                        
                        response = await self.client.post(swap_url, json=current_payload)
                        response.raise_for_status()
                        data = response.json()
                        
                        # Check if response contains swapInstruction (success case)
                        if "swapInstruction" in data:
                            # Success! Parse and return instructions
                    
                            # Parse instructions from response
                            # Expected format:
                            # {
                            #   "setupInstructions": [...],
                            #   "swapInstruction": {...},
                            #   "cleanupInstruction": {...},
                            #   "addressLookupTables": [...],
                            #   "lastValidBlockHeight": ...
                            # }
                            
                            last_valid_block_height = data.get("lastValidBlockHeight", 0)
                            if "lastValidBlockHeight" not in data:
                                logger.debug("lastValidBlockHeight not found in Jupiter API response, using 0 as default")
                            
                            # Parse setup instructions
                            setup_instructions = []
                            if "setupInstructions" in data:
                                for instr_data in data["setupInstructions"]:
                                    accounts = self._parse_accounts(instr_data.get("accounts", []))
                                    setup_instructions.append(SwapInstruction(
                                        program_id=instr_data.get("programId", ""),
                                        accounts=accounts,
                                        data=instr_data.get("data", "")
                                    ))
                            
                            # Parse swap instruction
                            swap_instr_data = data["swapInstruction"]
                            swap_accounts = self._parse_accounts(swap_instr_data.get("accounts", []))
                            swap_instruction = SwapInstruction(
                                program_id=swap_instr_data.get("programId", ""),
                                accounts=swap_accounts,
                                data=swap_instr_data.get("data", "")
                            )
                            
                            # Parse cleanup instruction (optional)
                            cleanup_instruction = None
                            if "cleanupInstruction" in data and data["cleanupInstruction"]:
                                cleanup_instr_data = data["cleanupInstruction"]
                                cleanup_accounts = self._parse_accounts(cleanup_instr_data.get("accounts", []))
                                cleanup_instruction = SwapInstruction(
                                    program_id=cleanup_instr_data.get("programId", ""),
                                    accounts=cleanup_accounts,
                                    data=cleanup_instr_data.get("data", "")
                                )
                            
                            # Parse address lookup tables (robust extraction from multiple possible keys)
                            raw_alts = (
                                data.get("addressLookupTables")
                                or data.get("addressLookupTableAddresses")
                                or []
                            )
                            address_lookup_tables: List[str] = []
                            if isinstance(raw_alts, list):
                                for x in raw_alts:
                                    if isinstance(x, str):
                                        address_lookup_tables.append(x)
                                    elif isinstance(x, dict):
                                        # Support various dict formats: {"accountKey": "..."}, {"address": "..."}, {"key": "..."}
                                        for key in ("accountKey", "address", "key"):
                                            if isinstance(x.get(key), str):
                                                address_lookup_tables.append(x[key])
                                                break
                            
                            # Deduplicate while preserving order
                            seen = set()
                            address_lookup_tables = [
                                a for a in address_lookup_tables
                                if not (a in seen or seen.add(a))
                            ]
                            
                            instructions_response = JupiterSwapInstructionsResponse(
                                setup_instructions=setup_instructions,
                                swap_instruction=swap_instruction,
                                cleanup_instruction=cleanup_instruction,
                                address_lookup_tables=address_lookup_tables,
                                last_valid_block_height=last_valid_block_height,
                                priority_fee_lamports=priority_fee_lamports
                            )
                            
                            # Cache successful endpoint + path
                            self._working_swap_endpoint = endpoint
                            
                            logger.debug(
                                f"Swap instructions OK via {swap_url}: "
                                f"{len(setup_instructions)} setup, 1 swap, "
                                f"{1 if cleanup_instruction else 0} cleanup, "
                                f"{len(address_lookup_tables)} ALTs"
                            )
                            return instructions_response
                        
                        # Check if response contains only swapTransaction (no swapInstruction)
                        elif "swapTransaction" in data and "swapInstruction" not in data:
                            # This path doesn't support instructions-only mode - try next path
                            error_summary['swap_transaction_only'] += 1
                            logger.debug(
                                f"Path {path} on {endpoint} returned swapTransaction-only, trying next path"
                            )
                            break  # Break out of retry loop, try next path
                        
                        else:
                            # Response doesn't contain swapInstruction or swapTransaction
                            error_summary['other_errors'] += 1
                            logger.debug(
                                f"Unexpected response from {swap_url}: missing both swapInstruction and swapTransaction"
                            )
                            break  # Break out of retry loop, try next path
                            
                    except httpx.HTTPStatusError as e:
                        if e.response.status_code == 401:
                            # Unauthorized - mark endpoint as tried and continue to next
                            error_summary['http_codes'][401] = error_summary['http_codes'].get(401, 0) + 1
                            logger.debug(f"Path {path} on {endpoint} returned 401 (unauthorized), trying next path")
                            self._tried_endpoints.add(endpoint)
                            break  # Break out of retry loop, try next path
                        elif e.response.status_code == 429:
                            # Rate limit exceeded - retry with backoff
                            if attempt < self.max_retries_on_429:
                                # Check for Retry-After header
                                retry_after = e.response.headers.get("Retry-After")
                                if retry_after:
                                    try:
                                        wait_time = float(retry_after)
                                    except ValueError:
                                        wait_time = self.backoff_base_seconds * (2 ** attempt)
                                else:
                                    # Exponential backoff
                                    wait_time = min(
                                        self.backoff_base_seconds * (2 ** attempt),
                                        self.backoff_max_seconds
                                    )
                                
                                logger.warning(
                                    f"Rate limit exceeded (429) for swap instructions from {swap_url}, "
                                    f"retrying in {wait_time:.1f}s (attempt {attempt + 1}/{self.max_retries_on_429})"
                                )
                                await asyncio.sleep(wait_time)
                                continue
                            else:
                                # Max retries reached - try next path
                                logger.error(f"Rate limit exceeded (429) for swap instructions from {swap_url} after {self.max_retries_on_429} retries, trying next path")
                                break  # Break out of retry loop, try next path
                        
                        # Check if endpoint doesn't support instructions-only mode (400 with specific error)
                        elif e.response.status_code == 400:
                            error_text = e.response.text.lower()
                            # For 2-swap: never retry with useSharedAccounts=True (hard requirement)
                            # If 400 is about useSharedAccounts and we're already using False, try next path
                            if not allow_shared_accounts_retry and ("sharedaccounts" in error_text or "useSharedAccounts" in error_text):
                                # For 2-swap: this shouldn't happen (we always use False)
                                # But if it does, try next path (don't retry with True)
                                logger.debug(f"Path {path} on {endpoint} returned 400 about useSharedAccounts (2-swap always uses False), trying next path")
                                break  # Try next path
                            elif use_shared_accounts and ("sharedaccounts" in error_text or "useSharedAccounts" in error_text):
                                # Legacy path: retry without useSharedAccounts (only if allow_shared_accounts_retry=True)
                                use_shared_accounts = False
                                logger.debug(f"Path {path} on {endpoint} doesn't support useSharedAccounts, retrying without it")
                                continue  # Retry with same path but without useSharedAccounts
                            elif "onlylegs" in error_text or "instructions" in error_text:
                                error_summary['swap_transaction_only'] += 1
                                logger.debug(
                                    f"Path {path} on {endpoint} does not support instructions-only: {e.response.status_code} - {e.response.text}. Trying next path."
                                )
                                break  # Break out of retry loop, try next path
                        
                        # Track HTTP status codes and try next path
                        status_code = e.response.status_code
                        error_summary['http_codes'][status_code] = error_summary['http_codes'].get(status_code, 0) + 1
                        logger.debug(f"Path {path} on {endpoint} returned {e.response.status_code}, trying next path")
                        break  # Break out of retry loop, try next path
                    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.NetworkError) as e:
                        # Network/parse errors - try next path
                        error_summary['network_errors'] += 1
                        logger.debug(f"Network error with {swap_url}: {e}, trying next path")
                        break  # Break out of retry loop, try next path
                    except Exception as e:
                        # Other errors - try next path
                        error_summary['other_errors'] += 1
                        logger.debug(f"Error with {swap_url}: {e}, trying next path")
                        break  # Break out of retry loop, try next path
            
            # If we've tried all paths for this endpoint without success, continue to next endpoint
        
        # All endpoints/paths failed - log summary
        error_parts = []
        if error_summary['endpoints_tried'] > 0:
            error_parts.append(f"tried {error_summary['endpoints_tried']} endpoint(s)")
        if error_summary['paths_tried'] > 0:
            error_parts.append(f"tried {error_summary['paths_tried']} path(s)")
        if error_summary['http_codes']:
            codes_str = ', '.join(f"{code}({count})" for code, count in error_summary['http_codes'].items())
            error_parts.append(f"HTTP codes: {codes_str}")
        if error_summary['swap_transaction_only'] > 0:
            error_parts.append(f"{error_summary['swap_transaction_only']} swapTransaction-only response(s)")
        if error_summary['network_errors'] > 0:
            error_parts.append(f"{error_summary['network_errors']} network error(s)")
        if error_summary['other_errors'] > 0:
            error_parts.append(f"{error_summary['other_errors']} other error(s)")
        
        summary_msg = " | ".join(error_parts) if error_parts else "unknown reasons"
        logger.error(f"All Jupiter API endpoints/paths failed for swap instructions: {summary_msg}")
        return None
    
    async def get_sol_price_usdc(
        self,
        slippage_bps: int = 10,
        return_full_quote: bool = False,
        amount_sol: float = 1.0
    ) -> Optional[Union[float, JupiterQuote]]:
        """
        Get SOL price in USDC from Jupiter API.
        
        Args:
            slippage_bps: Slippage in basis points (default: 10 for accurate price)
            return_full_quote: If True, return full JupiterQuote object; if False, return price as float
            amount_sol: Amount in SOL to use for quote (default: 1.0 SOL)
        
        Returns:
            If return_full_quote=False: float price (USDC per SOL) or None if failed
            If return_full_quote=True: JupiterQuote object or None if failed
        """
        # NOTE: Hardcoded addresses for SOL and USDC are acceptable here
        # as this is a specialized method for fetching SOL price only.
        # These are well-known constants for native SOL and USDC.
        # For arbitrage cycles, addresses come from config.json.
        sol_mint = "So11111111111111111111111111111111111111112"
        usdc_mint = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
        amount = int(amount_sol * 1e9)  # Convert SOL to lamports
        
        logger.debug(f"Fetching SOL price: {amount / 1e9} SOL → USDC (slippage_bps={slippage_bps})")
        
        quote = await self.get_quote(
            input_mint=sol_mint,
            output_mint=usdc_mint,
            amount=amount,
            slippage_bps=slippage_bps,
            only_direct_routes=False
        )
        
        if quote:
            if return_full_quote:
                logger.debug(f"SOL price quote received: {quote.out_amount / 1e6:.2f} USDC")
                return quote
            else:
                # Return price as float (USDC per SOL, USDC has 6 decimals)
                price = quote.out_amount / 1e6
                logger.debug(f"SOL price from Jupiter API: ${price:.2f} USDC")
                return price
        else:
            logger.debug("Failed to get SOL price from Jupiter API")
            return None
    
    async def get_tokens(self) -> Optional[List[Dict[str, Any]]]:
        """Get list of available tokens from Jupiter."""
        # Use working endpoint if available, otherwise use explicit api_url
        endpoint = self._working_endpoint or self.api_url
        if not endpoint:
            logger.error("No Jupiter API endpoint available for tokens")
            return None
        
        try:
            # Use correct endpoint path: /swap/v1/tokens
            base_url = endpoint.rstrip('/v6').rstrip('/v1').rstrip('/')
            tokens_url = f"{base_url}/swap/v1/tokens"
            response = await self.client.get(tokens_url)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Error getting tokens: {e}")
            return None
    
    async def close(self):
        """Close HTTP client."""
        await self.client.aclose()
