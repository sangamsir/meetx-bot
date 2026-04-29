"""Telegram command and message handlers."""
from __future__ import annotations

import logging
import os
from datetime import datetime

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    LabeledPrice,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.constants import ChatAction
from telegram.ext import (
    ApplicationHandlerStop,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)
from typing import Any

from . import matching, storage

logger = logging.getLogger(__name__)

ASK_NAME, ASK_GENDER, ASK_AGE, ASK_CITY, ASK_INTERESTS = range(5)

GENDER_KB = ReplyKeyboardMarkup(
    [[KeyboardButton("Male"), KeyboardButton("Female"), KeyboardButton("Other")]],
    resize_keyboard=True,
    one_time_keyboard=True,
)

# Persistent app-style menu shown to registered users.
BTN_FIND = "⚡ Find a Partner"
BTN_PREMIUM = "💎 Premium"
BTN_PROFILE = "👤 My Profile"
BTN_STOP = "🛑 End Chat"
BTN_NEXT = "➡️ Next Partner"
BTN_FILTERS = "⚙️ Filters"
BTN_RECHAT = "🔄 Rechat"
BTN_EDIT = "✏️ Edit Profile"
BTN_HELP = "❓ Help"

MAIN_KB = ReplyKeyboardMarkup(
    [
        [KeyboardButton(BTN_FIND)],
        [KeyboardButton(BTN_PREMIUM), KeyboardButton(BTN_PROFILE)],
        [KeyboardButton(BTN_STOP), KeyboardButton(BTN_NEXT)],
        [KeyboardButton(BTN_RECHAT), KeyboardButton(BTN_EDIT)],
        [KeyboardButton(BTN_FILTERS), KeyboardButton(BTN_HELP)],
    ],
    resize_keyboard=True,
    is_persistent=True,
)

MENU_BUTTON_LABELS = {
    BTN_FIND, BTN_PREMIUM, BTN_PROFILE, BTN_STOP, BTN_NEXT,
    BTN_FILTERS, BTN_RECHAT, BTN_EDIT, BTN_HELP,
}

# State key for in-progress profile edits (uses user_data)
PENDING_EDIT_KEY = "pending_edit_field"

# Editable profile fields and their validators
EDITABLE_FIELDS = {
    "name": ("Name", "Send your new name (1–40 characters)."),
    "gender": ("Gender", "Send your gender: Male, Female, or Other."),
    "age": ("Age", "Send your age (13–99)."),
    "city": ("City", "Send your city name."),
    "interests": ("Interests", "Send your interests, comma-separated."),
}


def _admin_ids() -> set[int]:
    raw = os.environ.get("ADMIN_USER_IDS", "")
    ids: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    return ids


def _is_admin(user_id: int) -> bool:
    return user_id in _admin_ids()


def _format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    parts = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if not parts or secs:
        parts.append(f"{secs}s")
    return " ".join(parts)


def _format_status(user: dict) -> str:
    if storage.has_active_premium(user):
        plan = storage.PLAN_DETAILS[user["premium_plan"]]["name"]
        expires = datetime.fromtimestamp(int(user["premium_expires_at"]))
        return (
            f"Subscription: {plan}\n"
            f"Renews / expires: {expires:%d %b %Y, %H:%M}"
        )
    remaining = storage.daily_remaining(user)
    return (
        f"Plan: Free\n"
        f"Daily chat time left: {_format_duration(remaining)} of 1h"
    )


def _plans_keyboard() -> InlineKeyboardMarkup:
    rows = []
    for key, plan in storage.PLAN_DETAILS.items():
        rows.append(
            [
                InlineKeyboardButton(
                    f"{plan['name']} — {plan['price']} ⭐",
                    callback_data=f"buy:{key}",
                )
            ]
        )
    return InlineKeyboardMarkup(rows)


# -------- Registration conversation --------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    if user is None or update.message is None:
        return ConversationHandler.END

    existing = storage.get_user(user.id)
    if existing and existing.get("name"):
        await update.message.reply_text(
            f"👋 Welcome back, *{existing['name']}*\n"
            f"━━━━━━━━━━━━━━━\n"
            f"{_format_status(existing)}\n\n"
            f"Tap *⚡ Find a Partner* below to start chatting, "
            f"or use /help to see all commands.",
            reply_markup=MAIN_KB,
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "🌟 *Welcome to Anonymous Chat*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Meet new people. Chat freely. Stay anonymous.\n\n"
        "✨ *What you get:*\n"
        "• Instant match with random strangers\n"
        "• 100% anonymous one-on-one chats\n"
        "• Photos, videos, voice — share anything\n"
        "• 1 free hour every day, refreshed daily\n\n"
        "💎 *With Premium:*\n"
        "• Unlimited chat time\n"
        "• Filter by gender (Pro) or city (VIP)\n"
        "• Priority matching, skip the queue\n\n"
        "Let's set up your profile first.\n"
        "👇 What name (or nickname) should we show?",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="Markdown",
    )
    return ASK_NAME


