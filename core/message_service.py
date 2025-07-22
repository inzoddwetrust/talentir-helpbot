"""
Service for sending messages to different recipients with improved queueing.
"""
import logging
import asyncio
from typing import Dict, Any, Optional, List, Deque, Set
from collections import deque
from datetime import datetime

from aiogram import Bot
from aiogram.types import Message
from aiogram.exceptions import TelegramAPIError

from core.templates import MessageTemplates
from models.user import User
from core.db import get_db_session_ctx

logger = logging.getLogger(__name__)


class MessageQueue:
    """Queue for handling message sending with rate limiting."""

    def __init__(self, max_per_minute: int = 30, burst_limit: int = 5):
        """
        Initialize message queue with rate limiting.

        Args:
            max_per_minute: Maximum messages per minute
            burst_limit: Maximum messages to send in burst
        """
        self.queue: Deque[Dict[str, Any]] = deque()
        self.max_per_minute = max_per_minute
        self.burst_limit = burst_limit
        self.processing = False
        self.sent_in_last_minute: List[float] = []
        self.pending_tasks: Set[asyncio.Task] = set()

    async def add_message(self, message_data: Dict[str, Any]) -> None:
        """
        Add message to the queue and start processing if not already running.

        Args:
            message_data: Dictionary with message data and callback
        """
        self.queue.append(message_data)
        logger.debug(f"Added message to queue. Queue size: {len(self.queue)}")

        if not self.processing:
            self.processing = True
            task = asyncio.create_task(self._process_queue())
            self.pending_tasks.add(task)
            task.add_done_callback(self.pending_tasks.discard)

    async def _process_queue(self) -> None:
        """Process message queue with rate limiting."""
        try:
            while self.queue:
                # Apply rate limiting
                now = datetime.now().timestamp()
                # Remove timestamps older than 1 minute
                self.sent_in_last_minute = [t for t in self.sent_in_last_minute if now - t < 60]

                # Check if we've reached the rate limit
                if len(self.sent_in_last_minute) >= self.max_per_minute:
                    wait_time = 60 - (now - self.sent_in_last_minute[0])
                    logger.warning(f"Rate limit reached. Waiting {wait_time:.2f} seconds")
                    await asyncio.sleep(wait_time)
                    continue

                # Send messages in bursts up to burst_limit
                burst_count = min(len(self.queue), self.burst_limit)

                for _ in range(burst_count):
                    if not self.queue:
                        break

                    message_data = self.queue.popleft()
                    send_callback = message_data.pop('callback')
                    message_id = message_data.pop('message_id', None)

                    # ИЗМЕНЕНИЕ: Проверяем, содержит ли сообщение объект user
                    if 'user' in message_data:
                        try:
                            user_obj = message_data['user']
                            telegram_id = user_obj.telegramID

                            # Получаем свежий объект User из базы данных
                            with get_db_session_ctx() as session:
                                fresh_user = session.query(User).filter_by(telegramID=telegram_id).first()
                                if fresh_user:
                                    message_data['user'] = fresh_user
                                else:
                                    logger.warning(f"User with telegram ID {telegram_id} not found")
                                    continue
                        except Exception as e:
                            logger.error(f"Error refreshing user object: {e}")
                            continue

                    try:
                        await send_callback(**message_data)
                        self.sent_in_last_minute.append(datetime.now().timestamp())
                        if message_id:
                            logger.info(f"Sent queued message {message_id}. Queue size: {len(self.queue)}")
                    except TelegramAPIError as e:
                        logger.error(f"Failed to send queued message: {e}")

                # Small delay between bursts
                await asyncio.sleep(0.1)

        except Exception as e:
            logger.error(f"Error in message queue processor: {e}", exc_info=True)
        finally:
            self.processing = False
            # If new messages were added during exception handling, restart processing
            if self.queue and not self.processing:
                self.processing = True
                task = asyncio.create_task(self._process_queue())
                self.pending_tasks.add(task)
                task.add_done_callback(self.pending_tasks.discard)


