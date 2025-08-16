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
from models.user import User

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
                f"[ROUTE_CLIENT] Starting routing for dialogue {dialogue_id}, "
                f"from user {message.from_user.id}: {message.text[:50] if message.text else '[Media]'}"
            )

            # ENHANCED CHECK: verify dialogue_id consistency with user's FSM and dialogue status
            with get_db_session_ctx() as session:
                user = session.query(User).filter_by(telegramID=message.from_user.id).first()
                if user:
                    fsm_state = user.get_fsm_state()
                    fsm_context = user.get_fsm_context()
                    fsm_dialogue_id = fsm_context.get('dialogue_id') if fsm_context else None

                    logger.info(
                        f"[ROUTE_CLIENT] FSM check for user {message.from_user.id}: "
                        f"state='{fsm_state}', FSM dialogue='{fsm_dialogue_id}', "
                        f"routing to dialogue='{dialogue_id}'"
                    )

                    # DETECT DESYNC!
                    if fsm_dialogue_id and fsm_dialogue_id != dialogue_id:
                        logger.error(
                            f"[ROUTE_CLIENT] ⚠️ DESYNC DETECTED! User {message.from_user.id} "
                            f"FSM has dialogue '{fsm_dialogue_id}' but routing to '{dialogue_id}'. "
                            f"Using FSM dialogue instead!"
                        )
                        # FIX: use dialogue_id from FSM as source of truth
                        dialogue_id = fsm_dialogue_id

                    # NEW CHECK: verify dialogue exists and is active
                    dialogue = session.query(Dialogue).filter_by(
                        dialogueID=dialogue_id,
                        status='active'
                    ).first()

                    if not dialogue:
                        logger.error(
                            f"[ROUTE_CLIENT] Dialogue {dialogue_id} not found or inactive. "
                            f"Clearing FSM for user {message.from_user.id}"
                        )

                        # Clear FSM
                        user.clear_fsm()
                        session.commit()

                        # Send notification to user
                        await self.message_service.send_template_to_telegram_id(
                            telegram_id=message.from_user.id,
                            template_key='/support/ticket_closed_while_typing',
                            variables={'dialogue_id': dialogue_id}
                        )
                        return False

                    # If dialogue is active, save its data for use outside session
                    dialogue_group_id = dialogue.groupID
                    dialogue_thread_id = dialogue.threadID

                    logger.debug(
                        f"[ROUTE_CLIENT] Dialogue verified: "
                        f"status={dialogue.status}, state={dialogue.state}, "
                        f"group={dialogue_group_id}, thread={dialogue_thread_id}"
                    )
                else:
                    logger.warning(f"[ROUTE_CLIENT] User {message.from_user.id} not found in DB")
                    return False

            # Update dialogue activity
            await self._update_dialogue_activity(dialogue_id)

            # Route to operator thread using saved data
            operator_endpoint = DialogueEndpoint('group', dialogue_group_id, dialogue_thread_id)

            logger.info(
                f"[ROUTE_CLIENT] Routing message to operator thread: "
                f"group={dialogue_group_id}, thread={dialogue_thread_id}"
            )

            # Try to send message
            result = None

            if message.text:
                # Send text with template
                logger.debug(f"[ROUTE_CLIENT] Sending text message via template")
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
                logger.debug(f"[ROUTE_CLIENT] Forwarding media message")
                result = await self.message_service.forward_message(
                    message=message,
                    to_endpoint=operator_endpoint,
                    with_comment="📥 Client: "
                )

            # If sending failed, check if we need to recreate thread
            if result is None:
                logger.warning(
                    f"[ROUTE_CLIENT] Failed to send message to thread {dialogue_thread_id}, "
                    f"checking if thread exists..."
                )

                # Try to send a test message to check if thread exists
                try:
                    await self.message_service.bot.send_message(
                        chat_id=dialogue_group_id,
                        message_thread_id=dialogue_thread_id,
                        text="."  # Minimal test message
                    )
                    # If we're here, thread exists but something else is wrong
                    logger.error("[ROUTE_CLIENT] Thread exists but message sending failed for other reason")
                    return False

                except TelegramAPIError as e:
                    if "thread not found" in str(e).lower() or "message thread not found" in str(e).lower():
                        logger.warning(f"[ROUTE_CLIENT] Thread {dialogue_thread_id} was deleted, recreating...")

                        # Recreate thread
                        new_thread_id = await self._recreate_dialogue_thread(dialogue_id, {
                            'group_id': dialogue_group_id,
                            'thread_id': dialogue_thread_id,
                            'client_telegram_id': message.from_user.id,
                            'ticket_id': dialogue.ticketID if 'dialogue' in locals() else None
                        })

                        if new_thread_id:
                            logger.info(f"[ROUTE_CLIENT] Thread recreated with ID {new_thread_id}, retrying message")
                            # Update endpoint and retry
                            operator_endpoint = DialogueEndpoint('group', dialogue_group_id, new_thread_id)

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

                            success = result is not None
                            logger.info(f"[ROUTE_CLIENT] Retry after thread recreation: {'success' if success else 'failed'}")
                            return success
                        else:
                            logger.error(f"[ROUTE_CLIENT] Failed to recreate thread for dialogue {dialogue_id}")
                            return False
                    else:
                        # Some other error
                        logger.error(f"[ROUTE_CLIENT] Thread check failed: {e}")
                        return False

            success = result is not None
            logger.info(f"[ROUTE_CLIENT] Message routing {'successful' if success else 'failed'} for dialogue {dialogue_id}")
            return success

        except Exception as e:
            logger.error(f"[ROUTE_CLIENT] Error routing client message: {e}", exc_info=True)
            return False

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
                f"[ROUTE_OPERATOR] Starting routing for dialogue {dialogue_id}, "
                f"from operator {message.from_user.id}: {message.text[:50] if message.text else '[Media]'}"
            )

            # Update dialogue activity
            await self._update_dialogue_activity(dialogue_id)

            # Check if it's a command
            if message.text and message.text.startswith('&'):
                logger.info(f"[ROUTE_OPERATOR] Detected command '{message.text.split()[0]}', delegating to command processor")
                # Delegate to command processor
                return await self.command_processor.process_command(message, dialogue_id)

            # Regular message - verify dialogue is active before routing
            dialogue_info = await self.dialogue_service.get_dialogue_info(dialogue_id)
            if not dialogue_info:
                logger.error(f"[ROUTE_OPERATOR] Dialogue {dialogue_id} not found")
                return False

            logger.debug(
                f"[ROUTE_OPERATOR] Dialogue info retrieved: "
                f"status={dialogue_info.get('status')}, state={dialogue_info.get('state')}, "
                f"client_tg_id={dialogue_info.get('client_telegram_id')}"
            )

            # Check if dialogue is still active
            if dialogue_info.get('status') != 'active':
                logger.warning(
                    f"[ROUTE_OPERATOR] Attempting to route in inactive dialogue {dialogue_id} "
                    f"(status={dialogue_info.get('status')})"
                )
                # Notify operator that dialogue is closed
                await self.message_service.send_template_to_telegram_id(
                    telegram_id=message.from_user.id,
                    template_key='/support/operator_dialogue_already_closed',
                    variables={'dialogue_id': dialogue_id}
                )
                return False

            # Check client FSM state for consistency
            with get_db_session_ctx() as session:
                client_user = session.query(User).filter_by(telegramID=dialogue_info['client_telegram_id']).first()
                if client_user:
                    fsm_state = client_user.get_fsm_state()
                    fsm_context = client_user.get_fsm_context()
                    fsm_dialogue_id = fsm_context.get('dialogue_id') if fsm_context else None

                    logger.debug(
                        f"[ROUTE_OPERATOR] Client {dialogue_info['client_telegram_id']} FSM check: "
                        f"state='{fsm_state}', FSM dialogue='{fsm_dialogue_id}', "
                        f"current dialogue='{dialogue_id}'"
                    )

                    if fsm_dialogue_id and fsm_dialogue_id != dialogue_id:
                        logger.warning(
                            f"[ROUTE_OPERATOR] ⚠️ Client FSM desync! "
                            f"FSM has '{fsm_dialogue_id}' but operator in '{dialogue_id}'. "
                            f"Updating client FSM to match current dialogue."
                        )
                        # Fix client FSM to match current dialogue
                        fsm_context = {
                            "dialogue_id": dialogue_id,
                            "ticket_id": dialogue_info.get('ticket_id'),
                            "thread_id": dialogue_info.get('thread_id'),
                            "operator_id": message.from_user.id,
                            "updated_at": datetime.now().isoformat()
                        }
                        client_user.set_fsm_state("has_ticket", fsm_context)
                        session.commit()

            client_endpoint = DialogueEndpoint('user', dialogue_info['client_telegram_id'])

            logger.info(f"[ROUTE_OPERATOR] Routing message to client {dialogue_info['client_telegram_id']}")

            if message.text:
                # Send text with template
                logger.debug(f"[ROUTE_OPERATOR] Sending text message via template")
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
                logger.debug(f"[ROUTE_OPERATOR] Forwarding media message")
                await self.message_service.forward_message(
                    message=message,
                    to_endpoint=client_endpoint,
                    with_comment="💬 Support: "
                )

            logger.info(f"[ROUTE_OPERATOR] Message routing successful for dialogue {dialogue_id}")
            return True

        except Exception as e:
            logger.error(f"[ROUTE_OPERATOR] Error routing operator message: {e}", exc_info=True)
            return False

    async def _update_dialogue_activity(self, dialogue_id: str):
        """Update dialogue last activity time and message count."""
        try:
            with get_db_session_ctx() as session:
                dialogue = session.query(Dialogue).filter_by(dialogueID=dialogue_id).first()
                if dialogue:
                    old_activity = dialogue.lastActivityTime
                    dialogue.lastActivityTime = datetime.now()
                    dialogue.messageCount = (dialogue.messageCount or 0) + 1
                    session.commit()
                    logger.debug(
                        f"[UPDATE_ACTIVITY] Dialogue {dialogue_id}: "
                        f"message_count={dialogue.messageCount}, "
                        f"last_activity updated from {old_activity} to {dialogue.lastActivityTime}"
                    )
                else:
                    logger.warning(f"[UPDATE_ACTIVITY] Dialogue {dialogue_id} not found for activity update")
        except Exception as e:
            logger.error(f"[UPDATE_ACTIVITY] Error updating dialogue activity: {e}")

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
            logger.info(f"[RECREATE_THREAD] Starting thread recreation for dialogue {dialogue_id}")

            with get_db_session_ctx() as session:
                dialogue = session.query(Dialogue).filter_by(dialogueID=dialogue_id).first()
                if not dialogue:
                    logger.error(f"[RECREATE_THREAD] Dialogue {dialogue_id} not found in DB")
                    return None

                # Get ticket info for topic name
                ticket = session.query(Ticket).filter_by(ticketID=dialogue.ticketID).first()
                if not ticket:
                    logger.error(f"[RECREATE_THREAD] Ticket {dialogue.ticketID} not found")
                    return None

                # Create new topic
                topic_name = f"Ticket #{ticket.ticketID} [RESTORED]"
                if ticket.category:
                    topic_name += f" [{ticket.category}]"

                logger.info(f"[RECREATE_THREAD] Creating new topic: '{topic_name}'")

                bot = self.message_service.bot

                topic = await bot.create_forum_topic(
                    chat_id=dialogue_info['group_id'],
                    name=topic_name,
                    icon_color=0xFF93B2  # Pink color for restored topics
                )

                if not topic or not hasattr(topic, 'message_thread_id'):
                    logger.error(f"[RECREATE_THREAD] Failed to create forum topic")
                    return None

                new_thread_id = topic.message_thread_id
                logger.info(f"[RECREATE_THREAD] New thread created with ID {new_thread_id}")

                # Update dialogue with new thread ID
                old_thread_id = dialogue.threadID
                dialogue.threadID = new_thread_id
                session.commit()

                logger.info(
                    f"[RECREATE_THREAD] Updated dialogue {dialogue_id}: "
                    f"thread_id changed from {old_thread_id} to {new_thread_id}"
                )

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
                    logger.info(f"[RECREATE_THREAD] Re-registering handlers")

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

                    logger.info(f"[RECREATE_THREAD] Handlers re-registered successfully")

                logger.info(f"[RECREATE_THREAD] Successfully recreated thread {new_thread_id} for dialogue {dialogue_id}")
                return new_thread_id

        except Exception as e:
            logger.error(f"[RECREATE_THREAD] Error recreating thread: {e}", exc_info=True)
            return None