async def ask_gender(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None:
        return ASK_NAME
    name = update.message.text.strip()
    if not name or len(name) > 40:
        await update.message.reply_text("⚠️ Please enter a valid name (1–40 characters).")
        return ASK_NAME
    context.user_data["name"] = name
    await update.message.reply_text(
        f"Nice to meet you, *{name}* 👋\n\nNow, please select your gender 👇",
        reply_markup=GENDER_KB,
        parse_mode="Markdown",
    )
    return ASK_GENDER


async def ask_age(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None:
        return ASK_GENDER
    gender = update.message.text.strip()
    if gender not in {"Male", "Female", "Other"}:
        await update.message.reply_text(
            "Please choose one option: Male, Female, or Other.",
            reply_markup=GENDER_KB,
        )
        return ASK_GENDER
    context.user_data["gender"] = gender
    await update.message.reply_text(
        "Got it ✅\n\nHow old are you? (13–99)",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ASK_AGE


async def ask_city(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None:
        return ASK_AGE
    text = update.message.text.strip()
    if not text.isdigit() or not (13 <= int(text) <= 99):
        await update.message.reply_text("⚠️ Please enter a valid age between 13 and 99.")
        return ASK_AGE
    context.user_data["age"] = int(text)
    await update.message.reply_text("📍 Which city are you from?")
    return ASK_CITY


async def ask_interests(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None:
        return ASK_CITY
    city = update.message.text.strip()
    if not city or len(city) > 60:
        await update.message.reply_text("⚠️ Please enter a valid city name.")
        return ASK_CITY
    context.user_data["city"] = city
    await update.message.reply_text(
        "Last step ✨\n\n"
        "Tell me a few of your interests, separated by commas.\n"
        "_Example: music, gaming, books, movies, travel_",
        parse_mode="Markdown",
    )
    return ASK_INTERESTS


async def finish_registration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None or update.effective_user is None:
        return ASK_INTERESTS
    interests = update.message.text.strip()
    if not interests or len(interests) > 200:
        await update.message.reply_text("⚠️ Please enter your interests (up to 200 characters).")
        return ASK_INTERESTS

    profile = {
        "name": context.user_data["name"],
        "gender": context.user_data["gender"],
        "age": context.user_data["age"],
        "city": context.user_data["city"],
        "interests": interests,
    }
    user = storage.upsert_user(update.effective_user.id, profile)
    context.user_data.clear()

    await update.message.reply_text(
        f"🎉 *All set, {user['name']}!*\n"
        "━━━━━━━━━━━━━━━\n"
        "You're on the *Free plan* — 1 hour of chat per day, refreshed every 24 hours.\n\n"
        "💎 Upgrade anytime via *Premium* for unlimited chats and partner filters.\n\n"
        "👇 Tap *⚡ Find a Partner* to start your first chat!",
        reply_markup=MAIN_KB,
        parse_mode="Markdown",
    )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is not None:
        await update.message.reply_text(
            "Setup cancelled. Send /start when you're ready to try again.",
            reply_markup=ReplyKeyboardRemove(),
        )
    context.user_data.clear()
    return ConversationHandler.END


# -------- Profile / premium --------

async def profile_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.message is None:
        return
    user = storage.get_user(update.effective_user.id)
    if not user or not user.get("name"):
        await update.message.reply_text(
            "You haven't set up a profile yet.\n👉 Tap /start to begin.",
            reply_markup=MAIN_KB,
        )
        return
    filters_info = user.get("filters") or {}
    filter_lines = []
    if filters_info.get("gender"):
        filter_lines.append(f"  • Gender: {filters_info['gender']}")
    if filters_info.get("city"):
        filter_lines.append(f"  • City: {filters_info['city']}")
    filter_text = "\n".join(filter_lines) if filter_lines else "  • None set"

    # Edit allowance line
    if storage.has_active_premium(user) and user.get("premium_plan") == "vip":
        edit_line = "💎 VIP — unlimited free edits"
    else:
        free_left = storage.free_edits_remaining(user)
        paid = int(user.get("paid_edits", 0))
        edit_line = f"Free: {free_left}/{storage.FREE_PROFILE_EDITS}"
        if paid:
            edit_line += f" • Paid credits: {paid}"

    # Rating line
    avg, count = storage.get_rating(user)
    if storage.can_view_rating(user):
        rating_line = f"⭐ {avg:.2f} / 5 from {count} rating(s)" if count else "_No ratings yet_"
    else:
        rating_line = f"🔒 Locked — unlock with /rating ({storage.RATING_VIEW_PRICE_STARS} ⭐)"

    await update.message.reply_text(
        "👤 *Your Profile*\n"
        "━━━━━━━━━━━━━━━\n"
        f"*Name:* {user['name']}\n"
        f"*Gender:* {user['gender']}\n"
        f"*Age:* {user['age']}\n"
        f"*City:* {user['city']}\n"
        f"*Interests:* {user['interests']}\n\n"
        "📊 *Status*\n"
        f"{_format_status(user)}\n"
        f"Total chats: {user.get('total_chats', 0)}\n\n"
        "⭐ *Your Rating*\n"
        f"  {rating_line}\n\n"
        "✏️ *Profile Edits*\n"
        f"  {edit_line}\n\n"
        "🎯 *Active filters*\n"
        f"{filter_text}\n\n"
        "_Edit fields with ✏️ Edit Profile_\n"
        "_Set filters with /setfilter (Pro & VIP only)_",
        reply_markup=MAIN_KB,
        parse_mode="Markdown",
    )


async def premium_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    lines = [
        "💎 *Premium Plans*",
        "━━━━━━━━━━━━━━━",
        "_All plans valid for 30 days. Pay securely with Telegram Stars ⭐ —_",
        "_no external payment app needed._",
        "",
    ]
    plan_emoji = {"basic": "🥉", "pro": "🥈", "vip": "🥇"}
    for key, plan in storage.PLAN_DETAILS.items():
        emoji = plan_emoji.get(key, "•")
        lines.append(f"{emoji} *{plan['name']}* — *{plan['price']} ⭐*")
        for feat in plan["features"]:
            lines.append(f"   ✓ {feat}")
        lines.append("")
    lines.append("👇 *Tap a plan below to purchase instantly*")
    await update.message.reply_text(
        "\n".join(lines),
        reply_markup=_plans_keyboard(),
        parse_mode="Markdown",
    )


async def _send_invoice(context: ContextTypes.DEFAULT_TYPE, chat_id: int, plan_key: str) -> None:
    plan = storage.PLAN_DETAILS[plan_key]
    await context.bot.send_invoice(
        chat_id=chat_id,
        title=f"{plan['name']} Plan — 30 days",
        description=" • ".join(plan["features"]),
        payload=f"premium:{plan_key}",
        currency="XTR",
        prices=[LabeledPrice(label=f"{plan['name']} (30 days)", amount=int(plan["price"]))],
    )


async def buy_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    if not context.args:
        await update.message.reply_text(
            "Choose a plan to purchase with Telegram Stars:",
            reply_markup=_plans_keyboard(),
        )
        return
    plan = context.args[0].lower()
    if plan not in storage.PLAN_DETAILS:
        await update.message.reply_text(
            "Unknown plan. Choose one of: basic, pro, vip.",
            reply_markup=_plans_keyboard(),
        )
        return
    await _send_invoice(context, update.effective_user.id, plan)


async def buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None or update.effective_user is None:
        return
    await query.answer()
    _, _, plan = query.data.partition(":")
    if plan not in storage.PLAN_DETAILS:
        return
    await _send_invoice(context, update.effective_user.id, plan)


def _is_valid_payload(payload: str) -> bool:
    parts = payload.split(":")
    if not parts:
        return False
    head = parts[0]
    if head == "premium" and len(parts) >= 2 and parts[1] in storage.PLAN_DETAILS:
        return True
    if head == "edit_credit":
        return True
    if head == "rating_view":
        return True
    if head == "rechat" and len(parts) >= 2 and parts[1].lstrip("-").isdigit():
        return True
    return False


async def precheckout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.pre_checkout_query
    if query is None:
        return
    if _is_valid_payload(query.invoice_payload or ""):
        await query.answer(ok=True)
    else:
        await query.answer(ok=False, error_message="Invalid purchase. Please try again.")


async def successful_payment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.message.successful_payment is None or update.effective_user is None:
        return
    payment = update.message.successful_payment
    payload = payment.invoice_payload or ""
    parts = payload.split(":")
    user_id = update.effective_user.id
    head = parts[0] if parts else ""
    txn = payment.telegram_payment_charge_id

    if head == "premium" and len(parts) >= 2 and parts[1] in storage.PLAN_DETAILS:
        plan = parts[1]
        result = storage.grant_premium(user_id, plan)
        if not result:
            await update.message.reply_text(
                "Payment received, but activation failed.\n"
                f"Please contact support with this ID: `{txn}`",
                parse_mode="Markdown",
            )
            return
        plan_name = storage.PLAN_DETAILS[plan]["name"]
        expires = datetime.fromtimestamp(int(result["premium_expires_at"]))
        await update.message.reply_text(
            f"🎉 *Payment successful — Thank you!*\n"
            "━━━━━━━━━━━━━━━━\n"
            f"Your *{plan_name}* subscription is now active.\n"
            f"📅 Valid until: *{expires:%d %b %Y, %H:%M}*\n\n"
            f"_Transaction ID:_ `{txn}`",
            parse_mode="Markdown",
            reply_markup=MAIN_KB,
        )
        logger.info("Premium activated for user %s: plan=%s, charge=%s", user_id, plan, txn)
        return

    if head == "edit_credit":
        storage.grant_paid_edit(user_id, 1)
        await update.message.reply_text(
            "✅ *1 profile edit credit added.*\n\n"
            "Tap *✏️ Edit Profile* to use it now.\n"
            f"_Transaction ID:_ `{txn}`",
            parse_mode="Markdown",
            reply_markup=MAIN_KB,
        )
        logger.info("Edit credit granted to user %s, charge=%s", user_id, txn)
        return

    if head == "rating_view":
        storage.unlock_rating_view(user_id)
        await update.message.reply_text(
            "🔓 *Rating view unlocked.*\n\n"
            "You can now see your rating any time from *👤 My Profile*.\n"
            f"_Transaction ID:_ `{txn}`",
            parse_mode="Markdown",
            reply_markup=MAIN_KB,
        )
        logger.info("Rating view unlocked for user %s, charge=%s", user_id, txn)
        return

    if head == "rechat" and len(parts) >= 2:
        try:
            partner_id = int(parts[1])
        except ValueError:
            return
        storage.grant_rechat_credit(user_id, partner_id)
        await update.message.reply_text(
            "✅ *Rechat credit unlocked for this partner.*\n\n"
            "I'll send them an invitation now.",
            parse_mode="Markdown",
            reply_markup=MAIN_KB,
        )
        logger.info("Rechat credit granted to %s for partner %s, charge=%s", user_id, partner_id, txn)
        await _send_rechat_invitation(context, user_id, partner_id)
        return


async def setfilter_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    user = storage.get_user(update.effective_user.id)
    if not user:
        await update.message.reply_text("Send /start to set up your profile first.")
        return
    if not storage.has_active_premium(user) or user.get("premium_plan") not in {"pro", "vip"}:
        await update.message.reply_text(
            "Filters are available on the Pro and VIP plans. See /premium for details."
        )
        return
    if len(context.args) < 1:
        await update.message.reply_text(
            "Usage:\n"
            "/setfilter gender Male|Female|Other\n"
            "/setfilter city <city name>     (VIP only)\n"
            "/setfilter clear"
        )
        return
    key = context.args[0].lower()
    value = " ".join(context.args[1:]).strip()
    filters_obj = dict(user.get("filters") or {})
    if key == "clear":
        filters_obj = {}
        await update.message.reply_text("All filters cleared.")
    elif key == "gender":
        if value not in {"Male", "Female", "Other"}:
            await update.message.reply_text("Gender must be Male, Female, or Other.")
            return
        filters_obj["gender"] = value
        await update.message.reply_text(f"Gender filter set to {value}.")
    elif key == "city":
        if user.get("premium_plan") != "vip":
            await update.message.reply_text("The city filter is available on the VIP plan only.")
            return
        if not value:
            await update.message.reply_text("Please specify a city name.")
            return
        filters_obj["city"] = value
        await update.message.reply_text(f"City filter set to {value}.")
    else:
        await update.message.reply_text("Unknown filter. Use: gender, city, or clear.")
        return
    storage.update_user(update.effective_user.id, filters=filters_obj)


# -------- Profile edit --------

def _edit_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("Name", callback_data="edit:name"),
         InlineKeyboardButton("Gender", callback_data="edit:gender")],
        [InlineKeyboardButton("Age", callback_data="edit:age"),
         InlineKeyboardButton("City", callback_data="edit:city")],
        [InlineKeyboardButton("Interests", callback_data="edit:interests")],
    ]
    return InlineKeyboardMarkup(rows)


def _edit_status_text(user: dict) -> str:
    if storage.has_active_premium(user) and user.get("premium_plan") == "vip":
        return "💎 *VIP* — unlimited free edits."
    free_left = storage.free_edits_remaining(user)
    paid = int(user.get("paid_edits", 0))
    lines = [f"Free edits left: *{free_left}* of {storage.FREE_PROFILE_EDITS}"]
    if paid:
        lines.append(f"Paid edit credits: *{paid}*")
    if free_left == 0 and paid == 0:
        lines.append(f"_No edits available — buy 1 edit for {storage.EDIT_PRICE_STARS} ⭐_")
    return "\n".join(lines)


async def edit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    user = storage.get_user(update.effective_user.id)
    if not user or not user.get("name"):
        await update.message.reply_text(
            "You haven't set up a profile yet.\n👉 Tap /start to begin.",
            reply_markup=MAIN_KB,
        )
        return
    context.user_data.pop(PENDING_EDIT_KEY, None)
    text = (
        "✏️ *Edit Profile*\n"
        "━━━━━━━━━━━━━━━\n"
        f"{_edit_status_text(user)}\n\n"
        "Pick a field to edit:"
    )
    rows = []
    rows += _edit_keyboard().inline_keyboard
    allowed, _ = storage.can_edit_profile(user)
    if not allowed:
        rows.append([InlineKeyboardButton(
            f"💳 Buy 1 edit — {storage.EDIT_PRICE_STARS} ⭐",
            callback_data="buyedit",
        )])
    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode="Markdown",
    )


async def edit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None or update.effective_user is None:
        return
    await query.answer()
    _, _, field = query.data.partition(":")
    if field not in EDITABLE_FIELDS:
        return
    user = storage.get_user(update.effective_user.id)
    if not user:
        return
    allowed, reason = storage.can_edit_profile(user)
    if not allowed:
        await context.bot.send_message(
            update.effective_user.id,
            f"⚠️ *No edits left.*\n\nBuy 1 profile edit for *{storage.EDIT_PRICE_STARS} ⭐* below.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    f"💳 Buy 1 edit — {storage.EDIT_PRICE_STARS} ⭐",
                    callback_data="buyedit",
                )
            ]]),
        )
        return
    context.user_data[PENDING_EDIT_KEY] = field
    label, prompt = EDITABLE_FIELDS[field]
    if field == "gender":
        await context.bot.send_message(
            update.effective_user.id,
            f"✏️ *Editing {label}*\n\n{prompt}",
            reply_markup=GENDER_KB,
            parse_mode="Markdown",
        )
    else:
        await context.bot.send_message(
            update.effective_user.id,
            f"✏️ *Editing {label}*\n\n{prompt}\n_Send /cancel to abort._",
            parse_mode="Markdown",
        )