class DialogueEndpoint:
    """Represents a communication endpoint for a dialogue participant."""

    def __init__(self, type_: str, id_: int, thread_id: int = None):
        """
        Initialize dialogue endpoint.

        Args:
            type_: Type of endpoint ('user', 'group', 'channel')
            id_: Chat ID
            thread_id: Optional thread ID for forum topics
        """
        self.type = type_
        self.id = id_
        self.thread_id = thread_id

    @property
    def is_thread(self):
        """Check if this endpoint is a thread in a group."""
        return self.type == 'group' and self.thread_id is not None

    def get_send_params(self):
        """Get parameters for sending messages to this endpoint."""
        params = {'chat_id': self.id}
        if self.is_thread:
            params['message_thread_id'] = self.thread_id
        return params


class MessageService:
    """
    Service for sending messages to different recipients with queueing.
    """

    def __init__(self, bot: Bot, templates_manager=None):
        """
        Initialize message service.

        Args:
            bot: Bot instance
            templates_manager: Templates manager (default: MessageTemplates)
        """
        self.bot = bot
        self.templates_manager = templates_manager or MessageTemplates
        self.sent_messages = {}  # message_id -> message_info
        self.message_queue = MessageQueue()

        # Statistics for monitoring
        self.stats = {
            'total_sent': 0,
            'total_failed': 0,
            'total_forwarded': 0,
            'last_send_time': datetime.now(),  # Инициализируем с datetime
            'queue_overflow_count': 0
        }

    def get_endpoint_for_telegram_id(self, telegram_id: int) -> DialogueEndpoint:
        """Create an endpoint for telegram ID."""
        return DialogueEndpoint('user', telegram_id)

    async def send_template_to_user(self, user: User, template_key: str,
                                    variables: Dict = None, media_id: str = None,
                                    edit_message_id: int = None,
                                    priority: int = 0) -> Optional[Message]:
        """
        Send a template-based message to a user with queueing support.

        Args:
            user: User object
            template_key: Template key
            variables: Template variables
            media_id: Optional media ID
            edit_message_id: Optional message ID to edit
            priority: Message priority (higher = more important)

        Returns:
            Message: Sent message or None on error
        """
        from core.message_manager import MessageManager

        try:
            start_time = datetime.now()

            # Create an instance of MessageManager for this operation
            message_manager = MessageManager(self.bot)

            # Prepare update object (needed for MessageManager)
            from core.fake_entities import create_fake_message
            if edit_message_id:
                # Create a fake update object for editing
                update = create_fake_message(
                    chat_id=user.telegramID,
                    message_id=edit_message_id,
                    from_user_id=user.telegramID
                )
            else:
                # Create a minimal fake message for new messages
                update = create_fake_message(
                    chat_id=user.telegramID,
                    message_id=0,  # Dummy message_id
                    from_user_id=user.telegramID,
                    text=""  # Empty text for new messages
                )

            # Generate a unique message ID for tracking
            unique_message_id = f"user_{user.telegramID}_{datetime.now().timestamp()}"

            # Log the request
            logger.debug(
                f"Preparing to send template {template_key} to user {user.telegramID}"
                f"{' (edit)' if edit_message_id else ''}"
            )

            # Queue the message sending
            await self.message_queue.add_message({
                'callback': message_manager.send_template,
                'message_id': unique_message_id,
                'user': user,
                'template_key': template_key,
                'update': update,
                'variables': variables,
                'override_media_id': media_id,
                'edit': bool(edit_message_id)
            })

            # For now, we can't return the actual message since it's queued
            # We could improve this in the future with a callback system
            # For now, return None with the understanding it's being processed
            preparation_time = (datetime.now() - start_time).total_seconds()
            logger.info(
                f"Queued template {template_key} to user {user.telegramID} "
                f"in {preparation_time:.3f}s"
            )

            self.stats['total_sent'] += 1
            self.stats['last_send_time'] = datetime.now()

            return None

        except Exception as e:
            self.stats['total_failed'] += 1
            logger.error(f"Error queuing template to user {user.telegramID}: {e}")
            return None

    async def send_template_to_endpoint(self, endpoint: DialogueEndpoint,
                                        template_key: str, variables: Dict = None,
                                        media_id: str = None,
                                        priority: int = 0) -> Optional[Message]:
        """
        Send a template-based message to an endpoint with queueing.

        Args:
            endpoint: Dialogue endpoint
            template_key: Template key
            variables: Template variables
            media_id: Optional media ID
            priority: Message priority (higher = more important)

        Returns:
            Message: Sent message or None on error
        """
        try:
            start_time = datetime.now()

            # For user endpoints, get User object and use send_template_to_user
            if endpoint.type == 'user':
                # Получаем raw_template напрямую, чтобы избежать проблем с User
                raw_template = await self.templates_manager.get_raw_template(
                    template_key, variables=variables or {})

                if not raw_template:
                    logger.error(f"Failed to get template {template_key}")
                    return None

                text, buttons_str = raw_template

                # Create keyboard if buttons defined
                keyboard = None
                if buttons_str:
                    keyboard = self.templates_manager.create_keyboard(buttons_str, variables=variables)

                # ОТПРАВЛЯЕМ СООБЩЕНИЕ НАПРЯМУЮ вместо постановки в очередь
                params = {'chat_id': endpoint.id}

                if media_id:
                    # С медиа
                    result = await self.bot.send_photo(
                        **params,
                        photo=media_id,
                        caption=text,
                        reply_markup=keyboard,
                        parse_mode='HTML'
                    )
                else:
                    # Только текст
                    result = await self.bot.send_message(
                        **params,
                        text=text,
                        reply_markup=keyboard,
                        parse_mode='HTML'
                    )

                preparation_time = (datetime.now() - start_time).total_seconds()
                logger.info(
                    f"Sent template {template_key} to {endpoint.type}/{endpoint.id} "
                    f"in {preparation_time:.3f}s"
                )

                self.stats['total_sent'] += 1
                self.stats['last_send_time'] = datetime.now()

                return result

            # Для других типов (группы, треды) - тот же код
            # Generate a unique message ID for tracking
            unique_message_id = f"{endpoint.type}_{endpoint.id}_{datetime.now().timestamp()}"

            # Get raw template (text and buttons)
            raw_template = await self.templates_manager.get_raw_template(
                template_key, variables=variables or {})

            text, buttons_str = raw_template

            # Create keyboard if buttons defined
            keyboard = None
            if buttons_str:
                keyboard = self.templates_manager.create_keyboard(buttons_str, variables=variables)

            # Prepare send parameters
            params = endpoint.get_send_params()

            # Log the request
            logger.debug(
                f"Preparing to send template {template_key} to {endpoint.type}/{endpoint.id}"
            )

            # ОТПРАВЛЯЕМ СООБЩЕНИЕ НАПРЯМУЮ
            if media_id:
                # С медиа
                result = await self.bot.send_photo(
                    **params,
                    photo=media_id,
                    caption=text,
                    reply_markup=keyboard,
                    parse_mode='HTML'
                )
            else:
                # Только текст
                result = await self.bot.send_message(
                    **params,
                    text=text,
                    reply_markup=keyboard,
                    parse_mode='HTML'
                )

            preparation_time = (datetime.now() - start_time).total_seconds()
            logger.info(
                f"Sent template {template_key} to {endpoint.type}/{endpoint.id} "
                f"in {preparation_time:.3f}s"
            )

            self.stats['total_sent'] += 1
            self.stats['last_send_time'] = datetime.now()

            return result

        except Exception as e:
            self.stats['total_failed'] += 1
            logger.error(f"Error sending template to endpoint {endpoint.type}/{endpoint.id}: {e}")
            return None

    async def forward_message(self, message: Message, to_endpoint: DialogueEndpoint,
                              with_comment: Optional[str] = None,
                              priority: int = 0) -> Optional[Message]:
        """
        Forward a message to an endpoint with queueing.

        Args:
            message: Message to forward
            to_endpoint: Destination endpoint
            with_comment: Optional comment to prepend
            priority: Message priority (higher = more important)

        Returns:
            Message: Forwarded message or None on error
        """
        try:
            params = to_endpoint.get_send_params()
            unique_message_id = f"{to_endpoint.type}_{to_endpoint.id}_{datetime.now().timestamp()}"

            # If comment provided, queue it first
            if with_comment:
                comment_params = {
                    'callback': self.bot.send_message,
                    'message_id': f"{unique_message_id}_comment",  # для трекинга
                    **params,
                    'text': with_comment
                }
                await self.message_queue.add_message(comment_params)

            # Queue the forward - используем строго те параметры, которые нужны bot.forward_message
            forward_data = {
                'callback': self.bot.forward_message,
                'message_id': unique_message_id,  # для трекинга - будет удален в очереди
                'chat_id': params['chat_id'],
                'from_chat_id': message.chat.id,
                'message_id': message.message_id,  # Это дублируется! Нужно другое решение
            }

            # Проблема: у нас два message_id! Нужно использовать обертку
            async def forward_wrapper(**kwargs):
                # Восстанавливаем правильный message_id
                return await self.bot.forward_message(
                    chat_id=kwargs['chat_id'],
                    from_chat_id=kwargs['from_chat_id'],
                    message_id=kwargs['forward_message_id'],
                    message_thread_id=kwargs.get('message_thread_id')
                )

            # Теперь используем обертку
            await self.message_queue.add_message({
                'callback': forward_wrapper,
                'message_id': unique_message_id,  # для трекинга
                'chat_id': params['chat_id'],
                'from_chat_id': message.chat.id,
                'forward_message_id': message.message_id,  # используем другое имя!
                'message_thread_id': params.get('message_thread_id')
            })

            logger.info(
                f"Queued message forward from {message.chat.id}/{message.message_id} "
                f"to {to_endpoint.type}/{to_endpoint.id}"
            )

            self.stats['total_forwarded'] += 1

            return None

        except Exception as e:
            self.stats['total_failed'] += 1
            logger.error(f"Error queuing message forward to {to_endpoint.type}/{to_endpoint.id}: {e}")
            return None

    async def delete_message(self, message_id: str) -> bool:
        """
        Delete a previously sent message.

        Args:
            message_id: Tracked message ID (format: type_id_messageid)

        Returns:
            bool: Success status
        """
        try:
            sent_info = self.sent_messages.get(message_id)
            if not sent_info:
                logger.warning(f"Message {message_id} not found for deletion")
                return False

            message = sent_info['message']

            await self.bot.delete_message(
                chat_id=message.chat.id,
                message_id=message.message_id
            )

            # Remove from tracking
            del self.sent_messages[message_id]
            return True

        except TelegramAPIError as e:
            if "message to delete not found" in str(e):
                # Message already deleted, consider it success
                if message_id in self.sent_messages:
                    del self.sent_messages[message_id]
                return True

            logger.error(f"Error deleting message {message_id}: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error deleting message {message_id}: {e}")
            return False

    async def get_queue_stats(self) -> Dict[str, Any]:
        """
        Get stats about the message queue.

        Returns:
            Dict with queue statistics
        """
        return {
            **self.stats,
            'queue_size': len(self.message_queue.queue),
            'queue_active': self.message_queue.processing,
            'messages_sent_last_minute': len(self.message_queue.sent_in_last_minute)
        }

    async def send_template_to_telegram_id(self, telegram_id: int,
                                         template_key: str, variables: Dict = None,
                                         media_id: str = None, edit_message_id: int = None,
                                         priority: int = 0) -> Optional[Message]:
        """
        Send template to user by telegram ID without requiring a User object.

        Args:
            telegram_id: User's Telegram ID
            template_key: Template key
            variables: Template variables
            media_id: Optional media ID
            edit_message_id: Optional message ID to edit
            priority: Message priority (higher = more important)

        Returns:
            Message: Sent message or None on error
        """
        with get_db_session_ctx() as session:
            user = session.query(User).filter_by(telegramID=telegram_id).first()
            if not user:
                logger.error(f"User with telegram ID {telegram_id} not found")
                return None

            # Теперь у нас есть активный объект User в рамках этой сессии
            return await self.send_template_to_user(
                user=user,
                template_key=template_key,
                variables=variables,
                media_id=media_id,
                edit_message_id=edit_message_id,
                priority=priority
            )