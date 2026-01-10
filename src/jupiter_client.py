"""
Jupiter API Client for quotes and swap transactions.
"""
import httpx
import time
from typing import Dict, List, Optional, Any, Tuple, Union
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


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
    
    def __init__(self, api_url: Optional[str] = None, api_key: Optional[str] = None, timeout: float = 10.0):
        """
        Initialize Jupiter API client.
        
        Args:
            api_url: Explicit API URL (overrides fallback). If None, uses fallback list.
            api_key: Jupiter API key for authenticated requests. If provided, will be used in headers.
            timeout: Request timeout in seconds.
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
        
        # Setup HTTP client with headers if API key is provided
        headers = {}
        if api_key:
            # Jupiter API expects API key in x-api-key header, not Authorization
            headers["x-api-key"] = api_key
        
        self.client = httpx.AsyncClient(timeout=timeout, headers=headers)
        self._tried_endpoints = set()  # Track endpoints already tried (DNS/401 failures)
        self._working_endpoint = None  # Cache working endpoint
    
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
        # Use correct endpoint path: /swap/v1/quote (current working Jupiter API endpoint)
        # Remove any trailing /v6 or /v1 from endpoint base URL
        base_url = endpoint.rstrip('/v6').rstrip('/v1').rstrip('/')
        url = f"{base_url}/swap/v1/quote"
        start_time = time.time()
        
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
        
        # Use working endpoint if available, otherwise use explicit api_url
        endpoint = self._working_endpoint or self.api_url
        if not endpoint:
            logger.error("No Jupiter API endpoint available for swap")
            return None
        
        try:
            # Use correct endpoint path: /swap/v1/swap
            base_url = endpoint.rstrip('/v6').rstrip('/v1').rstrip('/')
            swap_url = f"{base_url}/swap/v1/swap"
            response = await self.client.post(swap_url, json=payload)
            response.raise_for_status()
            data = response.json()
            
            last_valid_block_height = data.get("lastValidBlockHeight", 0)
            if last_valid_block_height == 0 or "lastValidBlockHeight" not in data:
                logger.warning("lastValidBlockHeight not found in Jupiter API response, using 0 as default")
            
            swap_response = JupiterSwapResponse(
                swap_transaction=data.get("swapTransaction", ""),
                last_valid_block_height=last_valid_block_height,
                priority_fee_lamports=priority_fee_lamports
            )
            
            logger.debug(f"Swap transaction built: {len(swap_response.swap_transaction)} bytes, last_valid_block_height: {swap_response.last_valid_block_height}")
            return swap_response
            
        except httpx.HTTPStatusError as e:
            logger.error(f"Jupiter swap transaction failed: {e.response.status_code} - {e.response.text}")
            return None
        except Exception as e:
            logger.error(f"Error building swap transaction: {e}")
            return None
    
    async def get_sol_price_usdc(
        self,
        slippage_bps: int = 10,
        return_full_quote: bool = False
    ) -> Optional[Union[float, JupiterQuote]]:
        """
        Get SOL price in USDC from Jupiter API.
        
        Args:
            slippage_bps: Slippage in basis points (default: 10 for accurate price)
            return_full_quote: If True, return full JupiterQuote object; if False, return price as float
        
        Returns:
            If return_full_quote=False: float price (USDC per SOL) or None if failed
            If return_full_quote=True: JupiterQuote object or None if failed
        """
        sol_mint = "So11111111111111111111111111111111111111112"
        usdc_mint = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
        amount = 1_000_000_000  # 1 SOL in lamports
        
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
