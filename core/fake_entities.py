"""
Fake entities for testing and mocking telegram objects.
"""
import logging
from typing import Optional, Dict, Any, Union
from datetime import datetime

from aiogram.types import Message, CallbackQuery, User, Chat, Update

logger = logging.getLogger(__name__)


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


def create_fake_message(
        chat_id: int,
        message_id: int,
        from_user_id: int,
        text: Optional[str] = None,
        date: Optional[datetime] = None
) -> Message:
    """
    Create a fake Message object.

    Args:
        chat_id: Chat ID
        message_id: Message ID
        from_user_id: User ID who sent the message
        text: Optional message text
        date: Optional message date

    Returns:
        Message: Fake Message object
    """
    fake_chat = Chat(id=chat_id, type="private")
    fake_user = User(id=from_user_id, is_bot=False, first_name="User")

    return Message(
        message_id=message_id,
        date=date or datetime.now(),
        chat=fake_chat,
        from_user=fake_user,
        text=text
    )


def create_fake_callback_query(
        chat_id: int,
        message_id: int,
        from_user_id: int,
        callback_data: str,
        text: Optional[str] = None,
        date: Optional[datetime] = None
) -> CallbackQuery:
    """
    Create a fake CallbackQuery object.

    Args:
        chat_id: Chat ID
        message_id: Message ID
        from_user_id: User ID who pressed the button
        callback_data: Callback data
        text: Optional message text
        date: Optional message date

    Returns:
        CallbackQuery: Fake CallbackQuery object
    """
    fake_message = create_fake_message(
        chat_id=chat_id,
        message_id=message_id,
        from_user_id=from_user_id,
        text=text,
        date=date
    )

    return CallbackQuery(
        id=f"fake_callback_{message_id}",
        from_user=fake_message.from_user,
        chat_instance=str(chat_id),
        message=fake_message,
        data=callback_data
    )


def create_fake_update(
        update_type: str,
        chat_id: int,
        from_user_id: int,
        message_id: Optional[int] = None,
        callback_data: Optional[str] = None,
        text: Optional[str] = None
) -> Update:
    """
    Create a fake Update object.

    Args:
        update_type: Type of update ('message' or 'callback_query')
        chat_id: Chat ID
        from_user_id: User ID
        message_id: Optional message ID (generated if None)
        callback_data: Optional callback data (required for callback_query type)
        text: Optional message text

    Returns:
        Update: Fake Update object
    """
    if message_id is None:
        message_id = int(datetime.now().timestamp())

    if update_type == 'message':
        fake_message = create_fake_message(
            chat_id=chat_id,
            message_id=message_id,
            from_user_id=from_user_id,
            text=text
        )
        return Update(
            update_id=message_id,
            message=fake_message
        )
    elif update_type == 'callback_query':
        if callback_data is None:
            raise ValueError("callback_data is required for callback_query update type")

        fake_callback = create_fake_callback_query(
            chat_id=chat_id,
            message_id=message_id,
            from_user_id=from_user_id,
            callback_data=callback_data,
            text=text
        )
        return Update(
            update_id=message_id,
            callback_query=fake_callback
        )
    else:
        raise ValueError(f"Unsupported update_type: {update_type}")