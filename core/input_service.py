"""
Simplified input service for message routing.
"""
import logging
import asyncio
from typing import Dict, Any, Optional, List, Callable

from aiogram import Dispatcher, Router
from aiogram.types import Message
from aiogram.filters import Filter

from models.user import User
from core.db import get_db_session_ctx

logger = logging.getLogger(__name__)


class SimpleFilter(Filter):
    """Simple filter that just calls a function."""

    def __init__(self, filter_func: Callable):
        self.filter_func = filter_func

    async def __call__(self, message: Message) -> bool:
        try:
            if callable(self.filter_func):
                if asyncio.iscoroutinefunction(self.filter_func):
                    return await self.filter_func(message)
                else:
                    return self.filter_func(message)
            return False
        except Exception as e:
            logger.error(f"Error in filter: {e}")
            return False


class InputService:
    """Simplified input service for message routing."""

    def __init__(self, dp: Dispatcher):
        """
        Initialize input service.

        Args:
            dp: Dispatcher instance
        """
        self.dp = dp
        self.router = Router(name="input_service_router")
        self.dp.include_router(self.router)
        self.handlers = {}  # For tracking registered handlers

    async def register_user_handler(self, user_id: int, handler: Callable,
                                    state: str = None, message_types: List[str] = None):
        """
        Register handler for specific user.

        Args:
            user_id: User Telegram ID
            handler: Async function to handle messages
            state: Optional FSM state filter
            message_types: List of message types to handle ['text', 'photo', 'video', 'document', 'voice', 'audio']
                          If None - handle all types
        """
        logger.info(f"SIMPLE_INPUT: Registering user handler for {user_id}, state: {state}, types: {message_types}")

        def user_filter(message: Message) -> bool:
            # Check if message is from the right user
            if not message.from_user or message.from_user.id != user_id:
                return False

            # SKIP ADMIN COMMANDS - они имеют приоритет!
            if message.text and message.text.startswith('&'):
                return False

            # Check message type if specified
            if message_types:
                has_type = any(getattr(message, msg_type, None) is not None for msg_type in message_types)
                if not has_type:
                    logger.debug(f"USER_FILTER: Message type not in {message_types}")
                    return False

            # Check state if needed
            if state:
                with get_db_session_ctx() as session:
                    user = session.query(User).filter_by(telegramID=user_id).first()
                    if not user or user.get_fsm_state() != state:
                        logger.debug(f"USER_FILTER: User {user_id} not in state {state}")
                        return False

            logger.info(f"USER_FILTER: Message from user {user_id} passed filter")
            return True

        @self.router.message(SimpleFilter(user_filter))
        async def user_message_handler(message: Message):
            logger.info(f"USER_HANDLER: Processing message from user {user_id}: {message.text[:50] if message.text else '[Media]'}")
            try:
                await handler(message)
            except Exception as e:
                logger.error(f"Error in user handler for {user_id}: {e}", exc_info=True)

        handler_id = f"user_{user_id}_{state or 'any'}"
        self.handlers[handler_id] = user_message_handler
        logger.info(f"SIMPLE_INPUT: Registered user handler {handler_id}")

    async def register_thread_handler(self, group_id: int, thread_id: int, handler: Callable,
                                      message_types: List[str] = None):
        """
        Register handler for specific thread.

        Args:
            group_id: Group chat ID
            thread_id: Thread ID
            handler: Async function to handle messages
            message_types: List of message types to handle. If None - handle all types
        """
        logger.info(f"SIMPLE_INPUT: Registering thread handler for group {group_id}, thread {thread_id}, types: {message_types}")

        def thread_filter(message: Message) -> bool:
            # Check if message is in the right group
            if message.chat.id != group_id:
                logger.debug(f"THREAD_FILTER: Wrong group. Expected {group_id}, got {message.chat.id}")
                return False

            # Check if message has thread_id
            if not hasattr(message, 'message_thread_id'):
                logger.debug(f"THREAD_FILTER: No message_thread_id attribute")
                return False

            # Check if it's the right thread
            if message.message_thread_id != thread_id:
                logger.debug(f"THREAD_FILTER: Wrong thread. Expected {thread_id}, got {message.message_thread_id}")
                return False

            # Check message type if specified
            if message_types:
                has_type = any(getattr(message, msg_type, None) is not None for msg_type in message_types)
                if not has_type:
                    logger.debug(f"THREAD_FILTER: Message type not in {message_types}")
                    return False

            logger.info(f"THREAD_FILTER: Message in thread {group_id}/{thread_id} passed filter")
            return True

        @self.router.message(SimpleFilter(thread_filter))
        async def thread_message_handler(message: Message):
            logger.info(f"THREAD_HANDLER: Processing message in thread {group_id}/{thread_id}")
            logger.info(f"THREAD_HANDLER: From user {message.from_user.id if message.from_user else 'None'}")
            logger.info(f"THREAD_HANDLER: Text: {message.text[:50] if message.text else '[Media]'}")
            logger.info(
                f"THREAD_HANDLER: Has photo: {bool(message.photo)}, video: {bool(message.video)}, document: {bool(message.document)}")

            from models.dialogue import Dialogue
            with get_db_session_ctx() as session:
                dialogue = session.query(Dialogue).filter_by(
                    groupID=group_id,
                    threadID=thread_id,
                    status='active'
                ).first()

                if not dialogue:
                    logger.warning(f"Thread handler called for closed dialogue in {group_id}/{thread_id}")
                    return

            try:
                await handler(message)
            except Exception as e:
                logger.error(f"Error in thread handler for {group_id}/{thread_id}: {e}", exc_info=True)

        handler_id = f"thread_{group_id}_{thread_id}"
        self.handlers[handler_id] = thread_message_handler
        logger.info(f"SIMPLE_INPUT: Registered thread handler {handler_id}")

    async def register_endpoint_handler(self, endpoint, handler: Callable,
                                        state: str = None, message_types: List[str] = None):
        """
        Register handler for messages from a dialogue endpoint.

        Args:
            endpoint: DialogueEndpoint
            handler: Async function to handle messages
            state: Optional FSM state for user endpoints
            message_types: Message types to handle
        """
        logger.debug(f"Registering endpoint handler for {endpoint.type} {endpoint.id} {'thread ' + str(endpoint.thread_id) if endpoint.thread_id else ''}")

        if endpoint.type == 'user':
            await self.register_user_handler(
                user_id=endpoint.id,
                handler=handler,
                state=state,
                message_types=message_types
            )
        elif endpoint.is_thread:
            await self.register_thread_handler(
                group_id=endpoint.id,
                thread_id=endpoint.thread_id,
                handler=handler,
                message_types=message_types
            )
        else:
            logger.warning(f"Unsupported endpoint type for handler: {endpoint.type}")

    async def unregister_user_handler(self, user_id: int, state: str = None):
        """
        Remove user handler (placeholder for compatibility).

        Args:
            user_id: User Telegram ID
            state: Optional FSM state
        """
        handler_id = f"user_{user_id}_{state or 'any'}"
        if handler_id in self.handlers:
            del self.handlers[handler_id]
            logger.info(f"SIMPLE_INPUT: Unregistered user handler {handler_id}")

    async def unregister_thread_handler(self, group_id: int, thread_id: int):
        """
        Remove thread handler (placeholder for compatibility).

        Args:
            group_id: Group chat ID
            thread_id: Thread ID
        """
        handler_id = f"thread_{group_id}_{thread_id}"
        if handler_id in self.handlers:
            del self.handlers[handler_id]
            logger.info(f"SIMPLE_INPUT: Unregistered thread handler {handler_id}")

    async def unregister_endpoint_handler(self, endpoint):
        """
        Unregister handler for an endpoint.

        Args:
            endpoint: DialogueEndpoint
        """
        if endpoint.type == 'user':
            await self.unregister_user_handler(endpoint.id)
        elif endpoint.is_thread:
            await self.unregister_thread_handler(endpoint.id, endpoint.thread_id)