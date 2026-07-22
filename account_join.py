"""
Joins channels/groups with 3 randomized modes:

Mode A – Stay Online (≈20%)   : comes online, joins, stays online forever
Mode B – Lurk (≈40%)          : joins hiding last-seen (privacy), goes offline after join
Mode C – Fade (≈40%)          : joins normally, goes offline exactly 2 minutes later

The accounts are shuffled, assigned to modes, then joined with random
delays between min_delay and max_delay seconds.
"""

import asyncio
import logging
import random
from datetime import datetime

from telethon import TelegramClient, functions
from telethon.errors import FloodWaitError, UserAlreadyParticipantError
from telethon.sessions import StringSession
from telethon.tl.functions.account import SetPrivacyRequest, UpdateStatusRequest
from telethon.tl.types import (
    InputPrivacyKeyStatusTimestamp,
    InputPrivacyValueDisallowAll,
    InputPrivacyValueAllowAll,
)

logger = logging.getLogger(__name__)


class AccountJoiner:
    def __init__(self, account_manager, api_id: int, api_hash: str):
        self.am = account_manager
        self.api_id = api_id
        self.api_hash = api_hash

    async def join_all(
        self,
        chat_target: str,
        min_delay: int,
        max_delay: int,
        progress_callback=None,
    ):
        """
        Join all connected accounts to `chat_target`.
        `chat_target` can be: @username, https://t.me/..., invite link.
        """
        accounts = self.am.get_connected_accounts()
        if not accounts:
            msg = "❌ No connected accounts found."
            if progress_callback:
                await progress_callback(msg)
            return

        random.shuffle(accounts)

        # Assign modes: 20% online, 40% lurk, 40% fade
        n = len(accounts)
        n_online = max(1, round(n * 0.2))
        n_lurk = max(1, round(n * 0.4))
        # The rest go to fade

        for i, acc in enumerate(accounts):
            if i < n_online:
                acc["_mode"] = "online"
            elif i < n_online + n_lurk:
                acc["_mode"] = "lurk"
            else:
                acc["_mode"] = "fade"

        random.shuffle(accounts)  # shuffle again so modes are interleaved
        results = {"success": 0, "failed": 0, "not_in_chat": []}

        for idx, acc in enumerate(accounts):
            delay = random.randint(min_delay, max_delay)
            if idx > 0:
                await asyncio.sleep(delay)

            mode = acc["_mode"]
            user_id = acc["user_id"]
            ss = acc["session_string"]

            try:
                client = TelegramClient(StringSession(ss), self.api_id, self.api_hash)
                await client.connect()
                if not await client.is_user_authorized():
                    results["failed"] += 1
                    await client.disconnect()
                    continue

                # Resolve the chat entity
                try:
                    entity = await client.get_entity(chat_target)
                except Exception:
                    # Might be an invite link
                    entity = await client.get_entity(chat_target)

                # Mode-specific pre-join setup
                if mode == "online":
                    # Come online before joining
                    await client(UpdateStatusRequest(offline=False))
                elif mode == "lurk":
                    # Hide last-seen from everyone
                    await client(
                        SetPrivacyRequest(
                            key=InputPrivacyKeyStatusTimestamp(),
                            rules=[InputPrivacyValueDisallowAll()],
                        )
                    )

                # Join
                try:
                    if hasattr(entity, "username") and entity.username:
                        await client(functions.channels.JoinChannelRequest(entity))
                    else:
                        await client(functions.messages.ImportChatInviteRequest(
                            chat_target.split("+")[-1]
                        ))
                except UserAlreadyParticipantError:
                    pass  # already in, that's fine
                except Exception:
                    # Try invite link approach
                    try:
                        await client(functions.messages.ImportChatInviteRequest(
                            chat_target.split("/")[-1]
                        ))
                    except Exception:
                        pass

                results["success"] += 1
                logger.info(f"Account {user_id} joined ({mode=})")

                # Mode-specific post-join actions
                if mode == "online":
                    # Stay online — do nothing else
                    await client(UpdateStatusRequest(offline=False))
                    pass  # keep online
                elif mode == "lurk":
                    # Go offline after join
                    await asyncio.sleep(2)
                    await client(UpdateStatusRequest(offline=True))
                elif mode == "fade":
                    # Go offline after exactly 2 minutes
                    asyncio.create_task(self._go_offline_after(client, 120))

                # Don't disconnect for online mode — keep them "alive"
                if mode != "online":
                    await client.disconnect()

            except FloodWaitError as e:
                logger.warning(f"Flood wait on account {user_id}: {e.seconds}s")
                results["failed"] += 1
            except Exception as e:
                logger.error(f"Error joining account {user_id}: {e}")
                results["failed"] += 1

        msg = (
            f"✅ Join process complete!\n"
            f"   • Successful: {results['success']}\n"
            f"   • Failed: {results['failed']}\n"
            f"   • Mode distribution: {n_online} online, {n_lurk} lurk, {n - n_online - n_lurk} fade"
        )
        if progress_callback:
            await progress_callback(msg)
        return results

    async def _go_offline_after(self, client: TelegramClient, seconds: int):
        """Set account offline after `seconds` delay."""
        await asyncio.sleep(seconds)
        try:
            await client(UpdateStatusRequest(offline=True))
            logger.info(f"Account went offline after {seconds}s")
        except Exception:
            pass
        finally:
            await client.disconnect()
