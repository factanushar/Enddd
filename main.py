import os
import html
import sqlite3
import logging
import asyncio
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatType, ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ContextTypes, filters,
)

from config import (
    BOT_TOKEN, ADMIN_IDS, CHANNELS, GIVEAWAY_CHANNEL_URL, DATABASE_FILE,
    SUBSCRIBERS_DB, BACKUP_DIR, QUALIFICATION_REFERRALS,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("sf_winner_notice")
os.makedirs("data", exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)


def is_admin(uid):
    return int(uid or 0) in {int(x) for x in ADMIN_IDS}


def init_subscribers():
    with sqlite3.connect(SUBSCRIBERS_DB) as db:
        db.execute("CREATE TABLE IF NOT EXISTS subscribers(user_id INTEGER PRIMARY KEY, first_name TEXT, username TEXT, added_at TEXT DEFAULT CURRENT_TIMESTAMP)")


def save_subscriber(user):
    with sqlite3.connect(SUBSCRIBERS_DB) as db:
        db.execute("INSERT INTO subscribers(user_id,first_name,username) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET first_name=excluded.first_name, username=excluded.username", (user.id, user.first_name or "", user.username or ""))


def validate_giveaway_db(path=DATABASE_FILE):
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        return False, "Database file missing or empty."
    try:
        with sqlite3.connect(f"file:{os.path.abspath(path)}?mode=ro", uri=True) as db:
            check = db.execute("PRAGMA integrity_check").fetchone()
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not check or check[0] != "ok":
                return False, "SQLite integrity check failed."
            if not {"users", "giveaway_results"}.issubset(tables):
                return False, "This is not a compatible SF ARMY giveaway database (users/giveaway_results missing)."
        return True, "OK"
    except Exception as exc:
        return False, str(exc)


def get_winners():
    ok, reason = validate_giveaway_db()
    if not ok:
        raise RuntimeError(reason)
    with sqlite3.connect(f"file:{os.path.abspath(DATABASE_FILE)}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute("""
            SELECT r.user_id, r.final_rank, r.username AS result_username,
                   r.first_name AS result_first_name, r.last_name AS result_last_name,
                   r.verified_referrals, r.referral_coins,
                   u.username AS live_username, u.first_name AS live_first_name,
                   u.last_name AS live_last_name, u.total_coins
            FROM giveaway_results r LEFT JOIN users u ON u.user_id=r.user_id
            ORDER BY r.final_rank ASC
        """).fetchall()
        return [dict(x) for x in rows]


def winner_text():
    rows = get_winners()
    if not rows:
        return "🏆 <b>SF ARMY GIVEAWAY WINNERS</b>\n\nNo finalized winners were found in the restored database."
    lines = [
        "🏆 <b>SF ARMY GIVEAWAY — QUALIFIED WINNERS</b>",
        "🦅 <b>SF ARMY 10K SPECIAL GIVEAWAY</b>",
        "",
        f"✅ <b>Total Qualified Winners:</b> {len(rows)}",
        f"🎯 <b>Qualification:</b> {QUALIFICATION_REFERRALS} verified referrals",
        "━━━━━━━━━━━━━━━━━━",
    ]
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    for r in rows:
        rank = int(r["final_rank"])
        first = (r.get("live_first_name") or r.get("result_first_name") or "").strip()
        last = (r.get("live_last_name") or r.get("result_last_name") or "").strip()
        name = (first + " " + last).strip() or "Unknown User"
        username = r.get("live_username") or r.get("result_username")
        handle = "@" + username.lstrip("@") if username else "Not Set"
        coins = int(r.get("total_coins") or 0)
        referrals = int(r.get("verified_referrals") or 0)
        prefix = medals.get(rank, f"<b>#{rank}</b>")
        lines.extend([
            f"{prefix} <b>{html.escape(name)}</b>",
            f"   👤 {html.escape(handle)}",
            f"   🆔 <code>{int(r['user_id'])}</code>",
            f"   🪙 <b>{coins:,} Total Coins</b>",
            f"   👥 Verified Referrals: <b>{referrals}</b>",
            "   🎯 ✅ Qualified",
            "",
        ])
    lines.extend(["━━━━━━━━━━━━━━━━━━", "💓🤍 <b>Congratulations to all winners!</b>"])
    return "\n".join(lines)


def split_html_message(text, limit=3900):
    # Winner blocks are split only at line boundaries to stay below Telegram limits.
    chunks, current = [], ""
    for line in text.split("\n"):
        candidate = (current + "\n" + line) if current else line
        if len(candidate) > limit and current:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def join_keyboard():
    rows = [[InlineKeyboardButton(c["name"], url=c["url"])] for c in CHANNELS]
    rows.append([InlineKeyboardButton("✅ I Verified", callback_data="verify_membership")])
    return InlineKeyboardMarkup(rows)


def show_winners_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏆 Giveaway & Show Winners Name", callback_data="show_winners")],
        [InlineKeyboardButton("📢 SF Giveaway Channel", url=GIVEAWAY_CHANNEL_URL)],
    ])


def admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📣 Broadcast", callback_data="admin_broadcast")],
        [InlineKeyboardButton("🗄️ Backup Database", callback_data="admin_backup")],
        [InlineKeyboardButton("♻️ Data Recovery", callback_data="admin_recovery")],
    ])


async def membership_ok(bot, user_id):
    for channel in CHANNELS:
        try:
            member = await bot.get_chat_member(chat_id=channel["chat_id"], user_id=user_id)
            if member.status in ("left", "kicked"):
                return False
        except TelegramError:
            return False
    return True


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_chat.type != ChatType.PRIVATE:
        return
    save_subscriber(update.effective_user)
    await update.message.reply_text(
        "🦅 <b>Welcome to SF ARMY Giveaway Results!</b>\n\n"
        "Please join all required channels and groups, then tap <b>I Verified</b> to view the qualified winners.",
        reply_markup=join_keyboard(), parse_mode=ParseMode.HTML,
    )


async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    if q.data == "verify_membership":
        if not await membership_ok(context.bot, uid):
            await q.message.reply_text("❌ You have not joined all required channels/groups yet. Join them and try again.", reply_markup=join_keyboard())
            return
        await q.message.reply_text("✅ <b>Membership Verified!</b>\n\nTap below to view the finalized giveaway winners.", reply_markup=show_winners_keyboard(), parse_mode=ParseMode.HTML)
    elif q.data == "show_winners":
        try:
            text = winner_text()
            for chunk in split_html_message(text):
                await q.message.reply_text(chunk, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        except Exception as exc:
            log.exception("Could not load winners")
            await q.message.reply_text("⚠️ Winners data is not available yet. Please contact the manager.")
    elif q.data.startswith("admin_"):
        if not is_admin(uid):
            await q.message.reply_text("⛔ Admin only.")
            return
        action = q.data.removeprefix("admin_")
        if action == "broadcast":
            context.user_data["admin_action"] = "broadcast"
            await q.message.reply_text("📣 Send the broadcast text now. HTML formatting is supported. Send /cancel to stop.")
        elif action == "backup":
            if not os.path.isfile(DATABASE_FILE):
                await q.message.reply_text("❌ Giveaway database not found.")
                return
            with open(DATABASE_FILE, "rb") as f:
                await q.message.reply_document(f, filename="SF_ARMY_Giveaway_Backup.db", caption="🗄️ Current giveaway database backup")
        elif action == "recovery":
            context.user_data["admin_action"] = "recovery"
            await q.message.reply_text("♻️ Upload the original SF ARMY giveaway database (.db) as a document. It will be validated before replacing the current results database. Send /cancel to stop.")


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ You are not authorized.")
        return
    await update.message.reply_text("🛠️ <b>SF ARMY ADMIN PANEL</b>", reply_markup=admin_keyboard(), parse_mode=ParseMode.HTML)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("admin_action", None)
    await update.message.reply_text("Cancelled.")


async def admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    action = context.user_data.get("admin_action")
    if action != "broadcast" or not update.message.text:
        return
    text = update.message.text_html
    context.user_data.pop("admin_action", None)
    with sqlite3.connect(f"file:{os.path.abspath(DATABASE_FILE)}?mode=ro", uri=True) as db:
        try:
            ids = [int(r[0]) for r in db.execute("SELECT user_id FROM users").fetchall()]
            with sqlite3.connect(SUBSCRIBERS_DB) as subdb:
                ids = sorted(set(ids) | {int(r[0]) for r in subdb.execute("SELECT user_id FROM subscribers").fetchall()})
        except sqlite3.Error:
            ids = []
    sent = failed = 0
    status = await update.message.reply_text(f"⏳ Broadcasting to {len(ids)} verified users...")
    for uid in ids:
        try:
            await context.bot.send_message(uid, text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
            sent += 1
        except TelegramError:
            failed += 1
        await asyncio.sleep(0.04)
    await status.edit_text(f"📣 <b>Broadcast finished</b>\n\n✅ Sent: {sent}\n❌ Failed: {failed}\n👥 Database users: {len(ids)}", parse_mode=ParseMode.HTML)


async def recovery_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id) or context.user_data.get("admin_action") != "recovery":
        return
    doc = update.message.document
    if not doc or not doc.file_name.lower().endswith((".db", ".sqlite", ".sqlite3")):
        await update.message.reply_text("❌ Please upload a .db / .sqlite database file.")
        return
    context.user_data.pop("admin_action", None)
    temp = DATABASE_FILE + ".incoming"
    try:
        tg_file = await context.bot.get_file(doc.file_id)
        await tg_file.download_to_drive(temp)
        ok, reason = validate_giveaway_db(temp)
        if not ok:
            os.remove(temp)
            await update.message.reply_text("❌ Recovery cancelled: " + reason)
            return
        # Keep a safety copy before replacing the live results DB.
        if os.path.isfile(DATABASE_FILE):
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            safety = os.path.join(BACKUP_DIR, f"before_restore_{stamp}.db")
            import shutil
            shutil.copy2(DATABASE_FILE, safety)
        os.replace(temp, DATABASE_FILE)
        await update.message.reply_text("✅ <b>Data Recovery Successful!</b>\n\nWinner data has been restored. Restart is not required.", parse_mode=ParseMode.HTML)
    except Exception:
        log.exception("Recovery failed")
        if os.path.exists(temp): os.remove(temp)
        await update.message.reply_text("❌ Recovery failed. Check the hosting logs.")


async def unknown_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_admin(update.effective_user.id) and context.user_data.get("admin_action") == "recovery":
        await recovery_document(update, context)


def main():
    if not BOT_TOKEN or BOT_TOKEN == "PASTE_NEW_BOT_TOKEN_HERE":
        raise RuntimeError("Set BOT_TOKEN in config.py before starting the bot.")
    init_subscribers()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CallbackQueryHandler(callbacks))
    app.add_handler(MessageHandler(filters.Document.ALL & filters.ChatType.PRIVATE, recovery_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, admin_text))
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
