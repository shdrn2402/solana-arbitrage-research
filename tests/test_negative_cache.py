"""
Tests for RouteNegativeCache in trader.py.
"""
import pytest
import time
from src.trader import RouteNegativeCache


class TestRouteNegativeCache:
    """Tests for RouteNegativeCache class."""
    
    @pytest.fixture
    def cache(self):
        """Create a RouteNegativeCache instance for testing."""
        return RouteNegativeCache(
            ttl_seconds=600,
            ttl_size_overflow_seconds=300,  # 5 minutes
            ttl_runtime_6024_seconds=600
        )
    
    def test_cache_route_atomic_size_overflow(self, cache):
        """Test caching route with atomic_size_overflow failure type."""
        route_sig = "USDC->SOL->USDC|2|False|Raydium|Orca|Raydium->Orca|"
        
        cache.cache_route(route_sig, failure_type="atomic_size_overflow")
        
        # Check it's cached
        is_cached, cached_type, ttl_remaining = cache.is_cached(
            route_sig, failure_type="atomic_size_overflow"
        )
        assert is_cached is True
        assert cached_type == "atomic_size_overflow"
        assert ttl_remaining is not None
        assert ttl_remaining <= 300  # Should be <= 5 minutes
    
    def test_cache_route_runtime_6024(self, cache):
        """Test caching route with runtime_6024_shared_accounts failure type."""
        route_sig = "USDC->SOL->USDC|2|True|Raydium|Orca|Raydium->Orca|"
        
        cache.cache_route(route_sig, failure_type="runtime_6024_shared_accounts")
        
        # Check it's cached
        is_cached, cached_type, ttl_remaining = cache.is_cached(
            route_sig, failure_type="runtime_6024_shared_accounts"
        )
        assert is_cached is True
        assert cached_type == "runtime_6024_shared_accounts"
        assert ttl_remaining is not None
        assert ttl_remaining <= 600  # Should be <= 10 minutes
    
    def test_cache_route_no_overwrite(self, cache):
        """Test that caching same route twice doesn't overwrite (TTL counted from first detection)."""
        route_sig = "USDC->SOL->USDC|2|False|Raydium|Orca|Raydium->Orca|"
        
        # First cache
        cache.cache_route(route_sig, failure_type="atomic_size_overflow")
        _, _, ttl1 = cache.is_cached(route_sig, failure_type="atomic_size_overflow")
        
        # Small delay
        time.sleep(0.1)
        
        # Try to cache again (should not overwrite)
        cache.cache_route(route_sig, failure_type="atomic_size_overflow")
        _, _, ttl2 = cache.is_cached(route_sig, failure_type="atomic_size_overflow")
        
        # TTL should be approximately the same (not reset)
        assert abs(ttl1 - ttl2) < 0.2  # Allow small timing difference
    
    def test_is_cached_not_cached(self, cache):
        """Test is_cached returns False for uncached route."""
        route_sig = "USDC->SOL->USDC|2|False|Raydium|Orca|Raydium->Orca|"
        
        is_cached, cached_type, ttl_remaining = cache.is_cached(
            route_sig, failure_type="atomic_size_overflow"
        )
        
        assert is_cached is False
        assert cached_type is None
        assert ttl_remaining is None
    
    def test_is_cached_wrong_failure_type(self, cache):
        """Test is_cached returns False when failure type doesn't match."""
        route_sig = "USDC->SOL->USDC|2|False|Raydium|Orca|Raydium->Orca|"
        
        # Cache with atomic_size_overflow
        cache.cache_route(route_sig, failure_type="atomic_size_overflow")
        
        # Check for runtime_6024 (should return False)
        is_cached, cached_type, ttl_remaining = cache.is_cached(
            route_sig, failure_type="runtime_6024_shared_accounts"
        )
        
        assert is_cached is False
        assert cached_type is None
        assert ttl_remaining is None
    
    def test_is_cached_expired(self, cache):
        """Test is_cached returns False for expired entry."""
        # Create cache with very short TTL
        short_cache = RouteNegativeCache(
            ttl_seconds=600,
            ttl_size_overflow_seconds=0.1,  # 100ms
            ttl_runtime_6024_seconds=600
        )
        
        route_sig = "USDC->SOL->USDC|2|False|Raydium|Orca|Raydium->Orca|"
        short_cache.cache_route(route_sig, failure_type="atomic_size_overflow")
        
        # Wait for expiration
        time.sleep(0.15)
        
        # Should be expired
        is_cached, cached_type, ttl_remaining = short_cache.is_cached(
            route_sig, failure_type="atomic_size_overflow"
        )
        
        assert is_cached is False
        assert cached_type is None
        assert ttl_remaining is None
    
    def test_cleanup_expired(self, cache):
        """Test cleanup_expired removes expired entries."""
        # Create cache with very short TTL
        short_cache = RouteNegativeCache(
            ttl_seconds=600,
            ttl_size_overflow_seconds=0.1,  # 100ms
            ttl_runtime_6024_seconds=600
        )
        
        route_sig1 = "USDC->SOL->USDC|2|False|Raydium|Orca|Raydium->Orca|"
        route_sig2 = "SOL->USDC->SOL|2|False|Orca|Raydium|Orca->Raydium|"
        
        short_cache.cache_route(route_sig1, failure_type="atomic_size_overflow")
        short_cache.cache_route(route_sig2, failure_type="atomic_size_overflow")
        
        # Wait for expiration
        time.sleep(0.15)
        
        # Cleanup should remove expired entries
        removed_count = short_cache.cleanup_expired()
        
        assert removed_count == 2
        
        # Both should be gone
        assert not short_cache.is_cached(route_sig1, failure_type="atomic_size_overflow")[0]
        assert not short_cache.is_cached(route_sig2, failure_type="atomic_size_overflow")[0]
    
    def test_route_signature_generation(self, cache):
        """Test _get_route_signature generates correct signature."""
        sig = cache._get_route_signature(
            cycle_mints="USDC->SOL->USDC",
            legs_count=2,
            use_shared_accounts=False,
            dex1="Raydium",
            dex2="Orca",
            direction="Raydium->Orca",
            program_ids_fingerprint=""
        )
        
        assert "USDC->SOL->USDC" in sig
        assert "2" in sig
        assert "False" in sig
        assert "Raydium" in sig
        assert "Orca" in sig
        assert "Raydium->Orca" in sig
