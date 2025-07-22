"""
Database connection and session management module.
Support for multiple database connections.
"""
import logging
from contextlib import contextmanager
from enum import Enum

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models.base import Base
from config import Config, ConfigurationError

logger = logging.getLogger(__name__)

class DatabaseType(Enum):
    HELPBOT = "helpbot"
    MAINBOT = "mainbot"

# Database engines and session factories
_ENGINES = {}
_SESSION_FACTORIES = {}

def get_db_session(db_type: DatabaseType = DatabaseType.HELPBOT):
    """
    Create and return SQLAlchemy session factory and engine.

    Args:
        db_type: Which database to connect to

    Returns:
        Tuple of session factory and engine

    Raises:
        ConfigurationError: If database URL is not configured
    """
    global _ENGINES, _SESSION_FACTORIES

    if db_type not in _ENGINES:
        try:
            # Get appropriate database URL
            if db_type == DatabaseType.HELPBOT:
                db_url = Config.get(Config.DATABASE_URL)
                config_key = "DATABASE_URL"
            elif db_type == DatabaseType.MAINBOT:
                db_url = Config.get(Config.MAINBOT_DATABASE_URL)
                config_key = "MAINBOT_DATABASE_URL"
            else:
                raise ValueError(f"Unknown database type: {db_type}")

            if not db_url:
                raise ConfigurationError(f"{config_key} is not set or empty")

            # SQLite compatibility
            if db_url.startswith('sqlite+aiosqlite'):
                db_url = db_url.replace('sqlite+aiosqlite', 'sqlite')

            connect_args = {}
            if db_url.startswith('sqlite'):
                connect_args["check_same_thread"] = False

            _ENGINES[db_type] = create_engine(
                db_url,
                connect_args=connect_args
            )

            _SESSION_FACTORIES[db_type] = sessionmaker(bind=_ENGINES[db_type])
            logger.info(f"Database engine initialized for {db_type.value} with {db_url}")

        except ConfigurationError as e:
            logger.critical(f"Database configuration error for {db_type.value}: {e}")
            raise

    return _SESSION_FACTORIES[db_type], _ENGINES[db_type]


@contextmanager
def get_db_session_ctx(db_type: DatabaseType = DatabaseType.HELPBOT):
    """
    Context manager for database sessions with automatic commit/rollback.

    Args:
        db_type: Which database to connect to

    Yields:
        SQLAlchemy Session object

    Raises:
        ConfigurationError: If database is not configured
    """
    session_factory, _ = get_db_session(db_type)
    session = session_factory()

    try:
        yield session
        # Only commit for helpbot database (read-write)
        if db_type == DatabaseType.HELPBOT:
            session.commit()
    except Exception as e:
        session.rollback()
        logger.error(f"Database session error in {db_type.value}: {e}")
        raise
    finally:
        session.close()


# Convenience functions for backward compatibility
@contextmanager
def get_helpbot_session():
    """Get helpbot database session (read-write)"""
    with get_db_session_ctx(DatabaseType.HELPBOT) as session:
        yield session


@contextmanager
def get_mainbot_session():
    """Get mainbot database session (read-only)"""
    with get_db_session_ctx(DatabaseType.MAINBOT) as session:
        yield session


def init_tables(engine=None):
    """
    Initialize database tables.

    Args:
        engine: SQLAlchemy engine (optional, uses helpbot engine if None)
    """
    if engine is None:
        engine = get_db_session(DatabaseType.HELPBOT)[1]

    Base.metadata.create_all(engine)
    logger.info("Database tables initialized")


def setup_database():
    """
    Initialize database connections and create tables if needed.

    Returns:
        Tuple containing session factory and engine for helpbot

    Raises:
        ConfigurationError: If database configuration is invalid
    """
    try:
        # Setup helpbot database
        session_factory, engine = get_db_session(DatabaseType.HELPBOT)
        init_tables(engine)

        # Test mainbot connection (but don't create tables there!)
        try:
            mainbot_factory, mainbot_engine = get_db_session(DatabaseType.MAINBOT)
            logger.info("Successfully connected to mainbot database")
        except Exception as e:
            logger.warning(f"Could not connect to mainbot database: {e}")
            logger.warning("Support bot will work without mainbot integration")

        logger.info("Databases initialized successfully")
        return session_factory, engine
    except ConfigurationError as e:
        logger.critical(f"Database configuration error: {e}")
        raise
    except Exception as e:
        logger.error(f"Error initializing database: {e}", exc_info=True)
        raise