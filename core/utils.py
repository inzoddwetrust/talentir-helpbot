"""
Utility functions and classes shared across the application.
"""
import logging
from datetime import datetime
from typing import Optional, Union, Any
from aiogram.types import Message, CallbackQuery
from aiogram.exceptions import TelegramAPIError

logger = logging.getLogger(__name__)


class SafeDict(dict):
    """
    Safe dictionary for template formatting to handle missing keys gracefully.
    When a key is missing, returns the key in curly braces instead of raising KeyError.
    Also handles format specifiers for numbers.
    """

    def __missing__(self, key):
        try:
            base_key = key.split(':')[0]
            if ':' in key:
                format_spec = key.split(':', 1)[1]
                if 'f' in format_spec:
                    return format(0, format_spec)
                elif 'd' in format_spec:
                    return format(0, format_spec)
            return '{' + base_key + '}'
        except Exception:
            return '{' + key + '}'


class FakeCallbackQuery:
    """
    Fake CallbackQuery object for handling text input through process_callback.
    Used when we need to process text messages using the same flow as callbacks.
    """

    def __init__(self, message, data=None):
        self.message = message
        self.from_user = message.from_user
        self.data = data
        self.id = str(message.message_id)  # Fake callback query ID

        # Add chat attribute for compatibility
        self.chat = message.chat

    async def answer(self, text=None, show_alert=False, **kwargs):
        """Fake answer method that does nothing"""
        pass

    @property
    def message_id(self):
        """Message ID for compatibility"""
        return self.message.message_id


def get_user_note(user, key: str) -> Optional[str]:
    """
    Gets value from user notes by key.

    Args:
        user: User object with notes field
        key: Note key to retrieve

    Returns:
        Note value or None if not found
    """
    if not user.notes:
        return None

    try:
        notes = dict(note.split(':') for note in user.notes.split() if ':' in note)
        return notes.get(key)
    except Exception as e:
        logger.warning(f"Error parsing user note: {e}")
        return None


def set_user_note(user, key: str, value: str):
    """
    Sets key-value pair in user notes.

    Args:
        user: User object with notes field
        key: Note key to set
        value: Note value to set
    """
    notes = {}
    if user.notes:
        try:
            notes = dict(note.split(':') for note in user.notes.split() if ':' in note)
        except Exception as e:
            logger.warning(f"Error parsing existing user notes: {e}")

    notes[key] = value
    user.notes = ' '.join(f'{k}:{v}' for k, v in notes.items())


# Data parsing utilities

def parse_date(value: Any) -> Optional[datetime]:
    """
    Parse a date value from various formats.

    Args:
        value: Date value to parse

    Returns:
        Datetime object or None if parsing fails
    """
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        try:
            return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            try:
                return datetime.strptime(value, "%Y-%m-%d")
            except (ValueError, TypeError):
                return None


def parse_bool(value: Any) -> bool:
    """
    Parse a boolean value from various formats.

    Args:
        value: Boolean value to parse

    Returns:
        Boolean value
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("true", "yes", "1", "y", "t")
    if isinstance(value, (int, float)):
        return bool(value)
    return False


def parse_int(value: Any) -> int:
    """
    Parse an integer value from various formats.

    Args:
        value: Integer value to parse

    Returns:
        Integer value or 0 if parsing fails
    """
    if not value:
        return 0
    if isinstance(value, int):
        return value
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return 0


def parse_float(value: Any) -> float:
    """
    Parse a float value from various formats.

    Args:
        value: Float value to parse

    Returns:
        Float value or 0.0 if parsing fails
    """
    if not value:
        return 0.0
    if isinstance(value, float):
        return value
    try:
        return float(value)
    except (ValueError, TypeError):
        return 0.0


def clean_str(value: Any) -> str:
    """
    Clean a string value.

    Args:
        value: String value to clean

    Returns:
        Cleaned string value
    """
    if value is None:
        return ""
    return str(value).strip()
