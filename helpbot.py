"""
Main entry point for the Help Bot.
"""
import asyncio
import logging
import signal
from aiogram import Bot, Dispatcher

from config import Config, ConfigurationError
from core.db import setup_database
from core.system_services import ServiceManager, start_bot_polling, shutdown, setup_resources, get_bot_info
from core.templates import MessageTemplates
from core.user_decorator import UserMiddleware
from core.message_service import MessageService
from core.input_service import InputService

# Import dialogue system
from services.dialogue_service import DialogueService
from services.dialogue_router import DialogueRouter
from core.di import register_service

# Import data management
from services.data_importer import ConfigImporter
from services.export_config import setup_sheets_exporter

from handlers import register_all_handlers

logger = logging.getLogger(__name__)


async def initialize_bot():
    """
    Initialize the bot with strict configuration checking.
    """
    try:
        # Initialize from .env first
        logger.info("Loading configuration from .env...")
        Config.initialize_from_env()

        # Setup database
        logger.info("Setting up database...")
        setup_database()

        # Load configuration from Google Sheets
        try:
            logger.info("Loading configuration from Google Sheets...")
            config_dict = await ConfigImporter.import_config()
            logger.info(f"Loaded configuration: {len(config_dict)} keys")
        except Exception as e:
            logger.critical(f"Failed to load configuration from Google Sheets: {e}")
            raise ConfigurationError("Cannot start without Google Sheets configuration") from e

        # Initialize dynamic values
        logger.info("Initializing dynamic configuration...")
        await Config.initialize_dynamic_values()

        # Validate critical keys
        logger.info("Validating configuration...")
        await Config.validate_critical_keys()

        # Get API token
        api_token = Config.get(Config.API_TOKEN)
        if not api_token:
            raise ConfigurationError("Bot API token not configured")

        # Initialize bot and dispatcher
        bot = Bot(token=api_token)
        dp = Dispatcher()

        # Get bot info
        bot_info = await get_bot_info(bot)
        logger.info(f"Bot initialized: @{bot_info.get('username')}")

        # Setup middleware
        logger.info("Setting up middleware...")
        dp.message.middleware(UserMiddleware(bot))
        dp.callback_query.middleware(UserMiddleware(bot))

        # Setup resources (templates and actions)
        logger.info("Setting up resources...")
        message_manager = await setup_resources(bot)

        # Initialize core services
        logger.info("Initializing core services...")

        # Message service
        message_service = MessageService(bot, MessageTemplates)
        register_service(MessageService, message_service)

        # Input service
        input_service = InputService(dp)
        register_service(InputService, input_service)

        # Initialize dialogue system
        logger.info("Initializing dialogue system...")

        dialogue_service = DialogueService(bot, message_service, input_service)
        dialogue_router = DialogueRouter(message_service)

        # Set cross-references
        dialogue_service.set_message_router(dialogue_router)
        dialogue_router.set_dialogue_service(dialogue_service)

        # Register in DI
        register_service(DialogueService, dialogue_service)
        register_service(DialogueRouter, dialogue_router)

        # Start background task for stale dialogues
        await dialogue_service.start_stale_check_task()
        # Restore active dialogues after restart
        await dialogue_service.restore_active_dialogues()

        logger.info("Dialogue system initialized successfully")

        logger.info("Dialogue system initialized successfully")

        # Create service manager
        service_manager = ServiceManager(bot)

        # Register handlers
        logger.info("Registering all handlers...")
        register_all_handlers(dp, bot)

        # Setup signal handlers
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                asyncio.get_event_loop().add_signal_handler(
                    sig,
                    lambda s=sig: asyncio.create_task(shutdown(s, bot, dp))
                )
            except NotImplementedError:
                logger.warning(f"Signal {sig.name} handler not implemented on this platform")

        # Setup Google Sheets exporter as background task
        try:
            logger.info("Setting up Google Sheets exporter...")
            sheets_exporter = setup_sheets_exporter()
            export_task = asyncio.create_task(sheets_exporter.start())
            logger.info("Sheets export service started")
        except Exception as e:
            logger.error(f"Failed to setup sheets exporter: {e}")
            # Continue without export - it's not critical

        # Start config update loop
        logger.info("Starting configuration update loop...")
        update_task = asyncio.create_task(Config.start_update_loop())

        # Start background services
        logger.info("Starting background services...")
        await service_manager.start_services()

        # Set system as ready
        Config.set(Config.SYSTEM_READY, True, source="system")
        Config.set(Config.SYSTEM_STATUS, "online", source="system")

        # Start bot polling
        logger.info("Starting bot polling...")
        await start_bot_polling(bot, dp)

    except ConfigurationError as e:
        logger.critical(f"Configuration error: {str(e)}")
        raise SystemExit("Bot initialization failed due to configuration error")
    except Exception as e:
        logger.critical(f"Failed to initialize bot: {str(e)}", exc_info=True)
        raise SystemExit("Bot initialization failed")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    asyncio.run(initialize_bot())