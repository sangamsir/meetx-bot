"""JSON-backed storage for users and premium subscriptions."""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
USERS_FILE = DATA_DIR / "users.json"

_lock = threading.RLock()

DAILY_FREE_SECONDS = 60 * 60  # 1 hour per day for free users

# Pricing for one-off purchases (in Telegram Stars)
EDIT_PRICE_STARS = 10        # 1 profile edit
RATING_VIEW_PRICE_STARS = 10  # one-time unlock to view your own rating
RECHAT_PRICE_STARS = 20      # one rechat session with a previous partner

# Free profile edits granted to non-VIP users
FREE_PROFILE_EDITS = 2

# How many recent partners we remember for rechat
RECENT_PARTNERS_LIMIT = 5

PLAN_DETAILS = {
    "basic": {
        "name": "Basic",
        "price": 49,
        "duration_days": 30,
        "features": [
            "Unlimited daily chat time",
            "No 1-hour daily limit",
        ],
    },
    "pro": {
        "name": "Pro",
        "price": 99,
        "duration_days": 30,
        "features": [
            "Unlimited daily chat time",
            "Filter partners by gender",
            "Priority over free users",
        ],
    },
    "vip": {
        "name": "VIP",
        "price": 199,
        "duration_days": 30,
        "features": [
            "Unlimited daily chat time",
            "Filter by gender and city",
            "Top priority matching",
            "Skip the queue",
        ],
    },
}


def _today() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d")


def _ensure_file() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not USERS_FILE.exists():
        USERS_FILE.write_text("{}", encoding="utf-8")


def _load() -> dict[str, Any]:
    _ensure_file()
    try:
        with USERS_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: dict[str, Any]) -> None:
    _ensure_file()
    tmp = USERS_FILE.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, USERS_FILE)


def _reset_day_if_needed(user: dict[str, Any]) -> None:
    today = _today()
    if user.get("trial_day") != today:
        user["trial_day"] = today
        user["trial_used_today_seconds"] = 0


def get_user(user_id: int) -> dict[str, Any] | None:
    with _lock:
        data = _load()
        user = data.get(str(user_id))
        if user is None:
            return None
        _reset_day_if_needed(user)
        return user


def upsert_user(user_id: int, profile: dict[str, Any]) -> dict[str, Any]:
    with _lock:
        data = _load()
        key = str(user_id)
        existing = data.get(key, {})
        existing.update(profile)
        existing.setdefault("trial_used_today_seconds", 0)
        existing.setdefault("trial_day", _today())
        existing.setdefault("premium_plan", None)
        existing.setdefault("premium_expires_at", 0)
        existing.setdefault("total_chats", 0)
        existing.setdefault("filters", {})
        existing.setdefault("registered_at", int(time.time()))
        # Profile edit tracking
        existing.setdefault("edits_used", 0)
        existing.setdefault("paid_edits", 0)
        # Rating tracking
        existing.setdefault("rating_sum", 0.0)
        existing.setdefault("rating_count", 0)
        existing.setdefault("rating_view_unlocked", False)
        # Rechat tracking
        existing.setdefault("recent_partners", [])  # list of {"id":int,"name":str,"ts":int}
        existing.setdefault("rechat_credits", {})   # str(partner_id) -> int (credits)
        _reset_day_if_needed(existing)
        data[key] = existing
        _save(data)
        return existing


def update_user(user_id: int, **fields: Any) -> dict[str, Any] | None:
    with _lock:
        data = _load()
        key = str(user_id)
        if key not in data:
            return None
        data[key].update(fields)
        _save(data)
        return data[key]


def add_trial_seconds(user_id: int, seconds: int) -> None:
    with _lock:
        data = _load()
        key = str(user_id)
        if key not in data:
            return
        user = data[key]
        _reset_day_if_needed(user)
        user["trial_used_today_seconds"] = int(user.get("trial_used_today_seconds", 0)) + int(seconds)
        user["total_chats"] = int(user.get("total_chats", 0)) + 1
        _save(data)


def grant_premium(user_id: int, plan: str) -> dict[str, Any] | None:
    if plan not in PLAN_DETAILS:
        return None
    duration = PLAN_DETAILS[plan]["duration_days"] * 24 * 60 * 60
    expires = int(time.time()) + duration
    return update_user(user_id, premium_plan=plan, premium_expires_at=expires)


def revoke_premium(user_id: int) -> dict[str, Any] | None:
    return update_user(user_id, premium_plan=None, premium_expires_at=0)


def has_active_premium(user: dict[str, Any]) -> bool:
    return bool(user.get("premium_plan")) and int(user.get("premium_expires_at", 0)) > int(time.time())


