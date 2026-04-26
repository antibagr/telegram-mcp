#!/usr/bin/env python3
"""
Session string generator with explicit auth flow and verbose output.
"""

import asyncio
import os
import sys

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import StringSession

load_dotenv()


async def main():
    api_id = int(os.getenv("TELEGRAM_API_ID"))
    api_hash = os.getenv("TELEGRAM_API_HASH")

    print(f"API_ID: {api_id}")
    print(f"API_HASH: {api_hash[:8]}...")
    print()

    client = TelegramClient(StringSession(), api_id, api_hash)
    await client.connect()
    print(f"Connected: {client.is_connected()}")

    phone = input("Enter phone number (international format, e.g. +995...): ").strip()

    try:
        result = await client.send_code_request(phone, force_sms=True)
        print(f"\nCode request sent successfully!")
        print(f"  phone_code_hash: {result.phone_code_hash}")
        print(f"  type: {type(result.type).__name__}")
        print(f"  next_type: {getattr(result, 'next_type', 'N/A')}")
        print(f"  timeout: {getattr(result, 'timeout', 'N/A')}")
    except Exception as e:
        print(f"\nERROR sending code request: {type(e).__name__}: {e}")
        await client.disconnect()
        sys.exit(1)

    code = input("\nEnter the code you received: ").strip()

    try:
        await client.sign_in(phone, code, phone_code_hash=result.phone_code_hash)
    except Exception as e:
        err_name = type(e).__name__
        print(f"\nsign_in raised: {err_name}: {e}")
        if "password" in str(e).lower() or "2fa" in err_name.lower() or "Two" in str(e):
            password = input("Enter your 2FA password: ").strip()
            try:
                await client.sign_in(password=password)
            except Exception as e2:
                print(f"\n2FA sign_in failed: {type(e2).__name__}: {e2}")
                await client.disconnect()
                sys.exit(1)
        else:
            await client.disconnect()
            sys.exit(1)

    if await client.is_user_authorized():
        session_string = StringSession.save(client.session)
        print("\n===== Authentication successful! =====")
        print(f"\nSession string:\n{session_string}\n")

        choice = input("Update .env file? (y/N): ").strip()
        if choice.lower() == "y":
            with open(".env", "r") as f:
                lines = f.readlines()
            found = False
            for i, line in enumerate(lines):
                if line.startswith("TELEGRAM_SESSION_STRING="):
                    lines[i] = f"TELEGRAM_SESSION_STRING={session_string}\n"
                    found = True
                    break
            if not found:
                lines.append(f"TELEGRAM_SESSION_STRING={session_string}\n")
            with open(".env", "w") as f:
                f.writelines(lines)
            print(".env updated!")
    else:
        print("\nFailed to authorize.")

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
