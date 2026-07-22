"""
Boost view count on a channel post via GetMessagesViewsRequest with increment=True.
Note: Telegram limits this to ~1-2 increments per account per day.
"""

import asyncio
import logging
import random
import re

from telethon import TelegramClient, functions, types
from telethon.errors import FloodWaitError
from telethon.sessions import StringSession

logger = logging.getLogger(__name__)

LINK_PATTERN = re.compile(
    r"https?://t\.me/(?:c/(\d+)|([^/]+))/(\d+)"
)


def parse_post_link(link: str) -> tuple[str | int, int]:
    m = LINK_PATTERN.match(link.strip())
    if not m:
        raise ValueError(f"Invalid link: {link}")
    cid, uname, mid = m.groups()
    if cid:
        return int(cid), int(mid)
    return uname, int(mid)


class ViewBooster:
    def __init__(self, account_manager, api_id: int, api_hash: str):
        self.am = account_manager
        self.api_id = api_id
        self.api_hash = api_hash

    async def boost_views(
        self,
        links: list[str],
        count: int,
        progress_callback=None,
    ):
        """
        Boost views on multiple post links.
        Each connected account increments the view counter once per link (per day limit).
        """
        accounts = self.am.get_connected_accounts()
        if not accounts:
            msg = "❌ No connected accounts."
            if progress_callback:
                await progress_callback(msg)
            return

        for link in links:
            await self._boost_single_link(link, accounts, count, progress_callback)

    async def _boost_single_link(
        self,
        link: str,
        accounts: list[dict],
        target_count: int,
        progress_callback,
    ):
        peer, msg_id = parse_post_link(link)
        random.shuffle(accounts)
        results = {"success": 0, "failed": 0, "not_in_chat": []}

        usable = accounts[: min(target_count, len(accounts))]

        for acc in usable:
            user_id = acc["user_id"]
            ss = acc["session_string"]

            try:
                client = TelegramClient(StringSession(ss), self.api_id, self.api_hash)
                await client.connect()
                if not await client.is_user_authorized():
                    results["failed"] += 1
                    await client.disconnect()
                    continue

                # Resolve peer
                try:
                    if isinstance(peer, int):
                        resolved = await client.get_entity(types.PeerChannel(peer))
                    else:
                        resolved = await client.get_entity(peer)
                except Exception:
                    results["failed"] += 1
                    results["not_in_chat"].append(user_id)
                    await client.disconnect()
                    continue

                # Increment view count
                await client(
                    functions.messages.GetMessagesViewsRequest(
                        peer=resolved,
                        id=[msg_id],
                        increment=True,
                    )
                )
                results["success"] += 1
                logger.info(f"Account {user_id} viewed msg {msg_id}")

                await client.disconnect()
                await asyncio.sleep(random.uniform(2, 5))

            except FloodWaitError as e:
                logger.warning(f"Flood on {user_id}: wait {e.seconds}s")
                results["failed"] += 1
            except Exception as e:
                logger.error(f"View boost error on {user_id}: {e}")
                results["failed"] += 1

        msg = (
            f"👁️ View boost for `{link}`\n"
            f"   • Incremented: {results['success']}\n"
            f"   • Failed: {results['failed']}\n"
            f"   • (Note: Telegram allows ~1-2 increments/account/day)"
        )
        if results["not_in_chat"]:
            msg += f"\n   • Accounts NOT in this chat: {len(results['not_in_chat'])}"

        if progress_callback:
            await progress_callback(msg)
