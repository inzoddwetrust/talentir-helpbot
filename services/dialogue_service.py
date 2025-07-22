"""
Dialogue service for managing support conversations in helpbot.
"""
import logging
import json
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
import asyncio

from aiogram import Bot
from aiogram.types import ForumTopic

from models.dialogue import Dialogue
from models.ticket import Ticket, TicketStatus, TicketPriority
from models.user import User
from core.db import get_db_session_ctx
from core.message_service import MessageService, DialogueEndpoint
from core.input_service import InputService
from services.dialogue_states import DialogueState
from models.operator import Operator
from config import Config

logger = logging.getLogger(__name__)


class DialogueService:
    """
    Service for managing support dialogues between clients and operators.

    Responsibilities:
    - Create/close dialogues
    - Register/unregister message handlers
    - Update dialogue states
    - Create Telegram topics for operators
    """

    def __init__(self, bot: Bot, message_service: MessageService, input_service: InputService):
        """
        Initialize dialogue service.

        Args:
            bot: Bot instance
            message_service: Message service for sending messages
            input_service: Input service for registering handlers
        """
        self.bot = bot
        self.message_service = message_service
        self.input_service = input_service

        # Will be set by external router
        self.message_router = None
        self.check_stale_task = None

    def set_message_router(self, router):
        """Set message router for handling dialogue messages."""
        self.message_router = router

    async def create_support_dialogue(self, ticket: Ticket, operator_id: int,
                                      context: Dict[str, Any] = None) -> Optional[str]:
        """
        Create a new support dialogue for a ticket.

        Args:
            ticket: Ticket object
            operator_id: Operator's ID
            context: Additional context

        Returns:
            str: Dialogue ID or None if failed
        """
        try:
            logger.info(f"Creating support dialogue for ticket #{ticket.ticketID}")

            dialogue_id = f"support_{ticket.ticketID}"
            context = context or {}

            # Get group ID for creating topics
            group_id = int(Config.get(Config.GROUP_ID, 0))
            if not group_id:
                logger.error("GROUP_ID not configured")
                return None
            logger.info(f"Config.GROUP_ID returns: {Config.get(Config.GROUP_ID)}")

            if not group_id:
                logger.error("GROUP_ID not configured")
                return None

            # Create topic in operators group
            topic_name = f"Ticket #{ticket.ticketID}"
            if ticket.category:
                topic_name += f" [{ticket.category}]"
            if ticket.subject:
                topic_name += f": {ticket.subject[:30]}"

            thread_id = await self._create_forum_topic(
                group_id, topic_name, ticket.ticketID, operator_id
            )

            if not thread_id:
                logger.error(f"Failed to create topic for dialogue {dialogue_id}")
                return None

            # Save all needed data BEFORE session closes
            client_telegram_id = None
            client_display_name = None

            with get_db_session_ctx() as session:
                # Get client user
                client_user = session.query(User).filter_by(userID=ticket.userID).first()
                if not client_user:
                    logger.error(f"Client user {ticket.userID} not found")
                    return None

                # SAVE ALL DATA WE NEED
                client_telegram_id = client_user.telegramID
                client_display_name = client_user.displayName

                # Create dialogue record
                dialogue = Dialogue(
                    dialogueID=dialogue_id,
                    dialogueType='support',
                    ticketID=ticket.ticketID,
                    userID=client_user.userID,
                    operatorID=operator_id,
                    groupID=group_id,
                    threadID=thread_id,
                    status='active',
                    state=str(DialogueState.IN_PROGRESS),
                    createdAt=datetime.now(),
                    lastActivityTime=datetime.now(),
                    notes=json.dumps({
                        'context': context,
                        'ticket_info': {
                            'category': ticket.category,
                            'subject': ticket.subject,
                            'description': ticket.description,
                            'error_code': ticket.error_code
                        }
                    })
                )

                session.add(dialogue)

                # Set client FSM state
                client_user.set_fsm_state("has_ticket", {"ticket_id": ticket.ticketID})

                # Update ticket dialogue reference
                ticket.dialogueID = dialogue_id

                session.commit()

            # Register message handlers with saved data
            await self._register_dialogue_handlers(dialogue_id, client_telegram_id, group_id, thread_id)

            # Send welcome messages with saved data
            await self._send_welcome_messages(dialogue_id, ticket, client_display_name)

            logger.info(f"Created dialogue {dialogue_id} successfully")
            return dialogue_id

        except Exception as e:
            logger.error(f"Error creating dialogue: {e}", exc_info=True)
            return None

    async def close_dialogue(self, dialogue_id: str, closed_by: str, reason: str = None) -> bool:
        """
        Close an active dialogue.

        Args:
            dialogue_id: Dialogue ID
            closed_by: Who closed the dialogue ('client', 'operator', 'system')
            reason: Optional reason for closing

        Returns:
            bool: Success status
        """
        try:
            logger.info(f"Closing dialogue {dialogue_id}, closed by {closed_by}")

            with get_db_session_ctx() as session:
                dialogue = session.query(Dialogue).filter_by(dialogueID=dialogue_id).first()
                if not dialogue or dialogue.status != 'active':
                    logger.warning(f"Dialogue {dialogue_id} not found or not active")
                    return False

                # Get client for FSM cleanup
                client_user = session.query(User).filter_by(userID=dialogue.userID).first()

                # Update dialogue
                dialogue.status = 'closed'
                dialogue.closedAt = datetime.now()
                dialogue.closedBy = closed_by
                dialogue.closeReason = reason

                # Переименовываем топик, чтобы показать что он закрыт
                if dialogue.threadID and dialogue.groupID:
                    try:
                        # Получаем информацию о тикете для названия
                        if dialogue.ticketID:
                            ticket = session.query(Ticket).filter_by(ticketID=dialogue.ticketID).first()
                            if ticket:
                                # Формируем новое название BASED ON DIALOGUE STATE
                                if dialogue.state == str(DialogueState.SPAM):
                                    closed_name = f"🚫 [SPAM] Ticket #{ticket.ticketID}"
                                elif dialogue.state == str(DialogueState.RESOLVED):
                                    closed_name = f"✅ [RESOLVED] Ticket #{ticket.ticketID}"
                                else:
                                    closed_name = f"🚫 [CLOSED] Ticket #{ticket.ticketID}"

                                # Меняем название топика
                                await self.bot.edit_forum_topic(
                                    chat_id=dialogue.groupID,
                                    message_thread_id=dialogue.threadID,
                                    name=closed_name,
                                    icon_custom_emoji_id=None  # Убираем кастомный эмодзи если был
                                )

                                logger.info(f"Renamed closed topic for dialogue {dialogue_id}")
                    except Exception as e:
                        logger.warning(f"Failed to rename closed topic: {e}")

                # Clear client FSM if they're in this dialogue
                if client_user and client_user.get_fsm_state() == "has_ticket":
                    fsm_data = client_user.get_fsm_data()
                    # ИСПРАВЛЕНО: проверяем ticket_id, а не dialogue_id
                    if fsm_data and fsm_data.get('ticket_id') == dialogue.ticketID:
                        client_user.clear_fsm()
                        logger.info(f"Cleared FSM for user {client_user.telegramID}")

                # Update ticket status if exists
                if dialogue.ticketID:
                    ticket = session.query(Ticket).filter_by(ticketID=dialogue.ticketID).first()
                    if ticket:
                        # Определяем статус тикета по состоянию диалога
                        if dialogue.state == str(DialogueState.SPAM):
                            ticket.status = TicketStatus.SPAM
                            ticket.resolution = 'Marked as spam'
                        elif dialogue.state == str(DialogueState.RESOLVED):
                            ticket.status = TicketStatus.RESOLVED
                            ticket.resolution = reason or 'Resolved by operator'
                        else:
                            ticket.status = TicketStatus.CLOSED
                            ticket.resolution = reason or f'Closed by {closed_by}'

                        ticket.resolvedAt = datetime.now()
                        if ticket.createdAt:
                            ticket.resolutionTime = int((ticket.resolvedAt - ticket.createdAt).total_seconds() / 60)

                session.commit()

                # Send closing messages
                await self._send_closing_messages(dialogue_id, closed_by, reason)

                # Unregister handlers
                await self._unregister_dialogue_handlers(dialogue_id, client_user.telegramID,
                                                         dialogue.groupID, dialogue.threadID)

            logger.info(f"Dialogue {dialogue_id} closed successfully")
            return True

        except Exception as e:
            logger.error(f"Error closing dialogue {dialogue_id}: {e}", exc_info=True)
            return False

    async def update_dialogue_state(self, dialogue_id: str, new_state: DialogueState,
                                    context: Dict[str, Any] = None) -> bool:
        """
        Update dialogue state.

        Args:
            dialogue_id: Dialogue ID
            new_state: New state
            context: Additional context to store

        Returns:
            bool: Success status
        """
        try:
            with get_db_session_ctx() as session:
                dialogue = session.query(Dialogue).filter_by(dialogueID=dialogue_id).first()
                if not dialogue:
                    logger.warning(f"Dialogue {dialogue_id} not found for state update")
                    return False

                old_state = dialogue.state
                dialogue.state = str(new_state)
                dialogue.updatedAt = datetime.now()

                # Update context if provided
                if context:
                    try:
                        notes = json.loads(dialogue.notes or "{}")
                        if 'context' not in notes:
                            notes['context'] = {}
                        notes['context'].update(context)
                        dialogue.notes = json.dumps(notes)
                    except json.JSONDecodeError:
                        logger.warning(f"Failed to update context for dialogue {dialogue_id}")

                session.commit()

                logger.info(f"Updated dialogue {dialogue_id} state: {old_state} -> {new_state}")
                return True

        except Exception as e:
            logger.error(f"Error updating dialogue state: {e}", exc_info=True)
            return False

    async def get_dialogue_info(self, dialogue_id: str) -> Optional[Dict[str, Any]]:
        """
        Get dialogue information.

        Args:
            dialogue_id: Dialogue ID

        Returns:
            Dict with dialogue info or None if not found
        """
        try:
            with get_db_session_ctx() as session:
                dialogue = session.query(Dialogue).filter_by(dialogueID=dialogue_id).first()
                if not dialogue:
                    return None

                # Get client info
                client_user = session.query(User).filter_by(userID=dialogue.userID).first()

                # Parse context
                context = {}
                try:
                    notes = json.loads(dialogue.notes or "{}")
                    context = notes.get('context', {})
                except json.JSONDecodeError:
                    pass

                return {
                    'dialogue_id': dialogue.dialogueID,
                    'dialogue_type': dialogue.dialogueType,
                    'ticket_id': dialogue.ticketID,
                    'state': DialogueState.from_string(dialogue.state),
                    'status': dialogue.status,
                    'client_telegram_id': client_user.telegramID if client_user else None,
                    'group_id': dialogue.groupID,
                    'thread_id': dialogue.threadID,
                    'created_at': dialogue.createdAt,
                    'last_activity': dialogue.lastActivityTime,
                    'context': context
                }

        except Exception as e:
            logger.error(f"Error getting dialogue info: {e}")
            return None

    # === Private helper methods ===

    async def _create_forum_topic(self, group_id: int, topic_name: str,
                                  ticket_id: int, operator_id: int) -> Optional[int]:
        """Create forum topic in operators group."""
        try:
            logger.info(f"Creating topic: group_id={group_id}, name='{topic_name}'")  # ДОБАВЛЕНО

            # All data operations inside session context
            with get_db_session_ctx() as session:
                operator = session.query(Operator).filter_by(operatorID=operator_id).first()
                if not operator:
                    logger.error(f"Operator {operator_id} not found for topic creation")
                    return None

                ticket = session.query(Ticket).filter_by(ticketID=ticket_id).first()
                if not ticket:
                    logger.error(f"Ticket {ticket_id} not found for topic creation")
                    return None

                client = session.query(User).filter_by(userID=ticket.userID).first()

                # Build informative topic name
                client_name = client.displayName if client else f"User{ticket.userID}"
                operator_tg = operator.telegramID

                # Priority indicator for topic name
                priority_emoji = {
                    TicketPriority.URGENT: "🔴",
                    TicketPriority.HIGH: "🟠",
                    TicketPriority.NORMAL: "🟢",
                    TicketPriority.LOW: "🔵"
                }.get(ticket.priority, "⚪")

                # Subject or error code
                issue = ticket.error_code or ticket.subject or ticket.category or "Support"
                if len(issue) > 30:
                    issue = issue[:27] + "..."

                # Format: "🟢 ClientName | Op:123456 | Issue"
                topic_name = f"{priority_emoji} {client_name} | Op:{operator_tg} | {issue}"

                # Save data we need outside session
                client_telegram_id = client.telegramID if client else None
                mainbot_user_id = ticket.mainbot_user_id

            # Color based on OPERATOR ID (each operator has their color)
            colors = [0x6FB9F0, 0xFFD67E, 0xCB86DB, 0x8EEE98, 0xFF93B2, 0xFB6F5F]
            icon_color = colors[operator_id % len(colors)]

            logger.info(f"Actually creating topic with chat_id={group_id}")  # ДОБАВЛЕНО

            # Create topic
            topic: ForumTopic = await self.bot.create_forum_topic(
                chat_id=group_id,
                name=topic_name,
                icon_color=icon_color
            )

            if not topic or not hasattr(topic, 'message_thread_id'):
                logger.error("Failed to create forum topic")
                return None

            thread_id = topic.message_thread_id

            # Send initial info using TEMPLATE
            operator_endpoint = DialogueEndpoint('group', group_id, thread_id)

            # Get mainbot user info if available
            user_info = None
            if mainbot_user_id and client_telegram_id:
                from services.mainbot_service import MainbotService
                user_info = await MainbotService.get_user_summary(client_telegram_id)

            return thread_id

        except Exception as e:
            logger.error(f"Error creating forum topic: {e}")
            return None

    async def _register_dialogue_handlers(self, dialogue_id: str, client_telegram_id: int,
                                          group_id: int, thread_id: int):
        """Register message handlers for client and operator."""
        try:
            # Handler for client messages (user in FSM state "has_ticket")
            async def handle_client_message(message):
                if self.message_router:
                    await self.message_router.route_client_message(message, dialogue_id)

            await self.input_service.register_user_handler(
                user_id=client_telegram_id,
                handler=handle_client_message,
                state="has_ticket"
            )

            # Handler for operator messages (in thread)
            async def handle_operator_message(message):
                if self.message_router:
                    await self.message_router.route_operator_message(message, dialogue_id)

            await self.input_service.register_thread_handler(
                group_id=group_id,
                thread_id=thread_id,
                handler=handle_operator_message
            )

            logger.info(f"Registered handlers for dialogue {dialogue_id}")

        except Exception as e:
            logger.error(f"Error registering handlers: {e}")

    async def _unregister_dialogue_handlers(self, dialogue_id: str, client_telegram_id: int,
                                            group_id: int, thread_id: int):
        """Unregister message handlers."""
        try:
            await self.input_service.unregister_user_handler(client_telegram_id, state="has_ticket")
            await self.input_service.unregister_thread_handler(group_id, thread_id)
            logger.info(f"Unregistered handlers for dialogue {dialogue_id}")
        except Exception as e:
            logger.error(f"Error unregistering handlers: {e}")

    async def _send_welcome_messages(self, dialogue_id: str, ticket: Ticket, client_user: User):
        """Send welcome messages to client and operator."""
        try:
            dialogue_info = await self.get_dialogue_info(dialogue_id)
            if not dialogue_info:
                return

            # Send to client
            if dialogue_info['client_telegram_id']:
                client_endpoint = DialogueEndpoint('user', dialogue_info['client_telegram_id'])
                await self.message_service.send_template_to_endpoint(
                    endpoint=client_endpoint,
                    template_key='/support/dialogue_started',
                    variables={
                        'ticket_id': ticket.ticketID,
                        'category': ticket.category or 'general'
                    }
                )

            # Send to operator (in thread) with ticket details
            operator_endpoint = DialogueEndpoint('group', dialogue_info['group_id'],
                                                 dialogue_info['thread_id'])

            # Get mainbot user info if available
            user_info = None
            if ticket.mainbot_user_id:
                from services.mainbot_service import MainbotService
                user_info = await MainbotService.get_user_summary(client_user.telegramID)

            await self.message_service.send_template_to_endpoint(
                endpoint=operator_endpoint,
                template_key='/support/operator_ticket_info',
                variables={
                    'ticket_id': ticket.ticketID,
                    'client_name': client_user.displayName,
                    'client_telegram_id': client_user.telegramID,
                    'category': ticket.category or 'general',
                    'subject': ticket.subject or 'No subject',
                    'description': ticket.description or 'No description',
                    'error_code': ticket.error_code or 'None',
                    'user_balance': user_info.get('balance_total', 0) if user_info else 'N/A',
                    'user_kyc': user_info.get('kyc_status', 'Unknown') if user_info else 'Unknown'
                }
            )

        except Exception as e:
            logger.error(f"Error sending welcome messages: {e}")

    async def _send_closing_messages(self, dialogue_id: str, closed_by: str, reason: str):
        """Send closing messages to participants."""
        try:
            dialogue_info = await self.get_dialogue_info(dialogue_id)
            if not dialogue_info:
                return

            variables = {
                'dialogue_id': dialogue_id,
                'ticket_id': dialogue_info['ticket_id'],
                'closed_by': closed_by,
                'reason': reason or 'No reason provided'
            }

            # Send to client
            if dialogue_info['client_telegram_id']:
                client_endpoint = DialogueEndpoint('user', dialogue_info['client_telegram_id'])
                await self.message_service.send_template_to_endpoint(
                    endpoint=client_endpoint,
                    template_key='/support/dialogue_closed',
                    variables=variables
                )

            # Send to operator
            operator_endpoint = DialogueEndpoint('group', dialogue_info['group_id'],
                                                 dialogue_info['thread_id'])
            await self.message_service.send_template_to_endpoint(
                endpoint=operator_endpoint,
                template_key='/support/operator_dialogue_closed',
                variables=variables
            )

        except Exception as e:
            logger.error(f"Error sending closing messages: {e}")

    async def start_stale_check_task(self):
        """Start background task for checking stale dialogues."""
        if self.check_stale_task is None:
            self.check_stale_task = asyncio.create_task(self._check_stale_dialogues())
            logger.info("Started stale dialogue check task")

    async def _check_stale_dialogues(self):
        """Background task to check and close stale dialogues."""
        while True:
            try:
                await asyncio.sleep(300)  # Check every 5 minutes

                with get_db_session_ctx() as session:
                    # Find dialogues inactive for more than configured hours
                    auto_close_hours = Config.get(Config.AUTO_CLOSE_HOURS, 24)
                    cutoff_time = datetime.now() - timedelta(hours=auto_close_hours)

                    stale_dialogues = session.query(Dialogue).filter(
                        Dialogue.status == 'active',
                        Dialogue.lastActivityTime < cutoff_time
                    ).all()

                    for dialogue in stale_dialogues:
                        logger.info(f"Auto-closing stale dialogue {dialogue.dialogueID}")

                        # Update state to CLOSED
                        dialogue.state = str(DialogueState.CLOSED)
                        dialogue.status = 'closed'
                        dialogue.closedAt = datetime.now()
                        dialogue.closedBy = 'system'
                        dialogue.closeReason = f'auto-closed after {auto_close_hours} hours of inactivity'

                        # Clear client FSM
                        client_user = session.query(User).filter_by(userID=dialogue.userID).first()
                        if client_user and client_user.get_fsm_state() == "has_ticket":
                            client_user.clear_fsm()

                        # Update ticket status
                        if dialogue.ticketID:
                            ticket = session.query(Ticket).filter_by(ticketID=dialogue.ticketID).first()
                            if ticket:
                                ticket.status = 'closed'
                                ticket.resolution = f'Auto-closed due to inactivity'

                        session.commit()

                        # Send notifications
                        await self._send_timeout_notifications(dialogue.dialogueID)

            except Exception as e:
                logger.error(f"Error in stale dialogue check: {e}", exc_info=True)

    async def _send_timeout_notifications(self, dialogue_id: str):
        """Send notifications about auto-closed dialogue."""
        try:
            dialogue_info = await self.get_dialogue_info(dialogue_id)
            if not dialogue_info:
                return

            auto_close_hours = Config.get(Config.AUTO_CLOSE_HOURS, 24)
            variables = {
                'dialogue_id': dialogue_id,
                'ticket_id': dialogue_info['ticket_id'],
                'reason': f'Ticket auto-closed after {auto_close_hours} hours of inactivity'
            }

            # Notify client
            if dialogue_info['client_telegram_id']:
                client_endpoint = DialogueEndpoint('user', dialogue_info['client_telegram_id'])
                await self.message_service.send_template_to_endpoint(
                    endpoint=client_endpoint,
                    template_key='/support/dialogue_auto_closed',
                    variables=variables
                )

            # Notify operator
            operator_endpoint = DialogueEndpoint('group', dialogue_info['group_id'], dialogue_info['thread_id'])
            await self.message_service.send_template_to_endpoint(
                endpoint=operator_endpoint,
                template_key='/support/operator_dialogue_auto_closed',
                variables=variables
            )

        except Exception as e:
            logger.error(f"Error sending timeout notifications: {e}")

    async def restore_active_dialogues(self):
        """Restore handlers for all active dialogues after bot restart."""
        try:
            logger.info("Restoring active dialogues...")

            with get_db_session_ctx() as session:
                # Find all active dialogues
                active_dialogues = session.query(Dialogue).filter_by(
                    status='active'
                ).all()

                restored_count = 0

                for dialogue in active_dialogues:
                    try:
                        # Get client user
                        client_user = session.query(User).filter_by(userID=dialogue.userID).first()
                        if not client_user:
                            logger.warning(
                                f"Client user {dialogue.userID} not found for dialogue {dialogue.dialogueID}")
                            continue

                        # Re-register handlers
                        await self._register_dialogue_handlers(
                            dialogue.dialogueID,
                            client_user.telegramID,
                            dialogue.groupID,
                            dialogue.threadID
                        )

                        restored_count += 1
                        logger.info(f"Restored dialogue {dialogue.dialogueID} for user {client_user.telegramID}")

                    except Exception as e:
                        logger.error(f"Error restoring dialogue {dialogue.dialogueID}: {e}")

            logger.info(f"Restored {restored_count} active dialogues")

        except Exception as e:
            logger.error(f"Error restoring active dialogues: {e}", exc_info=True)