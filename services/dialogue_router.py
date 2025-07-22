"""
Dialogue message router for helpbot support system.
Simplified version - only routing, no business logic.
"""
import logging
from datetime import datetime
from typing import Dict, Any, Optional

from aiogram.types import Message
from aiogram.exceptions import TelegramAPIError

from core.message_service import MessageService, DialogueEndpoint
from services.command_processor import CommandProcessor
from core.db import get_db_session_ctx
from core.di import get_service
from core.input_service import InputService
from models.dialogue import Dialogue
from models.ticket import Ticket

logger = logging.getLogger(__name__)


class DialogueRouter:
    """
    Routes messages between dialogue participants.
    All command processing delegated to CommandProcessor.

    Responsibilities:
    - Route messages between client and operator
    - Delegate operator commands to CommandProcessor
    - Update dialogue activity timestamps
    """

    def __init__(self, message_service: MessageService):
        """
        Initialize dialogue router.

        Args:
            message_service: Message service for sending messages
        """
        self.message_service = message_service
        self.dialogue_service = None  # Will be set externally
        self.command_processor = None  # Will be created after dialogue_service is set

    def set_dialogue_service(self, dialogue_service):
        """Set dialogue service reference and create command processor."""
        self.dialogue_service = dialogue_service
        # Now we can create command processor with dialogue_service
        self.command_processor = CommandProcessor(dialogue_service, self.message_service)

    async def route_client_message(self, message: Message, dialogue_id: str) -> bool:
        """
        Route message from client to operator.

        Args:
            message: Client message
            dialogue_id: Dialogue ID

        Returns:
            bool: Success status
        """
        try:
            logger.info(
                f"Routing client message in dialogue {dialogue_id}: {message.text[:50] if message.text else '[Media]'}")

            # Update dialogue activity
            await self._update_dialogue_activity(dialogue_id)

            # Get dialogue info for routing
            dialogue_info = await self.dialogue_service.get_dialogue_info(dialogue_id)
            if not dialogue_info:
                logger.error(f"Dialogue {dialogue_id} not found")
                return False

            # Route to operator thread
            operator_endpoint = DialogueEndpoint('group', dialogue_info['group_id'], dialogue_info['thread_id'])

            # First, try to send message
            result = None

            if message.text:
                # Send text with template
                result = await self.message_service.send_template_to_endpoint(
                    endpoint=operator_endpoint,
                    template_key='/support/operator_client_message',
                    variables={
                        'client_name': 'Client',
                        'message': message.text,
                        'dialogue_id': dialogue_id
                    }
                )
            else:
                # Forward media directly
                result = await self.message_service.forward_message(
                    message=message,
                    to_endpoint=operator_endpoint,
                    with_comment="📥 Client: "
                )

            # If sending failed, check if we need to recreate thread
            if result is None:
                logger.warning(
                    f"Failed to send message to thread {dialogue_info['thread_id']}, checking if thread exists...")

                # Try to send a test message to check if thread exists
                try:
                    await self.message_service.bot.send_message(
                        chat_id=dialogue_info['group_id'],
                        message_thread_id=dialogue_info['thread_id'],
                        text="."  # Minimal test message
                    )
                    # If we're here, thread exists but something else is wrong
                    logger.error("Thread exists but message sending failed for other reason")
                    return False

                except TelegramAPIError as e:
                    if "thread not found" in str(e).lower() or "message thread not found" in str(e).lower():
                        logger.warning(f"Thread {dialogue_info['thread_id']} was deleted, recreating...")

                        # Recreate thread
                        new_thread_id = await self._recreate_dialogue_thread(dialogue_id, dialogue_info)

                        if new_thread_id:
                            # Update endpoint and retry
                            operator_endpoint = DialogueEndpoint('group', dialogue_info['group_id'], new_thread_id)

                            if message.text:
                                result = await self.message_service.send_template_to_endpoint(
                                    endpoint=operator_endpoint,
                                    template_key='/support/operator_client_message',
                                    variables={
                                        'client_name': 'Client',
                                        'message': message.text,
                                        'dialogue_id': dialogue_id
                                    }
                                )
                            else:
                                result = await self.message_service.forward_message(
                                    message=message,
                                    to_endpoint=operator_endpoint,
                                    with_comment="📥 Client: "
                                )

                            return result is not None
                        else:
                            logger.error(f"Failed to recreate thread for dialogue {dialogue_id}")
                            return False
                    else:
                        # Some other error
                        logger.error(f"Thread check failed: {e}")
                        return False

            return result is not None

        except Exception as e:
            logger.error(f"Error routing client message: {e}", exc_info=True)
            return False

    async def _recreate_dialogue_thread(self, dialogue_id: str, dialogue_info: Dict[str, Any]) -> Optional[int]:
        """
        Recreate deleted thread for dialogue.

        Args:
            dialogue_id: Dialogue ID
            dialogue_info: Current dialogue information

        Returns:
            New thread ID or None if failed
        """
        try:
            with get_db_session_ctx() as session:
                dialogue = session.query(Dialogue).filter_by(dialogueID=dialogue_id).first()
                if not dialogue:
                    return None

                # Get ticket info for topic name
                ticket = session.query(Ticket).filter_by(ticketID=dialogue.ticketID).first()
                if not ticket:
                    return None

                # Create new topic
                topic_name = f"Ticket #{ticket.ticketID} [RESTORED]"
                if ticket.category:
                    topic_name += f" [{ticket.category}]"

                bot = self.message_service.bot

                topic = await bot.create_forum_topic(
                    chat_id=dialogue_info['group_id'],
                    name=topic_name,
                    icon_color=0xFF93B2  # Pink color for restored topics
                )

                if not topic or not hasattr(topic, 'message_thread_id'):
                    return None

                new_thread_id = topic.message_thread_id

                # Update dialogue with new thread ID
                dialogue.threadID = new_thread_id
                session.commit()

                # Send notification about restoration
                await bot.send_message(
                    chat_id=dialogue_info['group_id'],
                    message_thread_id=new_thread_id,
                    text=f"⚠️ Thread was deleted and restored\n"
                         f"Dialogue: {dialogue_id}\n"
                         f"Client messages will continue here."
                )

                # Re-register handlers
                input_service = get_service(InputService)
                if input_service:
                    # Unregister old handler
                    await input_service.unregister_thread_handler(
                        dialogue_info['group_id'],
                        dialogue_info['thread_id']
                    )

                    # Register new handler
                    async def handle_operator_message(message):
                        await self.route_operator_message(message, dialogue_id)

                    await input_service.register_thread_handler(
                        group_id=dialogue_info['group_id'],
                        thread_id=new_thread_id,
                        handler=handle_operator_message
                    )

                logger.info(f"Successfully recreated thread {new_thread_id} for dialogue {dialogue_id}")
                return new_thread_id

        except Exception as e:
            logger.error(f"Error recreating thread: {e}", exc_info=True)
            return None

    async def route_operator_message(self, message: Message, dialogue_id: str) -> bool:
        """
        Route message from operator to client.

        Args:
            message: Operator message
            dialogue_id: Dialogue ID

        Returns:
            bool: Success status
        """
        try:
            logger.info(
                f"Routing operator message in dialogue {dialogue_id}: {message.text[:50] if message.text else '[Media]'}")

            # Update dialogue activity
            await self._update_dialogue_activity(dialogue_id)

            # Check if it's a command
            if message.text and message.text.startswith('&'):
                # Delegate to command processor
                return await self.command_processor.process_command(message, dialogue_id)

            # Regular message - route to client
            dialogue_info = await self.dialogue_service.get_dialogue_info(dialogue_id)
            if not dialogue_info:
                logger.error(f"Dialogue {dialogue_id} not found")
                return False

            client_endpoint = DialogueEndpoint('user', dialogue_info['client_telegram_id'])

            if message.text:
                # Send text with template
                await self.message_service.send_template_to_endpoint(
                    endpoint=client_endpoint,
                    template_key='/support/client_operator_message',
                    variables={
                        'operator_name': 'Support',
                        'message': message.text,
                        'dialogue_id': dialogue_id
                    }
                )
            else:
                # Forward media directly
                await self.message_service.forward_message(
                    message=message,
                    to_endpoint=client_endpoint,
                    with_comment="💬 Support: "
                )

            return True

        except Exception as e:
            logger.error(f"Error routing operator message: {e}", exc_info=True)
            return False

    async def _update_dialogue_activity(self, dialogue_id: str):
        """Update dialogue last activity time and message count."""
        try:
            with get_db_session_ctx() as session:
                dialogue = session.query(Dialogue).filter_by(dialogueID=dialogue_id).first()
                if dialogue:
                    dialogue.lastActivityTime = datetime.now()
                    dialogue.messageCount = (dialogue.messageCount or 0) + 1
                    session.commit()
                    logger.debug(f"Updated activity for dialogue {dialogue_id}")
        except Exception as e:
            logger.error(f"Error updating dialogue activity: {e}")