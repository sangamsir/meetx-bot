"""In-memory matching queue and active chat pairing."""
from __future__ import annotations

import threading
import time
from typing import Any

from . import storage

_lock = threading.RLock()

# user_id -> partner_id
_pairs: dict[int, int] = {}
# user_id -> session start timestamp
_session_start: dict[int, float] = {}
# Waiting users (FIFO). VIP users get inserted at front.
_waiting: list[int] = []


def is_in_chat(user_id: int) -> bool:
    with _lock:
        return user_id in _pairs


def is_waiting(user_id: int) -> bool:
    with _lock:
        return user_id in _waiting


def get_partner(user_id: int) -> int | None:
    with _lock:
        return _pairs.get(user_id)


def _matches_filters(seeker: dict[str, Any], candidate: dict[str, Any]) -> bool:
    """Check if a candidate fits the seeker's premium filters (and vice versa)."""
    for filter_owner, other in ((seeker, candidate), (candidate, seeker)):
        if not storage.has_active_premium(filter_owner):
            continue
        plan = filter_owner.get("premium_plan")
        filters = filter_owner.get("filters") or {}
        if plan in ("pro", "vip"):
            wanted_gender = filters.get("gender")
            if wanted_gender and other.get("gender", "").lower() != wanted_gender.lower():
                return False
        if plan == "vip":
            wanted_city = filters.get("city")
            if wanted_city and other.get("city", "").strip().lower() != wanted_city.strip().lower():
                return False
    return True


def find_match(user_id: int) -> int | None:
    """Try to match user with someone in the waiting queue.

    Returns the matched partner id, or None if user was added to the queue.
    """
    with _lock:
        if user_id in _pairs:
            return _pairs[user_id]
        if user_id in _waiting:
            _waiting.remove(user_id)

        seeker = storage.get_user(user_id)
        if not seeker:
            return None

        for candidate_id in list(_waiting):
            candidate = storage.get_user(candidate_id)
            if not candidate:
                _waiting.remove(candidate_id)
                continue
            if _matches_filters(seeker, candidate):
                _waiting.remove(candidate_id)
                _pairs[user_id] = candidate_id
                _pairs[candidate_id] = user_id
                now = time.time()
                _session_start[user_id] = now
                _session_start[candidate_id] = now
                return candidate_id

        # No match found, queue the user (VIP at front).
        if storage.has_active_premium(seeker) and seeker.get("premium_plan") == "vip":
            _waiting.insert(0, user_id)
        else:
            _waiting.append(user_id)
        return None


def end_chat(user_id: int) -> tuple[int | None, int]:
    """End the chat for user_id. Returns (partner_id_or_None, seconds_used)."""
    with _lock:
        if user_id in _waiting:
            _waiting.remove(user_id)
        partner = _pairs.pop(user_id, None)
        seconds_used = 0
        if partner is not None:
            _pairs.pop(partner, None)
            start = _session_start.pop(user_id, None)
            partner_start = _session_start.pop(partner, None)
            if start is not None:
                seconds_used = int(time.time() - start)
            if partner_start is not None and partner is not None:
                partner_seconds = int(time.time() - partner_start)
                _record_usage(partner, partner_seconds)
            if seconds_used > 0:
                _record_usage(user_id, seconds_used)
        return partner, seconds_used


def cancel_waiting(user_id: int) -> bool:
    with _lock:
        if user_id in _waiting:
            _waiting.remove(user_id)
            return True
        return False


def force_pair(user_a: int, user_b: int) -> bool:
    """Directly pair two users for a rechat. Both must be free (not in chat / not waiting)."""
    with _lock:
        if user_a in _pairs or user_b in _pairs:
            return False
        if user_a in _waiting:
            _waiting.remove(user_a)
        if user_b in _waiting:
            _waiting.remove(user_b)
        _pairs[user_a] = user_b
        _pairs[user_b] = user_a
        now = time.time()
        _session_start[user_a] = now
        _session_start[user_b] = now
        return True


def is_free(user_id: int) -> bool:
    with _lock:
        return user_id not in _pairs and user_id not in _waiting


def _record_usage(user_id: int, seconds: int) -> None:
    user = storage.get_user(user_id)
    if not user:
        return
    if storage.has_active_premium(user):
        # Premium users: still increment chat count, but no trial seconds.
        storage.update_user(user_id, total_chats=int(user.get("total_chats", 0)) + 1)
    else:
        storage.add_trial_seconds(user_id, seconds)


def session_seconds(user_id: int) -> int:
    with _lock:
        start = _session_start.get(user_id)
        if start is None:
            return 0
        return int(time.time() - start)
