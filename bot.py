"""
Main Telegram bot — entry point.
Presents an inline keyboard with all operations and routes user input.
"""

import asyncio
import logging
import os
import re

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

import config
from account_load import AccountManager
from account_join import AccountJoiner
from reaction import ReactionHandler
from views import ViewBooster

# ── Logging ─────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Globals ─────────────────────────────────────────────────────────────
am = AccountManager(config.MONGO_URI, config.DB_NAME, config.API_ID, config.API_HASH)
joiner = AccountJoiner(am, config.API_ID, config.API_HASH)
reactor = ReactionHandler(am, config.API_ID, config.API_HASH)
viewer = ViewBooster(am, config.API_ID, config.API_HASH)

# In-memory user state machine
user_state: dict[int, dict] = {}


# ── Helpers ─────────────────────────────────────────────────────────────
def main_menu() -> InlineKeyboardMarkup:
    kb = [
        [InlineKeyboardButton("📱 Add Account", callback_data="add_account")],
        [InlineKeyboardButton("🔗 Join Channel / GC", callback_data="join")],
        [InlineKeyboardButton("👁️ View Boost", callback_data="view_boost")],
        [InlineKeyboardButton("❤️ Reaction", callback_data="reaction")],
        [InlineKeyboardButton("🟢 All IDs Online", callback_data="all_online")],
        [InlineKeyboardButton("📊 Total Account", callback_data="total_account")],
    ]
    return InlineKeyboardMarkup(kb)


async def reply_or_edit(update: Update, text: str, **kwargs):
    """Helper: reply to message or edit callback message."""
    if update.callback_query:
        q = update.callback_query
        await q.edit_message_text(text, **kwargs)
    else:
        await update.message.reply_text(text, **kwargs)


async def send_progress(msg: str):
    """Dummy progress callback — we send updates from the caller instead."""
    pass


# ── Command: /start ─────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Telegram Account Manager*\n\n"
        "Manage multiple Telegram accounts:\n"
        "add → join → boost views → react → control online status",
        reply_markup=main_menu(),
        parse_mode="Markdown",
    )


# ── Callback routing ────────────────────────────────────────────────────
async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    uid = update.effective_user.id

    if data == "back":
        user_state.pop(uid, None)
        await q.edit_message_text(
            "🤖 *Telegram Account Manager*\n\nChoose an action:",
            reply_markup=main_menu(),
            parse_mode="Markdown",
        )

    elif data == "add_account":
        kb = [
            [InlineKeyboardButton("📱 Phone + OTP", callback_data="add_phone")],
            [InlineKeyboardButton("🔑 Session String / File", callback_data="add_session")],
            [InlineKeyboardButton("◀️ Back", callback_data="back")],
        ]
        await q.edit_message_text("Choose add method:", reply_markup=InlineKeyboardMarkup(kb))

    elif data == "add_phone":
        user_state[uid] = {"state": "await_phone"}
        await q.edit_message_text(
            "📱 Send me the phone number in international format:\n`+1234567890`",
            parse_mode="Markdown",
        )

    elif data == "add_session":
        user_state[uid] = {"state": "await_session"}
        await q.edit_message_text(
            "🔑 Send me the session string **as text** or as a **.txt file**.",
            parse_mode="Markdown",
        )

    elif data == "join":
        user_state[uid] = {"state": "await_join_target"}
        await q.edit_message_text(
            "🔗 Send the channel/group:\n"
            "• Username: `@channel`\n"
            "• Public link: `https://t.me/channel`\n"
            "• Invite link: `https://t.me/+abc123`",
            parse_mode="Markdown",
        )

    elif data == "view_boost":
        user_state[uid] = {"state": "await_view_links"}
        await q.edit_message_text(
            "👁️ Send post link(s) to boost — one per line:\n"
            "`https://t.me/username/123`\n"
            "`https://t.me/c/123456789/123`",
            parse_mode="Markdown",
        )

    elif data == "reaction":
        user_state[uid] = {"state": "await_reaction_link"}
        await q.edit_message_text(
            "❤️ Send the post link to react to:\n"
            "`https://t.me/username/123`\n`https://t.me/c/123456789/123`",
            parse_mode="Markdown",
        )

    elif data == "all_online":
        await _all_online(update, context)

    elif data == "total_account":
        await _total_account(update, context)

    else:
        await q.edit_message_text("Unknown option.", reply_markup=main_menu())


