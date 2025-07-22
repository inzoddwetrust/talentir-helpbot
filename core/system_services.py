"""
System services management for HelpBot.
Provides centralized management of background services and tasks.
"""
import asyncio
import logging
from aiogram.exceptions import TelegramAPIError
import traceback
from aiogram import Bot, Dispatcher
from typing import Dict, Any

from config import Config
from core.message_manager import MessageManager
from core.templates import MessageTemplates

logger = logging.getLogger(__name__)


class ServiceManager:
    """
    Manager for HelpBot background services.
    Handles starting and stopping of all system services.
    """

    def __init__(self, bot: Bot):
        """
        Initialize service manager.

        Args:
            bot: Bot instance
        """
        self.bot = bot
        self.services = []
        self.is_running = False
        self.tasks = []

    async def start_services(self):
        """Start all background services."""
        if self.is_running:
            logger.warning("Services already running")
            return

        self.is_running = True
        logger.info("Starting background services")

        try:
            # Mark system as ready
            Config.set(Config.SYSTEM_READY, True, source="system")
            logger.info("All services started successfully")

        except Exception as e:
            logger.error(f"Error starting services: {e}", exc_info=True)
            self.is_running = False
            raise

    async def stop_services(self):
        """Stop all background services."""
        if not self.is_running:
            return

        logger.info("Stopping background services")
        self.is_running = False

        # Cancel all tasks
        for task in self.tasks:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.error(f"Error stopping task {task.get_name()}: {e}")

        self.tasks = []
        logger.info("All services stopped")


async def setup_resources(bot: Bot) -> MessageManager:
    """
    Setup application resources.

    Args:
        bot: Bot instance

    Returns:
        MessageManager: Initialized message manager
    """
    # Initialize message manager
    message_manager = MessageManager(bot)

    # Load templates
    try:
        await MessageTemplates.load_templates()
        logger.info("Message templates loaded successfully")
    except Exception as e:
        logger.error(f"Error loading templates: {e}")
        traceback.print_exc()

    # Initialize action system (empty for helpbot)
    try:
        from actions import initialize_registries
        initialize_registries()
        logger.info("Action system initialized (empty for helpbot)")
    except Exception as e:
        logger.error(f"Error initializing action system: {e}")

    return message_manager


async def start_bot_polling(bot: Bot, dp: Dispatcher, timeout: int = 20, retry_interval: int = 5) -> None:
    """
    Start bot polling with error handling and retries.

    Args:
        bot: Bot instance
        dp: Dispatcher
        timeout: Polling timeout in seconds
        retry_interval: Retry interval in seconds if connection fails
    """
    logger.info("Starting bot polling")
    while True:
        try:
            await dp.start_polling(bot, timeout=timeout, skip_updates=True)
        except TelegramAPIError as e:
            logger.error(f"Connection error: {e}. Restarting in {retry_interval} seconds...")
            await asyncio.sleep(retry_interval)
        except (KeyboardInterrupt, SystemExit):
            logger.info("Bot polling stopped")
            break
        except Exception as e:
            logger.error(f"Unexpected error in bot polling: {e}. Restarting in {retry_interval * 2} seconds...")
            await asyncio.sleep(retry_interval * 2)
    logger.info("Bot shut down")


async def shutdown(signal_type, bot: Bot, dp: Dispatcher):
    """
    Graceful shutdown on signal.

    Args:
        signal_type: Signal type
        bot: Bot instance
        dp: Dispatcher instance
    """
    logger.info(f"Received exit signal {signal_type.name}...")

    logger.info("Stopping bot polling...")
    await dp.stop_polling()

    logger.info("Closing bot session...")
    if bot.session:
        await bot.session.close()

    logger.info("Bot shutdown complete")


async def get_bot_info(bot: Bot) -> Dict[str, Any]:
    """
    Get bot information.

    Args:
        bot: Bot instance

    Returns:
        Dictionary with bot information
    """
    try:
        me = await bot.get_me()
        return {
            'id': me.id,
            'username': me.username,
            'first_name': me.first_name,
            'is_bot': me.is_bot,
            'can_join_groups': me.can_join_groups,
            'can_read_all_group_messages': me.can_read_all_group_messages,
            'supports_inline_queries': me.supports_inline_queries
        }
    except Exception as e:
        logger.error(f"Error getting bot info: {e}")
        return {'error': str(e)}