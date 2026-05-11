"""Telegram bot entry — thin wrapper over :mod:`core`.

The real handlers land in Этап 7. Until then ``bot.main`` exposes stub
``start_bot_polling`` / ``stop_bot_polling`` coroutines so :mod:`main` can
be wired up and tested without aiogram in the import path.
"""