# ── Message handler (state machine) ─────────────────────────────────────
async def msg_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in user_state:
        await update.message.reply_text("Use /start to see the menu.")
        return

    state = user_state[uid]["state"]
    text = update.message.text.strip() if update.message.text else ""

    # ── Add Phone ───────────────────────────────────────────────────
    if state == "await_phone":
        phone = text
        if not phone.startswith("+"):
            await update.message.reply_text("❌ Phone must start with `+`. Try again.", parse_mode="Markdown")
            return
        try:
            await am.send_otp(phone)
        except Exception as e:
            await update.message.reply_text(f"❌ Failed to send OTP: {e}\nUse /start to retry.")
            user_state.pop(uid, None)
            return
        user_state[uid] = {"state": "await_otp", "phone": phone}
        await update.message.reply_text(f"📱 OTP sent to `{phone}`\nNow send me the OTP code:", parse_mode="Markdown")

    elif state == "await_otp":
        phone = user_state[uid].get("phone")
        code = text
        user_state[uid] = {"state": "await_2fa", "phone": phone, "code": code}
        await update.message.reply_text(
            "🔐 If you have 2FA enabled, send your password now.\n"
            "Otherwise send: `/skip`",
            parse_mode="Markdown",
        )

    elif state == "await_2fa":
        phone = user_state[uid]["phone"]
        code = user_state[uid]["code"]
        password = None if text == "/skip" else text
        try:
            result = await am.complete_phone_login(phone, code, password)
            await update.message.reply_text(
                f"✅ Account added!\n   Name: {result['first_name']}\n   ID: {result['user_id']}",
                reply_markup=main_menu(),
            )
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {e}", reply_markup=main_menu())
        user_state.pop(uid, None)

    # ── Add Session ─────────────────────────────────────────────────
    elif state == "await_session":
        session_string = text
        if update.message.document:
            file = await update.message.document.get_file()
            path = f"/tmp/session_{uid}.txt"
            await file.download_to_drive(path)
            with open(path, "r") as f:
                session_string = f.read().strip()
            os.remove(path)
        try:
            result = await am.add_account_session(session_string)
            await update.message.reply_text(
                f"✅ Account added!\n   Name: {result['first_name']}\n   ID: {result['user_id']}",
                reply_markup=main_menu(),
            )
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {e}", reply_markup=main_menu())
        user_state.pop(uid, None)

    # ── Join ────────────────────────────────────────────────────────
    elif state == "await_join_target":
        user_state[uid] = {"state": "await_join_delay", "target": text}
        await update.message.reply_text(
            "⏱️ Enter delay range (seconds) between joins.\n"
            "Format: `min, max`  example: `8, 10`",
            parse_mode="Markdown",
        )

    elif state == "await_join_delay":
        try:
            parts = text.replace(" ", "").split(",")
            min_d = int(parts[0])
            max_d = int(parts[1])
        except (ValueError, IndexError):
            await update.message.reply_text("❌ Invalid format. Use: `8, 10`", parse_mode="Markdown")
            return

        target = user_state[uid]["target"]
        await update.message.reply_text(f"🔄 Joining `{target}` with delay {min_d}-{max_d}s…", parse_mode="Markdown")
        user_state.pop(uid, None)

        async def progress(m):
            await update.message.reply_text(m, parse_mode="Markdown")

        asyncio.create_task(joiner.join_all(target, min_d, max_d, progress))

    # ── View Boost ──────────────────────────────────────────────────
    elif state == "await_view_links":
        links = [l.strip() for l in text.split("\n") if l.strip()]
        user_state[uid] = {"state": "await_view_count", "links": links}
        await update.message.reply_text("👁️ How many views to boost per link?")

    elif state == "await_view_count":
        try:
            count = int(text)
        except ValueError:
            await update.message.reply_text("❌ Enter a valid number.")
            return
        links = user_state[uid]["links"]
        await update.message.reply_text(f"🔄 Boosting {count} views on {len(links)} link(s)…")
        user_state.pop(uid, None)

        async def progress(m):
            await update.message.reply_text(m, parse_mode="Markdown")

        asyncio.create_task(viewer.boost_views(links, count, progress))

    # ── Reaction ────────────────────────────────────────────────────
    elif state == "await_reaction_link":
        user_state[uid] = {"state": "await_reaction_types", "link": text}
        await update.message.reply_text(
            "❤️ Enter the reactions you want (space-separated emoji):\n"
            "Example: `❤️ 👍 😊 🔥`\n"
            "Available: 🥰 ❤️ 😊 ☺️ 👍 👎 🔥 🎉 💯"
        )

    elif state == "await_reaction_types":
        reactions = text.strip().split()
        if not reactions:
            await update.message.reply_text("❌ Enter at least one reaction.")
            return
        user_state[uid]["reactions"] = reactions
        user_state[uid]["state"] = "await_reaction_count"
        await update.message.reply_text("How many reactions to add?")

    elif state == "await_reaction_count":
        try:
            count = int(text)
        except ValueError:
            await update.message.reply_text("❌ Enter a valid number.")
            return
        link = user_state[uid]["link"]
        reactions = user_state[uid]["reactions"]
        await update.message.reply_text(f"🔄 Adding {count} reactions…")
        user_state.pop(uid, None)

        async def progress(m):
            await update.message.reply_text(m, parse_mode="Markdown")

        asyncio.create_task(reactor.add_reactions(link, reactions, count, progress))

    else:
        await update.message.reply_text("Unknown state. Use /start to restart.")


