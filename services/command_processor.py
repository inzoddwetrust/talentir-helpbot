"""
Universal command processor for operator commands.
"""
import logging
from typing import Dict, Any, Optional, Tuple
from datetime import datetime

from aiogram.types import Message

from core.message_service import MessageService, DialogueEndpoint
from core.db import get_db_session_ctx
from services.dialogue_states import DialogueState
from services.operator_commands import get_command_config, get_all_commands
from services.mainbot_service import MainbotService
from models.dialogue import Dialogue
from models.ticket import Ticket, TicketStatus
from models.user import User

logger = logging.getLogger(__name__)


class CommandProcessor:
    """
    Universal processor for operator commands.
    Separates command logic from dialogue routing.
    """

    def __init__(self, dialogue_service, message_service: MessageService):
        """
        Initialize command processor.

        Args:
            dialogue_service: DialogueService instance
            message_service: MessageService instance
        """
        self.dialogue_service = dialogue_service
        self.message_service = message_service

        # Map handler names to methods
        self.handlers = {
            'end_ticket': self._handle_end_ticket,
            'mark_spam': self._handle_mark_spam,
            'show_info': self._handle_show_info,
            'show_history': self._handle_show_history,
            'show_help': self._handle_show_help,
        }

    async def process_command(self, message: Message, dialogue_id: str) -> bool:
        """
        Process operator command from message.

        Args:
            message: Operator message with command
            dialogue_id: Current dialogue ID

        Returns:
            bool: True if command was processed
        """
        try:
            # Parse command and arguments
            command_text, args = self._parse_command(message.text)

            # Get command configuration
            command_config = get_command_config(command_text)
            if not command_config:
                await self._send_error(
                    dialogue_id,
                    template_key='/support/operator_unknown_command',
                    variables={'command': command_text}
                )
                return True

            # Get dialogue info
            dialogue_info = await self.dialogue_service.get_dialogue_info(dialogue_id)
            if not dialogue_info:
                logger.error(f"Dialogue {dialogue_id} not found for command processing")
                return False

            # Check state requirements
            current_state = dialogue_info['state']
            if not self._check_state_requirements(command_config, current_state):
                await self._send_error(
                    dialogue_id,
                    template_key=command_config.template_error,
                    variables={
                        'command': command_text,
                        'current_state': current_state.value,
                        'error': 'Command not allowed in current state'
                    }
                )
                return True

            # Check arguments requirement
            if command_config.requires_args and not args:
                await self._send_error(
                    dialogue_id,
                    template_key=command_config.template_help,
                    variables={'command': command_text}
                )
                return True

            # Get handler
            handler = self.handlers.get(command_config.handler)
            if not handler:
                logger.error(f"Handler {command_config.handler} not found")
                return False

            # Execute command
            success = await handler(
                dialogue_id=dialogue_id,
                dialogue_info=dialogue_info,
                args=args,
                operator_telegram_id=message.from_user.id,
                command_config=command_config
            )

            return success

        except Exception as e:
            logger.error(f"Error processing command: {e}", exc_info=True)
            await self._send_error(
                dialogue_id,
                template_key='/support/operator_command_error',
                variables={'error': str(e)}
            )
            return True

    def _parse_command(self, text: str) -> Tuple[str, str]:
        """Parse command and arguments from text."""
        parts = text.split(maxsplit=1)
        command = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""
        return command, args

    def _check_state_requirements(self, command_config, current_state: DialogueState) -> bool:
        """Check if command is allowed in current state."""
        # Check allowed states list
        if command_config.allowed_states:
            return current_state in command_config.allowed_states

        # Check minimum state
        if command_config.min_state:
            # Simple state ordering for our states
            state_order = [
                DialogueState.WAITING_OPERATOR,
                DialogueState.IN_PROGRESS,
                DialogueState.RESOLVED,
                DialogueState.CLOSED,
                DialogueState.SPAM
            ]
            try:
                current_index = state_order.index(current_state)
                min_index = state_order.index(command_config.min_state)
                return current_index >= min_index
            except ValueError:
                return True

        return True

    async def _send_to_operator(self, dialogue_id: str, template_key: str, variables: Dict[str, Any]):
        """Send template message to operator thread."""
        dialogue_info = await self.dialogue_service.get_dialogue_info(dialogue_id)
        if dialogue_info:
            operator_endpoint = DialogueEndpoint('group', dialogue_info['group_id'], dialogue_info['thread_id'])
            await self.message_service.send_template_to_endpoint(
                endpoint=operator_endpoint,
                template_key=template_key,
                variables=variables
            )

    async def _send_error(self, dialogue_id: str, template_key: str, variables: Dict[str, Any]):
        """Send error message to operator."""
        await self._send_to_operator(dialogue_id, template_key, variables)

    # === Command Handlers ===

    async def _handle_end_ticket(self, dialogue_id: str, dialogue_info: dict, args: str,
                                 operator_telegram_id: int, command_config) -> bool:
        """Handle &end command - close ticket as resolved."""
        try:
            resolution = args or "Issue resolved by operator"

            # Update dialogue state
            await self.dialogue_service.update_dialogue_state(
                dialogue_id, DialogueState.RESOLVED,
                context={
                    'resolved_at': datetime.now().isoformat(),
                    'resolution': resolution,
                    'resolved_by': operator_telegram_id
                }
            )

            # Update ticket
            with get_db_session_ctx() as session:
                ticket = session.query(Ticket).filter_by(ticketID=dialogue_info['ticket_id']).first()
                if ticket:
                    ticket.status = TicketStatus.RESOLVED
                    ticket.resolvedAt = datetime.now()
                    ticket.resolution = resolution
                    if ticket.createdAt:
                        ticket.resolutionTime = int((ticket.resolvedAt - ticket.createdAt).total_seconds() / 60)
                    session.commit()

            # Send success notification
            await self._send_to_operator(dialogue_id, command_config.template_success, {
                'ticket_id': dialogue_info['ticket_id'],
                'resolution': resolution
            })

            # Close dialogue
            await self.dialogue_service.close_dialogue(
                dialogue_id, 'operator', f'Resolved: {resolution}'
            )

            return True

        except Exception as e:
            logger.error(f"Error in end_ticket handler: {e}")
            return False

    async def _handle_mark_spam(self, dialogue_id: str, dialogue_info: dict, args: str,
                                operator_telegram_id: int, command_config) -> bool:
        """Handle &spam command - mark as spam."""
        try:
            # Update dialogue state
            await self.dialogue_service.update_dialogue_state(
                dialogue_id, DialogueState.SPAM,
                context={
                    'marked_spam_at': datetime.now().isoformat(),
                    'marked_by': operator_telegram_id
                }
            )

            # Update ticket
            with get_db_session_ctx() as session:
                ticket = session.query(Ticket).filter_by(ticketID=dialogue_info['ticket_id']).first()
                if ticket:
                    ticket.status = TicketStatus.SPAM
                    ticket.resolvedAt = datetime.now()
                    ticket.resolution = "Marked as spam by operator"
                    session.commit()

            # Send success notification
            await self._send_to_operator(dialogue_id, command_config.template_success, {
                'ticket_id': dialogue_info['ticket_id']
            })

            # Close dialogue
            await self.dialogue_service.close_dialogue(dialogue_id, 'operator', 'spam')

            return True

        except Exception as e:
            logger.error(f"Error in mark_spam handler: {e}")
            return False

    async def _handle_show_info(self, dialogue_id: str, dialogue_info: dict, args: str,
                                operator_telegram_id: int, command_config) -> bool:
        """Handle &info command - show user and ticket info."""
        try:
            variables = {'ticket_id': dialogue_info['ticket_id']}

            # Get ticket info
            with get_db_session_ctx() as session:
                ticket = session.query(Ticket).filter_by(ticketID=dialogue_info['ticket_id']).first()
                if ticket:
                    variables.update({
                        'category': ticket.category or 'general',
                        'subject': ticket.subject or 'No subject',
                        'description': ticket.description or 'No description',
                        'error_code': ticket.error_code or 'None',
                        'created_at': ticket.createdAt.strftime('%Y-%m-%d %H:%M') if ticket.createdAt else 'Unknown'
                    })

            # Get mainbot user info
            user_summary = await MainbotService.get_user_summary(dialogue_info['client_telegram_id'])

            if user_summary:
                variables.update({
                    'telegram_id': dialogue_info['client_telegram_id'],
                    'full_name': user_summary.get('full_name', 'Unknown'),
                    'email': user_summary.get('email', 'N/A'),
                    'phone': user_summary.get('phone', 'N/A'),
                    'country': user_summary.get('country', 'N/A'),
                    'balance_total': f"${user_summary.get('balance_total', 0):,.2f}",
                    'kyc_status': user_summary.get('kyc_status', 'Unknown'),
                    'days_since_registration': user_summary.get('days_since_registration', 'N/A')
                })

                # Use full info template
                template_key = '/support/operator_user_info_full'
            else:
                variables['telegram_id'] = dialogue_info['client_telegram_id']
                # Use basic info template
                template_key = '/support/operator_user_info_basic'

            await self._send_to_operator(dialogue_id, template_key, variables)
            return True

        except Exception as e:
            logger.error(f"Error in show_info handler: {e}")
            return False

    async def _handle_show_history(self, dialogue_id: str, dialogue_info: dict, args: str,
                                   operator_telegram_id: int, command_config) -> bool:
        """Handle &history command - show ticket history."""
        try:
            tickets_data = []

            with get_db_session_ctx() as session:
                # Get current ticket's user
                current_ticket = session.query(Ticket).filter_by(ticketID=dialogue_info['ticket_id']).first()
                if current_ticket:
                    # Get all tickets for this user
                    user_tickets = session.query(Ticket).filter_by(
                        userID=current_ticket.userID
                    ).order_by(Ticket.createdAt.desc()).limit(10).all()

                    for t in user_tickets:
                        status_emoji = {
                            TicketStatus.OPEN: "🔵",
                            TicketStatus.IN_PROGRESS: "🟡",
                            TicketStatus.RESOLVED: "✅",
                            TicketStatus.CLOSED: "⚫",
                            TicketStatus.SPAM: "🚫"
                        }.get(t.status, "❓")

                        tickets_data.append({
                            'emoji': status_emoji,
                            'ticket_id': t.ticketID,
                            'created_at': t.createdAt.strftime('%Y-%m-%d') if t.createdAt else 'Unknown',
                            'category': t.category or 'general',
                            'status': t.status.value if hasattr(t.status, 'value') else str(t.status),
                            'resolution': (t.resolution[:50] + '...') if t.resolution and len(t.resolution) > 50 else (
                                        t.resolution or 'N/A')
                        })

            # Send using template with rgroup
            await self._send_to_operator(dialogue_id, command_config.template_success, {
                'telegram_id': dialogue_info['client_telegram_id'],
                'rgroup': {
                    'emoji': [t['emoji'] for t in tickets_data],
                    'ticket_id': [t['ticket_id'] for t in tickets_data],
                    'created_at': [t['created_at'] for t in tickets_data],
                    'category': [t['category'] for t in tickets_data],
                    'resolution': [t['resolution'] for t in tickets_data]
                }
            })

            return True

        except Exception as e:
            logger.error(f"Error in show_history handler: {e}")
            return False

    async def _handle_show_help(self, dialogue_id: str, dialogue_info: dict, args: str,
                                operator_telegram_id: int, command_config) -> bool:
        """Handle &help command - show available commands."""
        try:
            # Get all commands
            all_commands = get_all_commands()

            # Prepare command list for template
            commands_data = []
            for cmd_name, cmd_config in all_commands.items():
                commands_data.append({
                    'command': cmd_name,
                    'args': '[args]' if cmd_config.requires_args else '',
                    'description': cmd_config.description
                })

            # Send using template with rgroup
            await self._send_to_operator(dialogue_id, command_config.template_success, {
                'rgroup': {
                    'command': [c['command'] for c in commands_data],
                    'args': [c['args'] for c in commands_data],
                    'description': [c['description'] for c in commands_data]
                }
            })

            return True

        except Exception as e:
            logger.error(f"Error in show_help handler: {e}")
            return False