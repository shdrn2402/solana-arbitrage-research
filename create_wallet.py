#!/usr/bin/env python3
"""
Create a new Solana wallet for the bot.
⚠️ IMPORTANT: Save the private key securely!
"""

import base58
from solders.keypair import Keypair

# Create a new wallet
keypair = Keypair()

# Get private key in base58 format (needed for .env)
private_key_base58 = base58.b58encode(bytes(keypair)).decode('utf-8')

# Get public address
public_key = str(keypair.pubkey())

print("=" * 60)
print("NEW WALLET CREATED")
print("=" * 60)
print(f"\nPublic Address (Public Key):")
print(public_key)
print(f"\nPrivate Key (base58):")
print(private_key_base58)
print("\n" + "=" * 60)
print("⚠️  IMPORTANT:")
print("1. Save the private key in a secure place!")
print("2. Add it to .env as WALLET_PRIVATE_KEY")
print("3. Fund the wallet with SOL for testing")
print("4. NEVER publish the private key!")
print("=" * 60)