# ── All IDs Online ──────────────────────────────────────────────────────
async def _all_online(update: Update, context: ContextTypes.DEFAULT_TYPE):
    accounts = am.get_connected_accounts()
    if not accounts:
        await reply_or_edit(update, "❌ No connected accounts.", reply_markup=main_menu())
        return

    await reply_or_edit(update, "🟢 Setting all accounts online…")
    success = 0
    failed = 0

    for acc in accounts:
        try:
            from telethon import TelegramClient
            from telethon.sessions import StringSession
            from telethon.tl.functions.account import UpdateStatusRequest

            client = TelegramClient(StringSession(acc["session_string"]), config.API_ID, config.API_HASH)
            await client.connect()
            if await client.is_user_authorized():
                await client(UpdateStatusRequest(offline=False))
                am.update_online(acc["user_id"], True)
                success += 1
            await client.disconnect()
        except Exception as e:
            logger.warning(f"Failed to set online for {acc['user_id']}: {e}")
            failed += 1

    msg = f"🟢 All IDs Online: {success} success, {failed} failed"
    # Send a new message if called via callback
    if update.callback_query:
        await update.callback_query.edit_message_text(msg, reply_markup=main_menu())
    else:
        await update.message.reply_text(msg, reply_markup=main_menu())


# ── Total Account ───────────────────────────────────────────────────────
async def _total_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    total, connected, disconnected = am.get_account_counts()
    msg = (
        f"📊 *Account Statistics*\n\n"
        f"📱 Total: `{total}`\n"
        f"🟢 Connected: `{connected}`\n"
        f"🔴 Disconnected: `{disconnected}`"
    )
    if update.callback_query:
        await update.callback_query.edit_message_text(msg, reply_markup=main_menu(), parse_mode="Markdown")
    else:
        await update.message.reply_text(msg, reply_markup=main_menu(), parse_mode="Markdown")


# ── Main ────────────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(config.BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(callback_router))
    app.add_handler(MessageHandler(filters.TEXT | filters.Document.ALL, msg_handler))

    logger.info("🤖 Bot started — polling…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