async def buy_edit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or update.effective_user is None:
        return
    await query.answer()
    await context.bot.send_invoice(
        chat_id=update.effective_user.id,
        title="1 Profile Edit",
        description="One-time credit to edit any field of your profile.",
        payload="edit_credit",
        currency="XTR",
        prices=[LabeledPrice(label="1 Profile Edit", amount=storage.EDIT_PRICE_STARS)],
    )


async def edit_input_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the next text message after the user picked a field to edit."""
    if update.message is None or update.message.text is None or update.effective_user is None:
        return
    field = context.user_data.get(PENDING_EDIT_KEY)
    if not field:
        return  # Nothing pending — let other handlers process this message.

    text = update.message.text.strip()

    # Allow the user to bail out with a menu button without losing data.
    if text in MENU_BUTTON_LABELS:
        context.user_data.pop(PENDING_EDIT_KEY, None)
        await update.message.reply_text(
            "Edit cancelled — no credit used.",
            reply_markup=MAIN_KB,
        )
        return  # Let the menu router handle the button next.

    new_value: Any = None
    if field == "name":
        if not text or len(text) > 40:
            await update.message.reply_text("⚠️ Name must be 1–40 characters. Try again or send /cancel.")
            raise ApplicationHandlerStop
        new_value = text
    elif field == "gender":
        if text not in {"Male", "Female", "Other"}:
            await update.message.reply_text(
                "Please choose Male, Female, or Other.", reply_markup=GENDER_KB,
            )
            raise ApplicationHandlerStop
        new_value = text
    elif field == "age":
        if not text.isdigit() or not (13 <= int(text) <= 99):
            await update.message.reply_text("⚠️ Age must be a number between 13 and 99.")
            raise ApplicationHandlerStop
        new_value = int(text)
    elif field == "city":
        if not text or len(text) > 60:
            await update.message.reply_text("⚠️ Please enter a valid city name (max 60 characters).")
            raise ApplicationHandlerStop
        new_value = text
    elif field == "interests":
        if not text or len(text) > 200:
            await update.message.reply_text("⚠️ Please enter your interests (max 200 characters).")
            raise ApplicationHandlerStop
        new_value = text
    else:
        context.user_data.pop(PENDING_EDIT_KEY, None)
        return

    # Atomically consume the edit allowance.
    source = storage.consume_edit(update.effective_user.id)
    if source is None:
        context.user_data.pop(PENDING_EDIT_KEY, None)
        await update.message.reply_text(
            "⚠️ No edits available anymore. Buy one with ✏️ Edit Profile.",
            reply_markup=MAIN_KB,
        )
        raise ApplicationHandlerStop

    storage.update_user(update.effective_user.id, **{field: new_value})
    context.user_data.pop(PENDING_EDIT_KEY, None)

    label = EDITABLE_FIELDS[field][0]
    source_note = {
        "vip": "_(VIP — free)_",
        "paid": "_(used 1 paid credit)_",
        "free": "_(free edit used)_",
    }.get(source, "")
    await update.message.reply_text(
        f"✅ *{label} updated.* {source_note}",
        parse_mode="Markdown",
        reply_markup=MAIN_KB,
    )
    raise ApplicationHandlerStop


async def edit_cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    if context.user_data.pop(PENDING_EDIT_KEY, None):
        await update.message.reply_text("Edit cancelled — no credit used.", reply_markup=MAIN_KB)
    else:
        await update.message.reply_text("Nothing to cancel.", reply_markup=MAIN_KB)


# -------- Rating --------

def _rating_keyboard(partner_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{n}⭐", callback_data=f"rate:{partner_id}:{n}") for n in (1, 2, 3, 4, 5)],
        [InlineKeyboardButton("Skip", callback_data=f"rate:{partner_id}:0")],
    ])


async def rating_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None or update.effective_user is None:
        return
    parts = query.data.split(":")
    if len(parts) != 3:
        await query.answer()
        return
    try:
        partner_id = int(parts[1])
        score = int(parts[2])
    except ValueError:
        await query.answer()
        return
    if score == 0:
        await query.answer("Skipped.")
        try:
            await query.edit_message_text("⏭️ You skipped the rating.")
        except Exception:
            pass
        return
    if not (1 <= score <= 5):
        await query.answer()
        return
    storage.record_rating(partner_id, score)
    await query.answer("Thanks for rating!")
    try:
        await query.edit_message_text(f"✅ You rated your partner *{score}⭐*. Thanks!", parse_mode="Markdown")
    except Exception:
        pass


async def rating_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    user = storage.get_user(update.effective_user.id)
    if not user:
        await update.message.reply_text("Send /start to set up your profile first.", reply_markup=MAIN_KB)
        return
    avg, count = storage.get_rating(user)
    if storage.can_view_rating(user):
        if count == 0:
            text = (
                "⭐ *Your Rating*\n"
                "━━━━━━━━━━━━━━━\n"
                "_No one has rated you yet._\n"
                "Have more chats to receive ratings!"
            )
        else:
            text = (
                "⭐ *Your Rating*\n"
                "━━━━━━━━━━━━━━━\n"
                f"*{avg:.2f} / 5* from *{count}* rating(s)"
            )
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=MAIN_KB)
        return
    text = (
        "🔒 *Your Rating is Locked*\n"
        "━━━━━━━━━━━━━━━\n"
        "Other users have rated you, but you can't see it yet.\n\n"
        f"Unlock your rating one-time for *{storage.RATING_VIEW_PRICE_STARS} ⭐*."
    )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            f"🔓 Unlock — {storage.RATING_VIEW_PRICE_STARS} ⭐",
            callback_data="buyrating",
        )
    ]])
    await update.message.reply_text(text, reply_markup=kb, parse_mode="Markdown")


async def buy_rating_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or update.effective_user is None:
        return
    await query.answer()
    await context.bot.send_invoice(
        chat_id=update.effective_user.id,
        title="Unlock Rating View",
        description="One-time unlock to see your average rating from other users.",
        payload="rating_view",
        currency="XTR",
        prices=[LabeledPrice(label="Rating View Unlock", amount=storage.RATING_VIEW_PRICE_STARS)],
    )


# -------- Rechat --------

async def rechat_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    user = storage.get_user(update.effective_user.id)
    if not user:
        await update.message.reply_text("Send /start to set up your profile first.", reply_markup=MAIN_KB)
        return
    recents = storage.get_recent_partners(user)
    if not recents:
        await update.message.reply_text(
            "🔄 *Rechat*\n"
            "━━━━━━━━━━━━━━━\n"
            "_No recent partners yet._\n"
            "Have a chat first, then you can reconnect from here.",
            reply_markup=MAIN_KB,
            parse_mode="Markdown",
        )
        return
    free_for_vip = storage.can_rechat_free(user)
    rows = []
    for p in recents:
        pid = int(p.get("id", 0))
        name = p.get("name", "Stranger")
        suffix = "" if free_for_vip else (
            " ✓" if storage.has_rechat_credit(user, pid) else f" • {storage.RECHAT_PRICE_STARS}⭐"
        )
        rows.append([InlineKeyboardButton(f"{name}{suffix}", callback_data=f"rechat:{pid}")])
    note = (
        "_💎 VIP — free unlimited rechats._" if free_for_vip
        else f"_Rechat costs {storage.RECHAT_PRICE_STARS} ⭐ per partner. Upgrade to VIP for unlimited free rechats._"
    )
    await update.message.reply_text(
        "🔄 *Rechat — Recent Partners*\n"
        "━━━━━━━━━━━━━━━\n"
        f"{note}\n\nTap a partner to reconnect:",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode="Markdown",
    )


async def rechat_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None or update.effective_user is None:
        return
    await query.answer()
    _, _, sid = query.data.partition(":")
    try:
        partner_id = int(sid)
    except ValueError:
        return
    user_id = update.effective_user.id
    user = storage.get_user(user_id)
    if not user:
        return

    if storage.can_rechat_free(user) or storage.has_rechat_credit(user, partner_id):
        if not storage.can_rechat_free(user):
            storage.consume_rechat_credit(user_id, partner_id)
        await _send_rechat_invitation(context, user_id, partner_id)
        return

    await context.bot.send_invoice(
        chat_id=user_id,
        title="Rechat with previous partner",
        description=f"One reconnect with this user. Costs {storage.RECHAT_PRICE_STARS} Stars.",
        payload=f"rechat:{partner_id}",
        currency="XTR",
        prices=[LabeledPrice(label="1 Rechat", amount=storage.RECHAT_PRICE_STARS)],
    )


async def _send_rechat_invitation(context: ContextTypes.DEFAULT_TYPE, requester_id: int, partner_id: int) -> None:
    requester = storage.get_user(requester_id) or {}
    partner = storage.get_user(partner_id)
    if not partner:
        try:
            await context.bot.send_message(
                requester_id,
                "⚠️ That user is no longer available.",
                reply_markup=MAIN_KB,
            )
        except Exception:
            pass
        return
    if matching.is_in_chat(partner_id):
        await context.bot.send_message(
            requester_id,
            "⏳ Your partner is currently in another chat. Try again later.\n"
            "_Your rechat credit is saved — it stays until used._",
            parse_mode="Markdown",
            reply_markup=MAIN_KB,
        )
        # Re-grant the credit since we couldn't deliver
        if not storage.can_rechat_free(requester):
            storage.grant_rechat_credit(requester_id, partner_id)
        return

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Accept", callback_data=f"racc:{requester_id}"),
         InlineKeyboardButton("❌ Decline", callback_data=f"rdec:{requester_id}")],
    ])
    name = requester.get("name", "Someone")
    try:
        await context.bot.send_message(
            partner_id,
            f"🔄 *Rechat invitation*\n"
            f"━━━━━━━━━━━━━━━\n"
            f"*{name}* (a previous partner) would like to chat with you again.\n"
            f"Accept to reconnect anonymously.",
            reply_markup=kb,
            parse_mode="Markdown",
        )
        await context.bot.send_message(
            requester_id,
            "📨 Invitation sent. I'll let you know when they respond.",
            reply_markup=MAIN_KB,
        )
    except Exception:
        await context.bot.send_message(
            requester_id,
            "⚠️ Couldn't deliver the invitation.\n_Your credit is saved._",
            parse_mode="Markdown",
            reply_markup=MAIN_KB,
        )
        if not storage.can_rechat_free(requester):
            storage.grant_rechat_credit(requester_id, partner_id)


async def rechat_response_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None or update.effective_user is None:
        return
    await query.answer()
    action, _, sid = query.data.partition(":")
    try:
        requester_id = int(sid)
    except ValueError:
        return
    partner_id = update.effective_user.id

    if action == "rdec":
        try:
            await query.edit_message_text("❌ You declined the rechat invitation.")
        except Exception:
            pass
        try:
            await context.bot.send_message(
                requester_id,
                "❌ Your rechat invitation was declined.",
                reply_markup=MAIN_KB,
            )
        except Exception:
            pass
        return

    # Accept
    if not matching.is_free(partner_id):
        try:
            await query.edit_message_text("⚠️ You're already in a chat. End it first, then accept.")
        except Exception:
            pass
        return
    if not matching.is_free(requester_id):
        try:
            await query.edit_message_text("⚠️ The other user is no longer available.")
        except Exception:
            pass
        try:
            await context.bot.send_message(
                requester_id,
                "⚠️ Couldn't connect — you were busy. Try /rechat again.",
                reply_markup=MAIN_KB,
            )
        except Exception:
            pass
        return

    if not matching.force_pair(requester_id, partner_id):
        try:
            await query.edit_message_text("⚠️ Couldn't connect right now. Try again in a moment.")
        except Exception:
            pass
        return

    try:
        await query.edit_message_text("🎉 Connected!")
    except Exception:
        pass
    requester = storage.get_user(requester_id) or {}
    partner = storage.get_user(partner_id) or {}
    await _notify_match(context, requester_id, requester, partner_id, partner)


# -------- Post-chat hook (rating + recent partner) --------

async def _post_chat_cleanup(context: ContextTypes.DEFAULT_TYPE, user_a: int, user_b: int) -> None:
    """Record each as a recent partner of the other and prompt for ratings."""
    a = storage.get_user(user_a) or {}
    b = storage.get_user(user_b) or {}
    a_name = a.get("name", "Stranger")
    b_name = b.get("name", "Stranger")
    storage.add_recent_partner(user_a, user_b, b_name)
    storage.add_recent_partner(user_b, user_a, a_name)
    for me, them, them_name in ((user_a, user_b, b_name), (user_b, user_a, a_name)):
        try:
            await context.bot.send_message(
                me,
                f"⭐ *How was your chat with {them_name}?*\nTap to rate (anonymous):",
                reply_markup=_rating_keyboard(them),
                parse_mode="Markdown",
            )
        except Exception:
            pass


# -------- Admin --------

async def grant_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    if not _is_admin(update.effective_user.id):
        await update.message.reply_text("This command is restricted to administrators.")
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /grant <user_id> <basic|pro|vip>")
        return
    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("user_id must be a number.")
        return
    plan = context.args[1].lower()
    result = storage.grant_premium(target_id, plan)
    if not result:
        await update.message.reply_text("Failed. The user may not exist, or the plan name is invalid.")
        return
    await update.message.reply_text(f"Activated {plan.upper()} for user {target_id}.")
    try:
        await context.bot.send_message(
            target_id,
            f"Your {storage.PLAN_DETAILS[plan]['name']} subscription is now active for 30 days. "
            "Thank you for upgrading.",
        )
    except Exception:
        logger.warning("Could not notify user %s about premium grant", target_id)


async def revoke_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    if not _is_admin(update.effective_user.id):
        await update.message.reply_text("This command is restricted to administrators.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /revoke <user_id>")
        return
    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("user_id must be a number.")
        return
    storage.revoke_premium(target_id)
    await update.message.reply_text(f"Revoked the premium subscription for user {target_id}.")


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    if not _is_admin(update.effective_user.id):
        await update.message.reply_text("This command is restricted to administrators.")
        return
    users = storage.all_users()
    premium = sum(1 for u in users.values() if storage.has_active_premium(u))
    by_plan: dict[str, int] = {"basic": 0, "pro": 0, "vip": 0}
    for u in users.values():
        if storage.has_active_premium(u):
            by_plan[u["premium_plan"]] = by_plan.get(u["premium_plan"], 0) + 1
    await update.message.reply_text(
        "Bot statistics\n"
        "──────────────\n"
        f"Total users: {len(users)}\n"
        f"Active subscriptions: {premium}\n"
        f"  • Basic: {by_plan.get('basic', 0)}\n"
        f"  • Pro:   {by_plan.get('pro', 0)}\n"
        f"  • VIP:   {by_plan.get('vip', 0)}\n\n"
        f"Your Telegram ID: {update.effective_user.id}"
    )


# -------- Chat: find / stop / next --------

async def _start_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    user_id = update.effective_user.id
    user = storage.get_user(user_id)
    if not user or not user.get("name"):
        await update.message.reply_text(
            "Please set up your profile first.\n👉 Tap /start to begin.",
            reply_markup=MAIN_KB,
        )
        return
    if matching.is_in_chat(user_id):
        await update.message.reply_text(
            "💬 You're already in a chat.\n\n"
            "Use *🛑 End Chat* to stop, or *➡️ Next Partner* to switch.",
            reply_markup=MAIN_KB,
            parse_mode="Markdown",
        )
        return
    if not storage.has_active_premium(user) and storage.daily_remaining(user) <= 0:
        await update.message.reply_text(
            "⏰ *Daily free time over*\n"
            "━━━━━━━━━━━━━━━\n"
            "You've used your 1 free hour for today.\n"
            "Your time refreshes in 24 hours.\n\n"
            "💎 _Or upgrade to Premium for unlimited chats — tap 💎 Premium below._",
            reply_markup=MAIN_KB,
            parse_mode="Markdown",
        )
        return

    matched = matching.find_match(user_id)
    if matched is None:
        await update.message.reply_text(
            "🔍 *Searching for a partner...*\n\n"
            "I'll connect you as soon as someone is available.\n"
            "_Tap 🛑 End Chat to cancel the search._",
            reply_markup=MAIN_KB,
            parse_mode="Markdown",
        )
        return

    partner = storage.get_user(matched)
    await _notify_match(context, user_id, user, matched, partner or {})


async def _notify_match(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    user: dict,
    partner_id: int,
    partner: dict,
) -> None:
    def card(p: dict) -> str:
        return (
            f"   👥 *Gender:* {p.get('gender', '—')}\n"
            f"   🎂 *Age:* {p.get('age', '—')}\n"
            f"   📍 *City:* {p.get('city', '—')}\n"
            f"   ✨ *Interests:* {p.get('interests', '—')}"
        )

    intro_for_user = (
        "🎉 *Connected! Say hello* 👋\n"
        "━━━━━━━━━━━━━━━━\n"
        "*Your partner:*\n"
        f"{card(partner)}\n\n"
        "_Your messages are forwarded anonymously._\n"
        "_Tap 🛑 End Chat to stop, or ➡️ Next Partner to switch._"
    )
    intro_for_partner = (
        "🎉 *Connected! Say hello* 👋\n"
        "━━━━━━━━━━━━━━━━\n"
        "*Your partner:*\n"
        f"{card(user)}\n\n"
        "_Your messages are forwarded anonymously._\n"
        "_Tap 🛑 End Chat to stop, or ➡️ Next Partner to switch._"
    )
    try:
        await context.bot.send_message(user_id, intro_for_user, parse_mode="Markdown", reply_markup=MAIN_KB)
    except Exception:
        logger.exception("Failed to message user %s", user_id)
    try:
        await context.bot.send_message(partner_id, intro_for_partner, parse_mode="Markdown", reply_markup=MAIN_KB)
    except Exception:
        logger.exception("Failed to message partner %s", partner_id)


async def find_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _start_search(update, context)


async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    user_id = update.effective_user.id
    if matching.cancel_waiting(user_id):
        await update.message.reply_text(
            "✅ Search cancelled.",
            reply_markup=MAIN_KB,
        )
        return
    partner, _ = matching.end_chat(user_id)
    if partner is None:
        await update.message.reply_text(
            "You're not in a chat right now.\n👉 Tap *⚡ Find a Partner* to start one.",
            reply_markup=MAIN_KB,
            parse_mode="Markdown",
        )
        return
    await update.message.reply_text(
        "👋 *Chat ended.*\n\nTap *⚡ Find a Partner* when you're ready to meet someone new.",
        reply_markup=MAIN_KB,
        parse_mode="Markdown",
    )
    try:
        await context.bot.send_message(
            partner,
            "👋 Your partner has left the chat.\n\nTap *⚡ Find a Partner* to meet someone new.",
            reply_markup=MAIN_KB,
            parse_mode="Markdown",
        )
    except Exception:
        logger.warning("Could not notify partner %s", partner)
    await _post_chat_cleanup(context, user_id, partner)


async def next_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    user_id = update.effective_user.id
    matching.cancel_waiting(user_id)
    partner, _ = matching.end_chat(user_id)
    if partner is not None:
        try:
            await context.bot.send_message(
                partner,
                "👋 Your partner has moved on.\n\nTap *⚡ Find a Partner* to meet someone new.",
                reply_markup=MAIN_KB,
                parse_mode="Markdown",
            )
        except Exception:
            logger.warning("Could not notify partner %s", partner)
        await _post_chat_cleanup(context, user_id, partner)
    await _start_search(update, context)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    await update.message.reply_text(
        "❓ *Help — All Commands*\n"
        "━━━━━━━━━━━━━━━\n"
        "🚀 /start — restart bot / update profile\n"
        "⚡ /find — find an anonymous partner\n"
        "🛑 /stop — end the current chat\n"
        "➡️ /next — switch to a new partner\n"
        "👤 /profile — view your profile & usage\n"
        "✏️ /edit — edit your profile fields\n"
        "🔄 /rechat — reconnect with a recent partner\n"
        "⭐ /rating — view your rating\n"
        "💎 /premium — see subscription plans\n"
        "⭐ /buy — purchase a plan with Stars\n"
        "⚙️ /setfilter — match filters (Pro & VIP)\n"
        "❓ /help — show this menu\n\n"
        f"💡 *Pricing:* edit *{storage.EDIT_PRICE_STARS}⭐* • rating unlock *{storage.RATING_VIEW_PRICE_STARS}⭐* • rechat *{storage.RECHAT_PRICE_STARS}⭐*\n"
        "_VIP unlocks unlimited edits & rechats._",
        reply_markup=MAIN_KB,
        parse_mode="Markdown",
    )


async def menu_button_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route presses of the persistent reply-keyboard buttons to their command logic."""
    if update.message is None or update.message.text is None:
        return
    text = update.message.text
    if text == BTN_FIND:
        await find_cmd(update, context)
    elif text == BTN_PREMIUM:
        await premium_cmd(update, context)
    elif text == BTN_PROFILE:
        await profile_cmd(update, context)
    elif text == BTN_STOP:
        await stop_cmd(update, context)
    elif text == BTN_NEXT:
        await next_cmd(update, context)
    elif text == BTN_FILTERS:
        await update.message.reply_text(
            "⚙️ *Match Filters*\n"
            "━━━━━━━━━━━━━━━\n"
            "_Available on Pro and VIP plans only._\n\n"
            "• `/setfilter gender Male` — match only men\n"
            "• `/setfilter gender Female` — match only women\n"
            "• `/setfilter gender Other` — match Other gender\n"
            "• `/setfilter city <name>` — same city only *(VIP)*\n"
            "• `/setfilter clear` — remove all filters",
            reply_markup=MAIN_KB,
            parse_mode="Markdown",
        )
    elif text == BTN_RECHAT:
        await rechat_cmd(update, context)
    elif text == BTN_EDIT:
        await edit_cmd(update, context)
    elif text == BTN_HELP:
        await help_cmd(update, context)


