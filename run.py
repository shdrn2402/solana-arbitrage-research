#!/usr/bin/env python3
"""
Simple launcher script for the arbitrage bot.
"""
import sys
from src.main import main
import asyncio

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nBot stopped by user")
        sys.exit(0)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
