"""
Handlers initialization module.
"""
import logging
from aiogram import Bot, Dispatcher

from .admin import setup_admin_handlers
from .dialogue import setup_dialogue_handlers

logger = logging.getLogger(__name__)


def register_all_handlers(dp: Dispatcher, bot: Bot):
    """
    Register all message handlers with dispatcher.

    Args:
        dp: Dispatcher instance
        bot: Bot instance
    """
    # Register handlers
    setup_admin_handlers(dp, bot)
    setup_dialogue_handlers(dp)

    logger.info("All handlers have been registered")