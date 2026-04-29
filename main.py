"""Entry point for the anonymous chat Telegram bot."""
from __future__ import annotations

import logging
import os
import sys

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    PreCheckoutQueryHandler,
    filters,
)

from bot import handlers


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


def main() -> None:
    configure_logging()
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("ERROR: TELEGRAM_BOT_TOKEN is not set.", file=sys.stderr)
        sys.exit(1)

    application = (
        Application.builder()
        .token(token)
        .post_init(handlers.post_init)
        .build()
    )

    application.add_handler(handlers.build_registration_handler())
    application.add_handler(CommandHandler("help", handlers.help_cmd))
    application.add_handler(CommandHandler("profile", handlers.profile_cmd))
    application.add_handler(CommandHandler("premium", handlers.premium_cmd))
    application.add_handler(CommandHandler("buy", handlers.buy_cmd))
    application.add_handler(CommandHandler("setfilter", handlers.setfilter_cmd))
    application.add_handler(CommandHandler("find", handlers.find_cmd))
    application.add_handler(CommandHandler("stop", handlers.stop_cmd))
    application.add_handler(CommandHandler("next", handlers.next_cmd))
    application.add_handler(CommandHandler("edit", handlers.edit_cmd))
    application.add_handler(CommandHandler("cancel", handlers.edit_cancel_cmd))
    application.add_handler(CommandHandler("rating", handlers.rating_cmd))
    application.add_handler(CommandHandler("rechat", handlers.rechat_cmd))
    application.add_handler(CommandHandler("grant", handlers.grant_cmd))
    application.add_handler(CommandHandler("revoke", handlers.revoke_cmd))
    application.add_handler(CommandHandler("stats", handlers.stats_cmd))

    # Telegram Stars payment flow
    application.add_handler(CallbackQueryHandler(handlers.buy_callback, pattern=r"^buy:"))
    application.add_handler(CallbackQueryHandler(handlers.buy_edit_callback, pattern=r"^buyedit$"))
    application.add_handler(CallbackQueryHandler(handlers.buy_rating_callback, pattern=r"^buyrating$"))
    application.add_handler(PreCheckoutQueryHandler(handlers.precheckout_handler))
    application.add_handler(
        MessageHandler(filters.SUCCESSFUL_PAYMENT, handlers.successful_payment_handler)
    )

    # Profile edit / rating / rechat callbacks
    application.add_handler(CallbackQueryHandler(handlers.edit_callback, pattern=r"^edit:"))
    application.add_handler(CallbackQueryHandler(handlers.rating_callback, pattern=r"^rate:"))
    application.add_handler(CallbackQueryHandler(handlers.rechat_callback, pattern=r"^rechat:"))
    application.add_handler(CallbackQueryHandler(handlers.rechat_response_callback, pattern=r"^(racc|rdec):"))

    # Edit-input capture: must run before the menu router & relay.
    # It returns silently when no edit is pending, so other handlers still fire.
    from telegram.ext import filters as _filters
    application.add_handler(
        MessageHandler(_filters.TEXT & ~_filters.COMMAND, handlers.edit_input_handler)
    )

    # Persistent menu button taps and anonymous chat relay live in a later group
    # so that the edit-input handler in group 0 can stop further processing
    # only when it actually consumed the message.
    application.add_handler(
        MessageHandler(handlers.build_menu_button_filter(), handlers.menu_button_router),
        group=1,
    )
    application.add_handler(
        MessageHandler(handlers.build_relay_filter(), handlers.relay),
        group=1,
    )

    if application.job_queue is not None:
        application.job_queue.run_repeating(handlers.trial_watchdog, interval=20, first=20)

    logging.getLogger(__name__).info("Bot starting (polling)...")
    application.run_polling(allowed_updates=None, drop_pending_updates=True)


if __name__ == "__main__":
    main()
