# Tests for Solana Arbitrage Bot

This directory contains tests for all modules of the Solana arbitrage bot.

## Test Structure

- `test_risk_manager.py` - Tests for risk management module
- `test_arbitrage_finder.py` - Tests for arbitrage opportunity finder
- `test_jupiter_client.py` - Tests for Jupiter API client (with mocks)
- `test_solana_client.py` - Tests for Solana RPC client (with mocks)
- `test_trader.py` - Tests for main trading module (with mocks)
- `test_utils.py` - Tests for utilities
- `conftest.py` - Common pytest fixtures

## Installing Dependencies

```bash
pip install -r requirements.txt
```

## Running Tests

Run all tests:
```bash
pytest tests/
```

Run a specific test file:
```bash
pytest tests/test_risk_manager.py
```

Run a specific test:
```bash
pytest tests/test_risk_manager.py::TestRiskManager::test_can_open_position_success
```

Run with verbose output:
```bash
pytest tests/ -v
```

Run with code coverage:
```bash
pytest tests/ --cov=src --cov-report=html
```

## Test Features

- All tests use mocks for external dependencies (Jupiter API, Solana RPC)
- Tests do not require real network connection
- Async tests use `pytest-asyncio`
- Tests cover main scenarios for each module

## Test Coverage

Tests cover:
- ✅ Initialization of all classes
- ✅ Main methods and functions
- ✅ Error handling
- ✅ Edge cases
- ✅ Input validation
- ✅ Different operation modes (scan, simulate, live)
