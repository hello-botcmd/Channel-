"""
Adds reactions to a Telegram post (channel or group).
Parses both public (t.me/username/msg_id) and private (t.me/c/chat_id/msg_id) links.
"""

import asyncio
import logging
import random
import re

from telethon import TelegramClient, functions, types
from telethon.errors import FloodWaitError
from telethon.sessions import StringSession
from telethon.tl.functions.messages import SendReactionRequest

logger = logging.getLogger(__name__)

LINK_PATTERN = re.compile(
    r"https?://t\.me/(?:c/(\d+)|([^/]+))/(\d+)"
)


def parse_post_link(link: str) -> tuple[str | int, int]:
    """
    Parse a Telegram post link.
    Returns (peer_identifier, message_id).
    peer_identifier is the username (str) for public, or channel ID (int) for private.
    """
    m = LINK_PATTERN.match(link.strip())
    if not m:
        raise ValueError(f"Invalid Telegram post link: {link}")
    channel_id_str, username, msg_id_str = m.groups()
    msg_id = int(msg_id_str)
    if channel_id_str:
        # private: t.me/c/123456789/123
        return int(channel_id_str), msg_id
    else:
        # public: t.me/username/123
        return username, msg_id


class ReactionHandler:
    def __init__(self, account_manager, api_id: int, api_hash: str):
        self.am = account_manager
        self.api_id = api_id
        self.api_hash = api_hash

    async def add_reactions(
        self,
        post_link: str,
        reactions: list[str],
        count: int,
        progress_callback=None,
    ):
        """
        Add reactions to a post. Each account can react once.
        `reactions` is a list of emoji strings (e.g. ["❤️", "👍"]).
        `count` is how many total reactions the user wants.
        """
        accounts = self.am.get_connected_accounts()
        if not accounts:
            msg = "❌ No connected accounts."
            if progress_callback:
                await progress_callback(msg)
            return

        peer, msg_id = parse_post_link(post_link)

        random.shuffle(accounts)
        results = {"success": 0, "failed": 0, "not_in_chat": []}

        # Only use as many accounts as needed (up to count)
        usable = accounts[: min(count, len(accounts))]

        for acc in usable:
            user_id = acc["user_id"]
            ss = acc["session_string"]
            reaction_emoji = random.choice(reactions)

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
                        resolved = await client.get_entity(
                            types.PeerChannel(peer)
                        )
                    else:
                        resolved = await client.get_entity(peer)
                except Exception:
                    results["failed"] += 1
                    results["not_in_chat"].append(user_id)
                    await client.disconnect()
                    continue

                # Send reaction
                await client(
                    SendReactionRequest(
                        peer=resolved,
                        msg_id=msg_id,
                        reaction=[types.ReactionEmoji(emoticon=reaction_emoji)],
                    )
                )
                results["success"] += 1
                logger.info(f"Account {user_id} reacted with {reaction_emoji}")

                await client.disconnect()
                await asyncio.sleep(random.uniform(1, 3))  # be polite

            except FloodWaitError as e:
                logger.warning(f"Flood on {user_id}: wait {e.seconds}s")
                results["failed"] += 1
            except Exception as e:
                logger.error(f"Reaction error on {user_id}: {e}")
                results["failed"] += 1

        msg = (
            f"✅ Reactions added!\n"
            f"   • Success: {results['success']}\n"
            f"   • Failed: {results['failed']}\n"
        )
        if results["not_in_chat"]:
            msg += f"   • Not in chat: {len(results['not_in_chat'])} account(s)\n"

        if progress_callback:
            await progress_callback(msg)
        return results
