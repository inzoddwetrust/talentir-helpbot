"""
Centralized configuration system for the application.
Replaces the GlobalVariables system with a simpler, more organized approach.
"""
import os
import asyncio
import logging
from typing import Dict, Any, List, Callable, Set
from datetime import datetime
from functools import wraps
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Global semaphore for limiting concurrent threads
THREAD_SEMAPHORE = asyncio.Semaphore(10)


class ConfigurationError(Exception):
    """
    Exception raised for critical configuration errors.

    Attributes:
        message -- explanation of the error
        source -- component or module where error occurred
    """

    def __init__(self, message, source=None):
        self.message = message
        self.source = source
        super().__init__(self.message)

    def __str__(self):
        if self.source:
            return f"Configuration Error in {self.source}: {self.message}"
        return f"Configuration Error: {self.message}"


class Config:
    """
    Centralized configuration class that provides access to all application settings.
    Replaces multiple separate classes with a single access point.
    """
    # Bot configuration
    API_TOKEN = "api_token"
    ADMINS = "admins"
    ADMIN_LINKS = "admin_links"
    DEFAULT_REFERRER = "default_referrer"
    DEBUG = "debug"

    # Google configuration
    GOOGLE_SHEET_ID = "google_sheet_id"
    GOOGLE_CREDENTIALS_JSON = "google_credentials_json"
    GOOGLE_SCOPES = "google_scopes"

    # Database configuration
    DATABASE_URL = "database_url"
    MAINBOT_DATABASE_URL = "mainbot_database_url"  # Read-only connection to mainbot

    # System configuration
    SYSTEM_VERSION = "system_version"
    SYSTEM_START_TIME = "system_start_time"
    SYSTEM_READY = "system_ready"
    SYSTEM_STATUS = "system_status"
    TOTAL_USERS = "total_users"
    TOTAL_TICKETS = "total_tickets"
    ACTIVE_TICKETS = "active_tickets"
    GROUP_ID = "group_id"  # ID мультигруппы операторов

    #Claude config
    CLAUDE_API_KEY = 'CLAUDE_API_KEY'
    CLAUDE_MODEL = 'CLAUDE_MODEL'
    CLAUDE_MAX_TOKENS = 'CLAUDE_MAX_TOKENS'
    CLAUDE_TIMEOUT = 'CLAUDE_TIMEOUT'
    CLAUDE_RATE_LIMIT = 'CLAUDE_RATE_LIMIT'
    TRANSLATION_PROMPT = 'TRANSLATION_PROMPT'

    # Helpbot specific configuration
    TICKET_CATEGORIES = "ticket_categories"  # Available ticket categories
    AUTO_CLOSE_HOURS = "auto_close_hours"  # Hours before auto-closing inactive tickets
    REMINDER_INTERVALS = "reminder_intervals"  # Minutes between inactivity reminders
    MAX_TICKETS_PER_OPERATOR = "max_tickets_per_operator"  # Maximum concurrent tickets
    WELCOME_MESSAGE_DELAY = "welcome_message_delay"  # Delay before welcome message
    OPERATOR_NOTIFICATION_DELAY = "operator_notification_delay"  # Delay for operator notification
    AUTO_ASSIGN_ENABLED = "auto_assign_enabled"  # Enable auto-assignment of tickets
    FEEDBACK_ENABLED = "feedback_enabled"  # Request feedback after ticket closure
    FEEDBACK_DELAY_HOURS = "feedback_delay_hours"  # Delay before requesting feedback

    # Internal storage
    _static_values: Dict[str, Any] = {}  # Values loaded from .env and static sources
    _dynamic_values: Dict[str, Any] = {}  # Values that can be updated dynamically
    _update_functions: Dict[str, Callable] = {}  # Functions to update dynamic values
    _update_intervals: Dict[str, int] = {}  # Update intervals for dynamic values
    _last_updates: Dict[str, datetime] = {}  # Last update times
    _is_updating: Dict[str, bool] = {}  # Track ongoing updates
    _sources: Dict[str, str] = {}  # Track where each value came from
    _listeners: Dict[str, List[Callable]] = {}  # Listeners for value changes
    _dependencies: Dict[str, Set[str]] = {}  # Dependencies between values
    _initialized = False  # Track if initialize_from_env has been called

    # Critical keys that must be set before the bot starts
    CRITICAL_KEYS = [
        API_TOKEN,
        ADMINS,
        DATABASE_URL,
        MAINBOT_DATABASE_URL,
        GOOGLE_SHEET_ID,
        GOOGLE_CREDENTIALS_JSON,
        GROUP_ID,
    ]

    @classmethod
    def get(cls, key: str, default: Any = None) -> Any:
        """
        Get a configuration value by key.

        Args:
            key: Configuration key
            default: Default value if key not found

        Returns:
            The configuration value or default
        """
        if not cls._initialized:
            logger.warning(f"Config accessed before initialization: {key}")

        # Static values take precedence over dynamic ones
        if key in cls._static_values:
            value = cls._static_values[key]
            logger.debug(f"Get static value: {key}={value}, source={cls._sources.get(key, 'unknown')}")
            return value

        if key in cls._dynamic_values:
            value = cls._dynamic_values[key]
            logger.debug(f"Get dynamic value: {key}={value}, source={cls._sources.get(key, 'unknown')}")
            return value

        logger.debug(f"Key not found, returning default: {key}={default}")
        return default

    @classmethod
    def set(cls, key: str, value: Any, source: str = "manual") -> None:
        """
        Set a configuration value.

        Args:
            key: Configuration key
            value: Configuration value
            source: Source of the value for tracking
        """
        old_value = None
        if key in cls._static_values:
            old_value = cls._static_values[key]
            cls._static_values[key] = value
        else:
            if key in cls._dynamic_values:
                old_value = cls._dynamic_values[key]
            cls._dynamic_values[key] = value

        cls._last_updates[key] = datetime.utcnow()
        cls._sources[key] = source

        # Notify listeners if value changed
        if old_value != value and key in cls._listeners:
            for listener in cls._listeners[key]:
                try:
                    listener(key, value)
                except Exception as e:
                    logger.error(f"Error in listener for {key}: {e}")

        # Update dependent values
        if key in cls._dependencies:
            for dep_key in cls._dependencies[key]:
                cls._mark_for_update(dep_key)

        logger.debug(f"Set {key}={value} from {source}")

    @classmethod
    def register_update(cls, key: str, update_func: Callable,
                        interval: int = 300, dependencies: List[str] = None) -> None:
        """
        Register a function to update a dynamic value periodically.

        Args:
            key: Configuration key
            update_func: Function that returns the new value
            interval: Update interval in seconds
            dependencies: Keys that this value depends on
        """
        cls._update_functions[key] = update_func
        cls._update_intervals[key] = interval
        cls._is_updating[key] = False

        # Register dependencies
        if dependencies:
            cls._dependencies[key] = set(dependencies)
            for dep_key in dependencies:
                if dep_key not in cls._listeners:
                    cls._listeners[dep_key] = []
                cls._listeners[dep_key].append(lambda _, __: cls._mark_for_update(key))

        logger.info(f"Registered dynamic value: {key} with {interval}s interval")

    @classmethod
    def add_listener(cls, key: str, listener: Callable[[str, Any], None]) -> None:
        """
        Add a listener to be notified when a value changes.

        Args:
            key: Configuration key
            listener: Function to call with (key, new_value) when value changes
        """
        if key not in cls._listeners:
            cls._listeners[key] = []
        cls._listeners[key].append(listener)
        logger.debug(f"Added listener for {key}")

    @classmethod
    def initialize_from_env(cls) -> None:
        """
        Initialize static configuration values from environment variables.
        This should be the first method called during application startup.
        """
        if cls._initialized:
            logger.warning("Config already initialized from .env")
            return

        logger.info("Loading configuration from .env")
        load_dotenv()

        # Strict check for administrators - this is critical
        admins_str = os.getenv("ADMINS")
        if not admins_str or not admins_str.strip():
            logger.critical("ADMINS variable is required in .env")
            raise ConfigurationError("ADMINS variable is required in .env")

        try:
            # Convert to a list of unique integers
            admin_ids = list(set(
                int(x.strip()) for x in admins_str.split(",")
                if x.strip() and x.strip().isdigit()
            ))

            if not admin_ids:
                raise ValueError("No valid admin IDs provided")

            cls.set(cls.ADMINS, admin_ids, source="env")
            logger.info(f"Loaded admins from .env: {admin_ids}")

        except ValueError as e:
            logger.critical(f"Invalid ADMINS format in .env: {admins_str}")
            raise ConfigurationError(str(e))

        # Load other environment variables
        env_vars = {
            cls.API_TOKEN: os.getenv("API_TOKEN"),
            cls.ADMIN_LINKS: os.getenv("ADMIN_LINKS", "").split(",") if os.getenv("ADMIN_LINKS") else None,
            cls.DEFAULT_REFERRER: os.getenv("DEFAULT_REFERRER"),
            cls.DEBUG: os.getenv("DEBUG", "").lower() == "true" if os.getenv("DEBUG") else None,
            cls.GOOGLE_SHEET_ID: os.getenv("GOOGLE_SHEET_ID"),
            cls.GOOGLE_CREDENTIALS_JSON: os.getenv("GOOGLE_CREDENTIALS_JSON"),
            cls.GOOGLE_SCOPES: [
                "https://www.googleapis.com/auth/drive",
                "https://www.googleapis.com/auth/spreadsheets"
            ] if os.getenv("GOOGLE_CREDENTIALS_JSON") else None,
            cls.DATABASE_URL: os.getenv("HELPBOT_DATABASE_URL"),
            cls.MAINBOT_DATABASE_URL: os.getenv("MAINBOT_DATABASE_URL"),
            cls.GROUP_ID: os.getenv("HELPBOT_GROUP_ID"),
            cls.CLAUDE_API_KEY: os.getenv("CLAUDE_API_KEY"),
            cls.CLAUDE_MODEL: os.getenv("CLAUDE_MODEL", "claude-3-5-sonnet-20241022"),
            cls.CLAUDE_MAX_TOKENS: os.getenv("CLAUDE_MAX_TOKENS", "1000"),
            cls.CLAUDE_TIMEOUT: os.getenv("CLAUDE_TIMEOUT", "30"),
            cls.CLAUDE_RATE_LIMIT: os.getenv("CLAUDE_RATE_LIMIT", "10"),
        }

        for key, value in env_vars.items():
            if value is not None:
                cls.set(key, value, source="env")

        # Initialize system variables
        system_vars = {
            cls.SYSTEM_VERSION: "1.0.0",
            cls.SYSTEM_START_TIME: datetime.utcnow(),
            cls.SYSTEM_READY: False,
            cls.SYSTEM_STATUS: "initializing",
            cls.TOTAL_USERS: 0,
            cls.TOTAL_TICKETS: 0,
            cls.ACTIVE_TICKETS: 0,
        }

        for key, value in system_vars.items():
            cls.set(key, value, source="system")

        cls._initialized = True
        logger.info("Configuration initialized from .env")

    @classmethod
    async def initialize_dynamic_values(cls) -> None:
        """
        Initialize dynamic configuration values.
        Should be called after initialize_from_env().
        """
        if not cls._initialized:
            raise ConfigurationError("initialize_from_env() must be called before initialize_dynamic_values()")

        logger.info("Initializing dynamic configuration values")

        # Load initial values for all registered dynamic variables
        update_tasks = []

        for key in cls._update_functions.keys():
            update_tasks.append(cls._update_variable(key))

        if update_tasks:
            results = await asyncio.gather(*update_tasks, return_exceptions=True)
            errors = [r for r in results if isinstance(r, Exception)]

            if errors:
                logger.warning(f"Errors initializing dynamic values: {errors}")

        logger.info("Dynamic configuration values initialized")

    @classmethod
    async def validate_critical_keys(cls) -> None:
        """
        Validate that all critical keys are set.
        Raises ConfigurationError if any critical key is missing.
        """
        logger.info("Validating critical configuration keys")

        missing = []
        for key in cls.CRITICAL_KEYS:
            value = cls.get(key)
            if value is None or (isinstance(value, (list, dict)) and not value):
                missing.append(key)

        if missing:
            error_msg = f"Missing critical configuration keys: {missing}"
            logger.critical(error_msg)
            cls.set(cls.SYSTEM_STATUS, "maintenance", source="system")
            raise ConfigurationError(error_msg)

        logger.info("All critical configuration keys are valid")

    @classmethod
    async def start_update_loop(cls) -> None:
        """
        Start the update loop for dynamic values.
        Should be called after initialize_dynamic_values().
        """
        logger.info("Starting configuration update loop")

        while True:
            try:
                now = datetime.utcnow()

                for key in list(cls._update_functions.keys()):
                    if key not in cls._update_intervals:
                        continue

                    last_update = cls._last_updates.get(key, datetime.min)
                    interval = cls._update_intervals[key]

                    if (now - last_update).total_seconds() > interval:
                        try:
                            await cls._update_variable(key)
                        except Exception as e:
                            logger.error(f"Error updating {key}: {e}")
                            if key in cls.CRITICAL_KEYS:
                                cls.set(cls.SYSTEM_STATUS, "maintenance", source="system")
                                logger.critical(f"System set to maintenance mode due to error updating {key}")

            except Exception as e:
                logger.error(f"Error in update loop: {e}")

            # Sleep until next update
            min_interval = min(cls._update_intervals.values(), default=60)
            await asyncio.sleep(min_interval)

    @classmethod
    async def _update_variable(cls, key: str) -> None:
        """
        Update a dynamic variable by calling its update function.

        Args:
            key: Configuration key to update
        """
        if key not in cls._update_functions or cls._is_updating.get(key, False):
            return

        try:
            cls._is_updating[key] = True
            old_value = cls.get(key)

            update_func = cls._update_functions[key]

            # Добавляем дополнительное логирование
            logger.debug(f"Calling update function for {key}")

            # Вызываем функцию обновления
            result = update_func()

            # Проверяем результат на тип
            if asyncio.iscoroutine(result):
                logger.debug(f"Update function for {key} returned coroutine, awaiting...")
                try:
                    new_value = await result
                    logger.debug(f"Coroutine for {key} finished, result: {new_value}")
                except Exception as coro_error:
                    logger.error(f"Error awaiting coroutine for {key}: {coro_error}", exc_info=True)
                    cls._is_updating[key] = False
                    return
            else:
                logger.debug(f"Update function for {key} returned direct value type: {type(result)}")
                async with THREAD_SEMAPHORE:
                    try:
                        # Если функция синхронная, выполняем ее в отдельном потоке
                        new_value = await asyncio.to_thread(lambda: result)
                    except RuntimeError as e:
                        if "can't start new thread" in str(e):
                            logger.error("Thread limit reached. Executing in current thread.")
                            new_value = result
                        else:
                            raise

            # Проверяем полученное значение
            if new_value is not None:
                logger.debug(f"Setting new value for {key}: {new_value}")
                cls.set(key, new_value, source="dynamic")
                logger.info(f"Updated dynamic value: {key}")
            else:
                logger.warning(f"Update function for {key} returned None, keeping old value: {old_value}")

        except Exception as e:
            logger.error(f"Error updating {key}: {e}", exc_info=True)
            if key in cls.CRITICAL_KEYS and key not in cls._static_values and key not in cls._dynamic_values:
                try:
                    cls.set(cls.SYSTEM_STATUS, "maintenance", source="system")
                    logger.critical(f"System set to maintenance mode due to missing critical key {key}")
                except Exception as status_error:
                    logger.critical(f"Error setting system status: {status_error}")
        finally:
            cls._is_updating[key] = False

    @classmethod
    def _mark_for_update(cls, key: str) -> None:
        """
        Mark a dynamic variable for immediate update.

        Args:
            key: Configuration key to update
        """
        cls._last_updates[key] = datetime.min
        logger.debug(f"Marked {key} for update")

    @classmethod
    def remove(cls, key: str) -> None:
        """
        Remove a configuration value and all related data.

        Args:
            key: Configuration key to remove
        """
        if key in cls._static_values:
            del cls._static_values[key]
        if key in cls._dynamic_values:
            del cls._dynamic_values[key]
        if key in cls._update_functions:
            del cls._update_functions[key]
        if key in cls._update_intervals:
            del cls._update_intervals[key]
        if key in cls._last_updates:
            del cls._last_updates[key]
        if key in cls._is_updating:
            del cls._is_updating[key]
        if key in cls._dependencies:
            del cls._dependencies[key]
        if key in cls._sources:
            del cls._sources[key]
        if key in cls._listeners:
            del cls._listeners[key]

        # Remove from dependencies of other keys
        for deps in cls._dependencies.values():
            if key in deps:
                deps.remove(key)

        logger.debug(f"Removed configuration key: {key}")

    @classmethod
    def get_update_info(cls) -> Dict[str, Dict[str, Any]]:
        """
        Get information about all configuration values.

        Returns:
            Dictionary with details about each configuration value
        """
        result = {}
        all_keys = set(cls._static_values.keys()) | set(cls._dynamic_values.keys())

        for key in all_keys:
            is_static = key in cls._static_values
            value = cls._static_values.get(key) if is_static else cls._dynamic_values.get(key)

            result[key] = {
                "value": value,
                "type": "static" if is_static else "dynamic",
                "source": cls._sources.get(key, "unknown"),
                "interval": cls._update_intervals.get(key),
                "last_update": cls._last_updates.get(key),
                "has_update_func": key in cls._update_functions,
                "is_updating": cls._is_updating.get(key, False),
                "dependencies": list(cls._dependencies.get(key, set())),
                "is_critical": key in cls.CRITICAL_KEYS,
            }

        return result


def depends_on(*keys):
    """
    Decorator for functions that depend on configuration values.

    Example:
    @depends_on(Config.TICKET_CATEGORIES, Config.AUTO_CLOSE_HOURS)
    async def process_ticket(ticket):
        categories = Config.get(Config.TICKET_CATEGORIES)
        auto_close = Config.get(Config.AUTO_CLOSE_HOURS)
        # Function implementation
    """

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            deps = {}
            for key in keys:
                deps[key] = Config.get(key)
                if deps[key] is None and key in Config.CRITICAL_KEYS:
                    logger.error(f"Critical dependency missing in {func.__name__}: {key}")
                    return None
            kwargs.update(deps)
            return await func(*args, **kwargs)

        wrapper._dependencies = keys
        return wrapper

    return decorator