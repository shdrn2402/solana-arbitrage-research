#!/usr/bin/env python3
"""
Simple launcher script for the arbitrage bot.
"""
import argparse
import sys
from src.main import main
import asyncio

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Solana Arbitrage Bot')
    parser.add_argument(
        'mode',
        nargs='?',
        default='scan',
        choices=['scan', 'simulate', 'live'],
        help='Operation mode: scan (default), simulate, or live'
    )
    
    args = parser.parse_args()
    
    try:
        asyncio.run(main(mode=args.mode))
    except KeyboardInterrupt:
        print("\nBot stopped by user")
        sys.exit(0)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
