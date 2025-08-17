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
        self._filter_id = id(filter_func)  # For tracking in logs

    async def __call__(self, message: Message) -> bool:
        try:
            if callable(self.filter_func):
                if asyncio.iscoroutinefunction(self.filter_func):
                    result = await self.filter_func(message)
                else:
                    result = self.filter_func(message)

                logger.debug(
                    f"[FILTER] Filter {self._filter_id} for message from "
                    f"{message.from_user.id if message.from_user else 'Unknown'}: "
                    f"result={result}"
                )
                return result
            return False
        except Exception as e:
            logger.error(f"[FILTER] Error in filter {self._filter_id}: {e}")
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
        self.handlers = {}  # handler_id -> handler info
        self._handler_counter = 0  # For unique handler IDs

        logger.info(f"[INPUT_SERVICE] Initialized with router: {self.router.name}")

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
        handler_id = f"user_{user_id}_{state or 'any'}"
        self._handler_counter += 1
        handler_unique_id = f"{handler_id}_{self._handler_counter}"

        # Count current handlers in router
        current_handler_count = len(self.router.message.handlers)

        logger.info(
            f"[REGISTER_USER] Starting registration: "
            f"handler_id='{handler_id}', unique_id='{handler_unique_id}', "
            f"user_id={user_id}, state='{state}', types={message_types}. "
            f"Router currently has {current_handler_count} handlers"
        )

        # Check and remove old handler if exists
        if handler_id in self.handlers:
            logger.warning(
                f"[REGISTER_USER] ⚠️ Handler '{handler_id}' already exists! "
                f"Removing old handler before registering new one."
            )
            await self.unregister_user_handler(user_id, state)

        def user_filter(message: Message) -> bool:

            if message.from_user and message.from_user.is_bot:
                logger.debug(
                    f"[USER_FILTER] Filter for {handler_id}: "
                    f"Ignoring bot message from {message.from_user.id}"
                )
                return False

            # Check if message is from the right user
            if not message.from_user or message.from_user.id != user_id:
                logger.debug(
                    f"[USER_FILTER] Filter for {handler_id}: "
                    f"Wrong user (expected {user_id}, got {message.from_user.id if message.from_user else None})"
                )
                return False

            # SKIP ADMIN COMMANDS - они имеют приоритет!
            if message.text and message.text.startswith('&'):
                logger.debug(f"[USER_FILTER] Filter for {handler_id}: Skipping admin command")
                return False

            # Check message type if specified
            if message_types:
                has_type = any(getattr(message, msg_type, None) is not None for msg_type in message_types)
                if not has_type:
                    logger.debug(f"[USER_FILTER] Filter for {handler_id}: Message type not in {message_types}")
                    return False

            # Check state if needed
            if state:
                with get_db_session_ctx() as session:
                    user = session.query(User).filter_by(telegramID=user_id).first()
                    if not user:
                        logger.debug(f"[USER_FILTER] Filter for {handler_id}: User not found in DB")
                        return False

                    user_fsm_state = user.get_fsm_state()
                    if user_fsm_state != state:
                        logger.debug(
                            f"[USER_FILTER] Filter for {handler_id}: "
                            f"State mismatch (expected '{state}', got '{user_fsm_state}')"
                        )
                        return False

            logger.info(
                f"[USER_FILTER] Filter for {handler_id} PASSED: "
                f"message from user {user_id} accepted"
            )
            return True

        async def user_message_handler(message: Message):
            logger.info(
                f"[USER_HANDLER] Handler {handler_unique_id} triggered for user {user_id}: "
                f"{message.text[:50] if message.text else '[Media]'}"
            )

            # Log handler closure details
            logger.debug(
                f"[USER_HANDLER] Handler details: "
                f"handler_func={handler}, "
                f"handler_id_in_closure='{handler_id}'"
            )

            try:
                await handler(message)
                logger.debug(f"[USER_HANDLER] Handler {handler_unique_id} completed successfully")
            except Exception as e:
                logger.error(f"[USER_HANDLER] Error in handler {handler_unique_id} for user {user_id}: {e}", exc_info=True)

        # Create filter object
        filter_obj = SimpleFilter(user_filter)

        # Register handler using router's register method
        # This returns the HandlerObject that was created
        self.router.message.register(user_message_handler, filter_obj)

        # Get the handler that was just added (it's the last one)
        new_handler_count = len(self.router.message.handlers)
        if new_handler_count > current_handler_count:
            # Handler was added, it's the last one in the list
            handler_obj = self.router.message.handlers[-1]
        else:
            # Something went wrong
            logger.error(f"[REGISTER_USER] Failed to register handler!")
            handler_obj = None

        # Store handler info for later removal
        self.handlers[handler_id] = {
            'handler_object': handler_obj,  # The actual handler in the router
            'filter_object': filter_obj,
            'handler_func': user_message_handler,
            'original_handler': handler,
            'unique_id': handler_unique_id,
            'user_id': user_id,
            'state': state,
            'message_types': message_types,
            'registered_at': asyncio.get_event_loop().time()
        }

        logger.info(
            f"[REGISTER_USER] ✅ Registered handler '{handler_id}' (unique: {handler_unique_id}). "
            f"Total handlers in dict: {len(self.handlers)}. "
            f"Total handlers in Router: {len(self.router.message.handlers)}"
        )

        # Log all current handlers
        logger.debug(
            f"[REGISTER_USER] Current handlers in dict: "
            f"{list(self.handlers.keys())}"
        )

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
        handler_id = f"thread_{group_id}_{thread_id}"
        self._handler_counter += 1
        handler_unique_id = f"{handler_id}_{self._handler_counter}"

        # Count current handlers in router
        current_handler_count = len(self.router.message.handlers)

        logger.info(
            f"[REGISTER_THREAD] Starting registration: "
            f"handler_id='{handler_id}', unique_id='{handler_unique_id}', "
            f"group={group_id}, thread={thread_id}, types={message_types}. "
            f"Router currently has {current_handler_count} handlers"
        )

        # Check and remove old handler if exists
        if handler_id in self.handlers:
            logger.warning(
                f"[REGISTER_THREAD] ⚠️ Handler '{handler_id}' already exists! "
                f"Removing old handler before registering new one."
            )
            await self.unregister_thread_handler(group_id, thread_id)

        def thread_filter(message: Message) -> bool:
            # Ignore bot's own messages
            if message.from_user and message.from_user.is_bot:
                logger.debug(
                    f"[THREAD_FILTER] Filter for {handler_id}: "
                    f"Ignoring bot message from {message.from_user.id}"
                )
                return False

            # Check if message is in the right group
            if message.chat.id != group_id:
                logger.debug(
                    f"[THREAD_FILTER] Filter for {handler_id}: "
                    f"Wrong group (expected {group_id}, got {message.chat.id})"
                )
                return False

            # Check if message has thread_id
            if not hasattr(message, 'message_thread_id'):
                logger.debug(f"[THREAD_FILTER] Filter for {handler_id}: No message_thread_id attribute")
                return False

            # Check if it's the right thread
            if message.message_thread_id != thread_id:
                logger.debug(
                    f"[THREAD_FILTER] Filter for {handler_id}: "
                    f"Wrong thread (expected {thread_id}, got {message.message_thread_id})"
                )
                return False

            # Check message type if specified
            if message_types:
                has_type = any(getattr(message, msg_type, None) is not None for msg_type in message_types)
                if not has_type:
                    logger.debug(f"[THREAD_FILTER] Filter for {handler_id}: Message type not in {message_types}")
                    return False

            logger.info(
                f"[THREAD_FILTER] Filter for {handler_id} PASSED: "
                f"message in thread {group_id}/{thread_id} accepted"
            )
            return True

        async def thread_message_handler(message: Message):
            logger.info(
                f"[THREAD_HANDLER] Handler {handler_unique_id} triggered in thread {group_id}/{thread_id}"
            )
            logger.info(
                f"[THREAD_HANDLER] From user {message.from_user.id if message.from_user else 'None'}: "
                f"{message.text[:50] if message.text else '[Media]'}"
            )
            logger.debug(
                f"[THREAD_HANDLER] Has photo: {bool(message.photo)}, "
                f"video: {bool(message.video)}, document: {bool(message.document)}"
            )

            # Additional check for active dialogue
            from models.dialogue import Dialogue
            with get_db_session_ctx() as session:
                dialogue = session.query(Dialogue).filter_by(
                    groupID=group_id,
                    threadID=thread_id,
                    status='active'
                ).first()

                if not dialogue:
                    logger.warning(
                        f"[THREAD_HANDLER] Handler called for closed/missing dialogue in {group_id}/{thread_id}"
                    )
                    return

                logger.debug(
                    f"[THREAD_HANDLER] Found active dialogue: {dialogue.dialogueID}"
                )

            try:
                await handler(message)
                logger.debug(f"[THREAD_HANDLER] Handler {handler_unique_id} completed successfully")
            except Exception as e:
                logger.error(f"[THREAD_HANDLER] Error in handler {handler_unique_id} for {group_id}/{thread_id}: {e}", exc_info=True)

        # Create filter object
        filter_obj = SimpleFilter(thread_filter)

        # Register handler using router's register method
        self.router.message.register(thread_message_handler, filter_obj)

        # Get the handler that was just added (it's the last one)
        new_handler_count = len(self.router.message.handlers)
        if new_handler_count > current_handler_count:
            # Handler was added, it's the last one in the list
            handler_obj = self.router.message.handlers[-1]
        else:
            # Something went wrong
            logger.error(f"[REGISTER_THREAD] Failed to register handler!")
            handler_obj = None

        # Store handler info for later removal
        self.handlers[handler_id] = {
            'handler_object': handler_obj,  # The actual handler in the router
            'filter_object': filter_obj,
            'handler_func': thread_message_handler,
            'original_handler': handler,
            'unique_id': handler_unique_id,
            'group_id': group_id,
            'thread_id': thread_id,
            'message_types': message_types,
            'registered_at': asyncio.get_event_loop().time()
        }

        logger.info(
            f"[REGISTER_THREAD] ✅ Registered handler '{handler_id}' (unique: {handler_unique_id}). "
            f"Total handlers in dict: {len(self.handlers)}. "
            f"Total handlers in Router: {len(self.router.message.handlers)}"
        )

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
        logger.debug(
            f"[REGISTER_ENDPOINT] Registering for {endpoint.type} {endpoint.id} "
            f"{'thread ' + str(endpoint.thread_id) if endpoint.thread_id else ''}"
        )

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
            logger.warning(f"[REGISTER_ENDPOINT] Unsupported endpoint type: {endpoint.type}")

    async def unregister_user_handler(self, user_id: int, state: str = None):
        """
        Remove user handler - NOW PROPERLY!

        Args:
            user_id: User Telegram ID
            state: Optional FSM state
        """
        handler_id = f"user_{user_id}_{state or 'any'}"

        logger.info(
            f"[UNREGISTER_USER] Attempting to unregister handler '{handler_id}' "
            f"for user {user_id}, state='{state}'. "
            f"Router currently has {len(self.router.message.handlers)} handlers"
        )

        if handler_id in self.handlers:
            handler_info = self.handlers[handler_id]
            logger.info(
                f"[UNREGISTER_USER] Found handler '{handler_id}' in dict: "
                f"unique_id={handler_info['unique_id']}, "
                f"registered_at={handler_info['registered_at']}"
            )

            # УДАЛЯЕМ ИЗ ROUTER!
            if 'handler_object' in handler_info and handler_info['handler_object']:
                try:
                    self.router.message.handlers.remove(handler_info['handler_object'])
                    logger.info(
                        f"[UNREGISTER_USER] ✅ Successfully removed handler '{handler_id}' from Router! "
                        f"Router now has {len(self.router.message.handlers)} handlers"
                    )
                except ValueError as e:
                    logger.error(
                        f"[UNREGISTER_USER] ❌ Handler object not found in Router! "
                        f"This shouldn't happen. Error: {e}"
                    )
            else:
                logger.warning(
                    f"[UNREGISTER_USER] ⚠️ No handler_object stored for '{handler_id}', "
                    f"cannot remove from Router!"
                )

            # Удаляем из нашего словаря
            del self.handlers[handler_id]

            logger.info(
                f"[UNREGISTER_USER] Handler '{handler_id}' fully removed. "
                f"Remaining handlers in dict: {len(self.handlers)}"
            )
        else:
            logger.warning(
                f"[UNREGISTER_USER] Handler '{handler_id}' not found in dict. "
                f"Available handlers: {list(self.handlers.keys())}"
            )

    async def unregister_thread_handler(self, group_id: int, thread_id: int):
        """
        Remove thread handler - NOW PROPERLY!

        Args:
            group_id: Group chat ID
            thread_id: Thread ID
        """
        handler_id = f"thread_{group_id}_{thread_id}"

        logger.info(
            f"[UNREGISTER_THREAD] Attempting to unregister handler '{handler_id}' "
            f"for thread {group_id}/{thread_id}. "
            f"Router currently has {len(self.router.message.handlers)} handlers"
        )

        if handler_id in self.handlers:
            handler_info = self.handlers[handler_id]
            logger.info(
                f"[UNREGISTER_THREAD] Found handler '{handler_id}' in dict: "
                f"unique_id={handler_info['unique_id']}, "
                f"registered_at={handler_info['registered_at']}"
            )

            # УДАЛЯЕМ ИЗ ROUTER!
            if 'handler_object' in handler_info and handler_info['handler_object']:
                try:
                    self.router.message.handlers.remove(handler_info['handler_object'])
                    logger.info(
                        f"[UNREGISTER_THREAD] ✅ Successfully removed handler '{handler_id}' from Router! "
                        f"Router now has {len(self.router.message.handlers)} handlers"
                    )
                except ValueError as e:
                    logger.error(
                        f"[UNREGISTER_THREAD] ❌ Handler object not found in Router! "
                        f"This shouldn't happen. Error: {e}"
                    )
            else:
                logger.warning(
                    f"[UNREGISTER_THREAD] ⚠️ No handler_object stored for '{handler_id}', "
                    f"cannot remove from Router!"
                )

            # Удаляем из нашего словаря
            del self.handlers[handler_id]

            logger.info(
                f"[UNREGISTER_THREAD] Handler '{handler_id}' fully removed. "
                f"Remaining handlers in dict: {len(self.handlers)}"
            )
        else:
            logger.warning(
                f"[UNREGISTER_THREAD] Handler '{handler_id}' not found in dict. "
                f"Available handlers: {list(self.handlers.keys())}"
            )

    async def unregister_endpoint_handler(self, endpoint):
        """
        Unregister handler for an endpoint.

        Args:
            endpoint: DialogueEndpoint
        """
        logger.debug(f"[UNREGISTER_ENDPOINT] Unregistering for {endpoint.type} {endpoint.id}")

        if endpoint.type == 'user':
            await self.unregister_user_handler(endpoint.id)
        elif endpoint.is_thread:
            await self.unregister_thread_handler(endpoint.id, endpoint.thread_id)

    async def cleanup_user_handlers(self, user_id: int):
        """
        Remove ALL handlers for a specific user.

        Args:
            user_id: User Telegram ID
        """
        logger.info(
            f"[CLEANUP_USER] Starting cleanup for user {user_id}. "
            f"Router has {len(self.router.message.handlers)} handlers, "
            f"dict has {len(self.handlers)} handlers"
        )

        # Find all handler IDs for this user
        user_handler_ids = [
            handler_id for handler_id in self.handlers.keys()
            if handler_id.startswith(f"user_{user_id}_")
        ]

        if not user_handler_ids:
            logger.info(f"[CLEANUP_USER] No handlers found for user {user_id}")
            return

        logger.info(
            f"[CLEANUP_USER] Found {len(user_handler_ids)} handlers for user {user_id}: "
            f"{user_handler_ids}"
        )

        removed_count = 0
        failed_count = 0

        for handler_id in user_handler_ids:
            handler_info = self.handlers.get(handler_id)
            if not handler_info:
                continue

            logger.debug(
                f"[CLEANUP_USER] Removing handler '{handler_id}': "
                f"state='{handler_info.get('state')}', "
                f"unique_id={handler_info.get('unique_id')}"
            )

            # Remove from Router
            if 'handler_object' in handler_info and handler_info['handler_object']:
                try:
                    self.router.message.handlers.remove(handler_info['handler_object'])
                    removed_count += 1
                    logger.debug(f"[CLEANUP_USER] Removed '{handler_id}' from Router")
                except ValueError as e:
                    failed_count += 1
                    logger.error(f"[CLEANUP_USER] Failed to remove '{handler_id}' from Router: {e}")

            # Remove from dict
            del self.handlers[handler_id]

        logger.info(
            f"[CLEANUP_USER] ✅ Cleanup complete for user {user_id}: "
            f"removed {removed_count} handlers, failed {failed_count}. "
            f"Router now has {len(self.router.message.handlers)} handlers, "
            f"dict has {len(self.handlers)} handlers"
        )

    def get_user_handlers(self, user_id: int) -> List[Dict[str, Any]]:
        """
        Get all handlers for a specific user (for debugging).

        Args:
            user_id: User Telegram ID

        Returns:
            List of handler info dictionaries
        """
        user_handlers = []
        for handler_id, handler_info in self.handlers.items():
            if handler_id.startswith(f"user_{user_id}_"):
                user_handlers.append({
                    'handler_id': handler_id,
                    'state': handler_info.get('state'),
                    'unique_id': handler_info.get('unique_id'),
                    'registered_at': handler_info.get('registered_at'),
                    'has_router_object': bool(handler_info.get('handler_object'))
                })
        return user_handlers

    def get_all_handlers_stats(self) -> Dict[str, Any]:
        """
        Get statistics about all handlers (for debugging).

        Returns:
            Dictionary with handler statistics
        """
        user_handlers = sum(1 for h in self.handlers if h.startswith('user_'))
        thread_handlers = sum(1 for h in self.handlers if h.startswith('thread_'))

        # Group by user
        users_with_handlers = {}
        for handler_id in self.handlers:
            if handler_id.startswith('user_'):
                parts = handler_id.split('_')
                if len(parts) >= 2:
                    user_id = parts[1]
                    users_with_handlers[user_id] = users_with_handlers.get(user_id, 0) + 1

        return {
            'total_in_router': len(self.router.message.handlers),
            'total_in_dict': len(self.handlers),
            'user_handlers': user_handlers,
            'thread_handlers': thread_handlers,
            'users_with_handlers': users_with_handlers,
            'potential_zombies': len(self.router.message.handlers) - len(self.handlers)
        }