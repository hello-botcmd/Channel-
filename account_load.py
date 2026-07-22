"""
Account management: MongoDB CRUD, add via phone+OTP+2FA or session string,
auto-update profile name from name.txt.
"""

import os
import asyncio
import logging
import random
from datetime import datetime

from pymongo import MongoClient
from telethon import TelegramClient, functions
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession
from telethon.tl.functions.account import UpdateProfileRequest

logger = logging.getLogger(__name__)

NAMES_FILE = "name.txt"


def _load_names() -> list[str]:
    """Read names from name.txt, one per line."""
    if not os.path.exists(NAMES_FILE):
        logger.warning(f"{NAMES_FILE} not found, using default names.")
        return ["User"]
    with open(NAMES_FILE, "r", encoding="utf-8") as f:
        names = [line.strip() for line in f if line.strip()]
    return names if names else ["User"]


class AccountManager:
    """Handles all MongoDB and account lifecycle operations."""

    def __init__(self, mongo_uri: str, db_name: str, api_id: int, api_hash: str):
        self.mongo = MongoClient(mongo_uri)
        self.db = self.mongo[db_name]
        self.collection = self.db["accounts"]
        self.api_id = api_id
        self.api_hash = api_hash
        self._pending_otp: dict[str, TelegramClient] = {}  # phone -> client

    # ── Add account via phone + OTP + optional 2FA ──────────────────────

    async def send_otp(self, phone: str) -> str:
        """Send OTP code to the given phone, return the phone for tracking."""
        client = TelegramClient(StringSession(), self.api_id, self.api_hash)
        await client.connect()
        await client.send_code_request(phone)
        self._pending_otp[phone] = client
        logger.info(f"OTP sent to {phone}")
        return phone

    async def complete_phone_login(
        self, phone: str, code: str, password: str | None = None
    ) -> dict:
        """Complete login with OTP code and optional 2FA password."""
        client = self._pending_otp.pop(phone, None)
        if not client:
            raise RuntimeError(f"No pending OTP session for {phone}")

        try:
            await client.sign_in(phone, code)
        except SessionPasswordNeededError:
            if not password:
                raise RuntimeError("2FA password is required")
            await client.sign_in(password=password)

        return await self._finalize_account(client, phone)

    # ── Add account via session string ──────────────────────────────────

    async def add_account_session(self, session_string: str) -> dict:
        """Validate and store an account from a session string."""
        client = TelegramClient(StringSession(session_string), self.api_id, self.api_hash)
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            raise ValueError("Session string is invalid or expired")
        return await self._finalize_account(client, None)

    # ── Internal: save account + update profile name ────────────────────

    async def _finalize_account(
        self, client: TelegramClient, phone: str | None
    ) -> dict:
        """Save account to MongoDB and auto-update its profile name."""
        me = await client.get_me()
        names = _load_names()
        new_name = random.choice(names)

        # Update Telegram profile name
        try:
            await client(
                UpdateProfileRequest(
                    first_name=new_name,
                    last_name="",
                )
            )
            logger.info(f"Updated profile name for {me.id} -> {new_name}")
        except Exception as e:
            logger.warning(f"Failed to update profile name for {me.id}: {e}")

        # Build account document
        session_string = client.session.save()
        account_doc = {
            "user_id": me.id,
            "phone": phone or getattr(me, "phone", f"id_{me.id}"),
            "session_string": session_string,
            "username": getattr(me, "username", None),
            "first_name": new_name,
            "last_name": "",
            "status": "connected",
            "online": False,
            "last_seen_mode": "normal",
            "created_at": datetime.utcnow().isoformat(),
        }

        self.collection.update_one(
            {"user_id": me.id},
            {"$set": account_doc},
            upsert=True,
        )
        logger.info(f"Account {me.id} saved to MongoDB")
        await client.disconnect()
        return account_doc

    # ── Queries ─────────────────────────────────────────────────────────

    def get_all_accounts(self) -> list[dict]:
        return list(self.collection.find({}))

    def get_connected_accounts(self) -> list[dict]:
        return list(self.collection.find({"status": "connected"}))

    def get_disconnected_accounts(self) -> list[dict]:
        return list(self.collection.find({"status": "disconnected"}))

    def get_account_counts(self) -> tuple[int, int, int]:
        total = self.collection.count_documents({})
        connected = self.collection.count_documents({"status": "connected"})
        disconnected = self.collection.count_documents({"status": "disconnected"})
        return total, connected, disconnected

    def update_status(self, user_id: int, status: str):
        self.collection.update_one({"user_id": user_id}, {"$set": {"status": status}})

    def update_online(self, user_id: int, online: bool):
        self.collection.update_one({"user_id": user_id}, {"$set": {"online": online}})