# -------- Relay messages --------

_FORWARDABLE = (
    filters.TEXT
    | filters.PHOTO
    | filters.VIDEO
    | filters.VOICE
    | filters.AUDIO
    | filters.Document.ALL
    | filters.Sticker.ALL
    | filters.ANIMATION
    | filters.VIDEO_NOTE
)


async def relay(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    user_id = update.effective_user.id
    partner = matching.get_partner(user_id)
    if partner is None:
        return  # Not in a chat — silently ignore.

    user = storage.get_user(user_id)
    if user and not storage.has_active_premium(user):
        used_so_far = (
            int(user.get("trial_used_today_seconds", 0))
            + matching.session_seconds(user_id)
        )
        if used_so_far >= storage.DAILY_FREE_SECONDS:
            await update.message.reply_text(
                "⏰ *Daily 1-hour free limit reached*\n"
                "━━━━━━━━━━━━━━━━\n"
                "Ending this chat.\n"
                "Your free time refreshes in 24 hours.\n\n"
                "💎 _Want unlimited chats? Tap 💎 Premium below._",
                parse_mode="Markdown",
                reply_markup=MAIN_KB,
            )
            other, _ = matching.end_chat(user_id)
            if other is not None:
                try:
                    await context.bot.send_message(
                        other,
                        "👋 Your partner's daily free time has ended.\n\nTap *⚡ Find a Partner* to meet someone new.",
                        parse_mode="Markdown",
                        reply_markup=MAIN_KB,
                    )
                except Exception:
                    pass
                await _post_chat_cleanup(context, user_id, other)
            return

    try:
        await context.bot.send_chat_action(partner, ChatAction.TYPING)
        await update.message.copy(chat_id=partner)
    except Exception:
        logger.exception("Failed to relay message to %s", partner)
        await update.message.reply_text(
            "Couldn't deliver your last message. The chat may have ended."
        )


async def trial_watchdog(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Periodically end chats whose free user has run out of daily allowance."""
    pairs_snapshot = list(matching._pairs.items())
    seen: set[int] = set()
    for user_id, partner_id in pairs_snapshot:
        if user_id in seen:
            continue
        seen.add(user_id)
        seen.add(partner_id)
        for uid in (user_id, partner_id):
            user = storage.get_user(uid)
            if not user or storage.has_active_premium(user):
                continue
            used_so_far = (
                int(user.get("trial_used_today_seconds", 0))
                + matching.session_seconds(uid)
            )
            if used_so_far >= storage.DAILY_FREE_SECONDS:
                other, _ = matching.end_chat(uid)
                try:
                    await context.bot.send_message(
                        uid,
                        "⏰ *Daily 1-hour free limit reached*\n\n"
                        "Your free time refreshes in 24 hours.\n"
                        "💎 _Tap 💎 Premium for unlimited chats._",
                        parse_mode="Markdown",
                        reply_markup=MAIN_KB,
                    )
                except Exception:
                    pass
                if other is not None:
                    try:
                        await context.bot.send_message(
                            other,
                            "👋 Your partner's daily free time has ended.\n\nTap *⚡ Find a Partner* to meet someone new.",
                            parse_mode="Markdown",
                            reply_markup=MAIN_KB,
                        )
                    except Exception:
                        pass
                    await _post_chat_cleanup(context, uid, other)
                break


def build_registration_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_gender)],
            ASK_GENDER: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_age)],
            ASK_AGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_city)],
            ASK_CITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_interests)],
            ASK_INTERESTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, finish_registration)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        name="registration",
        persistent=False,
    )


def build_relay_filter():
    # Forward everything except commands and our own menu button labels.
    return _FORWARDABLE & ~filters.COMMAND & ~filters.Text(list(MENU_BUTTON_LABELS))


def build_menu_button_filter():
    return filters.Text(list(MENU_BUTTON_LABELS)) & ~filters.COMMAND


async def post_init(application) -> None:
    """Register the slash-command menu shown in Telegram's UI."""
    from telegram import BotCommand

    commands = [
        BotCommand("start", "restart bot"),
        BotCommand("find", "find a partner"),
        BotCommand("stop", "end chat"),
        BotCommand("next", "switch partner"),
        BotCommand("profile", "view profile"),
        BotCommand("edit", "edit profile fields"),
        BotCommand("rechat", "reconnect with a recent partner"),
        BotCommand("rating", "view your rating"),
        BotCommand("premium", "premium plans"),
        BotCommand("buy", "purchase a plan"),
        BotCommand("setfilter", "match filters"),
        BotCommand("help", "help"),
    ]
    try:
        await application.bot.set_my_commands(commands)
        logger.info("Slash command menu registered with Telegram.")
    except Exception:
        logger.exception("Failed to register bot commands")