def daily_remaining(user: dict[str, Any]) -> int:
    """Remaining free chat seconds for today."""
    if user.get("trial_day") != _today():
        return DAILY_FREE_SECONDS
    return max(0, DAILY_FREE_SECONDS - int(user.get("trial_used_today_seconds", 0)))


def all_users() -> dict[str, Any]:
    with _lock:
        return _load()


# -------- Profile edit allowance --------

def can_edit_profile(user: dict[str, Any]) -> tuple[bool, str]:
    """Return (allowed, reason) where reason is 'vip', 'free', 'paid', or 'pay_required'."""
    if has_active_premium(user) and user.get("premium_plan") == "vip":
        return True, "vip"
    if int(user.get("paid_edits", 0)) > 0:
        return True, "paid"
    if int(user.get("edits_used", 0)) < FREE_PROFILE_EDITS:
        return True, "free"
    return False, "pay_required"


def consume_edit(user_id: int) -> str | None:
    """Use one edit allowance. Returns the source ('vip','paid','free') or None if not allowed."""
    with _lock:
        data = _load()
        key = str(user_id)
        if key not in data:
            return None
        user = data[key]
        if has_active_premium(user) and user.get("premium_plan") == "vip":
            _save(data)
            return "vip"
        if int(user.get("paid_edits", 0)) > 0:
            user["paid_edits"] = int(user["paid_edits"]) - 1
            _save(data)
            return "paid"
        if int(user.get("edits_used", 0)) < FREE_PROFILE_EDITS:
            user["edits_used"] = int(user.get("edits_used", 0)) + 1
            _save(data)
            return "free"
        return None


def grant_paid_edit(user_id: int, count: int = 1) -> dict[str, Any] | None:
    with _lock:
        data = _load()
        key = str(user_id)
        if key not in data:
            return None
        user = data[key]
        user["paid_edits"] = int(user.get("paid_edits", 0)) + int(count)
        _save(data)
        return user


def free_edits_remaining(user: dict[str, Any]) -> int:
    return max(0, FREE_PROFILE_EDITS - int(user.get("edits_used", 0)))


# -------- Rating --------

def record_rating(user_id: int, score: int) -> dict[str, Any] | None:
    if score < 1 or score > 5:
        return None
    with _lock:
        data = _load()
        key = str(user_id)
        if key not in data:
            return None
        user = data[key]
        user["rating_sum"] = float(user.get("rating_sum", 0.0)) + float(score)
        user["rating_count"] = int(user.get("rating_count", 0)) + 1
        _save(data)
        return user


def get_rating(user: dict[str, Any]) -> tuple[float, int]:
    count = int(user.get("rating_count", 0))
    if count == 0:
        return 0.0, 0
    return float(user.get("rating_sum", 0.0)) / count, count


def unlock_rating_view(user_id: int) -> dict[str, Any] | None:
    return update_user(user_id, rating_view_unlocked=True)


def can_view_rating(user: dict[str, Any]) -> bool:
    return bool(user.get("rating_view_unlocked", False))


# -------- Recent partners / rechat --------

def add_recent_partner(user_id: int, partner_id: int, partner_name: str) -> None:
    with _lock:
        data = _load()
        key = str(user_id)
        if key not in data:
            return
        user = data[key]
        recents = [p for p in user.get("recent_partners", []) if int(p.get("id", 0)) != int(partner_id)]
        recents.insert(0, {"id": int(partner_id), "name": partner_name, "ts": int(time.time())})
        user["recent_partners"] = recents[:RECENT_PARTNERS_LIMIT]
        _save(data)


def get_recent_partners(user: dict[str, Any]) -> list[dict[str, Any]]:
    return list(user.get("recent_partners", []))


def grant_rechat_credit(user_id: int, partner_id: int) -> None:
    with _lock:
        data = _load()
        key = str(user_id)
        if key not in data:
            return
        user = data[key]
        credits = dict(user.get("rechat_credits", {}))
        credits[str(partner_id)] = int(credits.get(str(partner_id), 0)) + 1
        user["rechat_credits"] = credits
        _save(data)


def consume_rechat_credit(user_id: int, partner_id: int) -> bool:
    with _lock:
        data = _load()
        key = str(user_id)
        if key not in data:
            return False
        user = data[key]
        credits = dict(user.get("rechat_credits", {}))
        n = int(credits.get(str(partner_id), 0))
        if n <= 0:
            return False
        n -= 1
        if n <= 0:
            credits.pop(str(partner_id), None)
        else:
            credits[str(partner_id)] = n
        user["rechat_credits"] = credits
        _save(data)
        return True


def has_rechat_credit(user: dict[str, Any], partner_id: int) -> bool:
    credits = user.get("rechat_credits", {}) or {}
    return int(credits.get(str(partner_id), 0)) > 0


def can_rechat_free(user: dict[str, Any]) -> bool:
    return has_active_premium(user) and user.get("premium_plan") == "vip"
