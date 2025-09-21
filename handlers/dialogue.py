"""
Dialogue handlers for helpbot - ticket creation and operator assignment.
"""
import logging
import json
from datetime import datetime
from typing import Dict

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command

from core.user_decorator import with_user
from core.message_manager import MessageManager
from models.operator import Operator
from models.ticket import Ticket, TicketStatus, TicketPriority
from models.dialogue import Dialogue
from models.user import User, UserType
from core.di import get_service
from services.dialogue_service import DialogueService
from core.message_service import MessageService, DialogueEndpoint

logger = logging.getLogger(__name__)

dialogue_router = Router(name="dialogue_router")


class TicketNotificationManager:
    """Manages operator notifications for new tickets."""

    def __init__(self):
        self.operator_notifications: Dict[str, Dict[int, int]] = {}

    def store_notification(self, ticket_id: int, operator_telegram_id: int, message_id: int):
        """Store notification message ID for later deletion."""
        ticket_key = f"ticket_{ticket_id}"
        if ticket_key not in self.operator_notifications:
            self.operator_notifications[ticket_key] = {}
        self.operator_notifications[ticket_key][operator_telegram_id] = message_id

    def get_notifications(self, ticket_id: int) -> Dict[int, int]:
        """Get all notifications for a ticket."""
        return self.operator_notifications.get(f"ticket_{ticket_id}", {})

    def clear_notifications(self, ticket_id: int):
        """Clear notifications after ticket is taken."""
        ticket_key = f"ticket_{ticket_id}"
        if ticket_key in self.operator_notifications:
            del self.operator_notifications[ticket_key]


# Global notification manager instance
notification_manager = TicketNotificationManager()


@dialogue_router.message(Command("start"))
@with_user()
async def cmd_start(message: Message, user, mainbot_user, user_type, session, message_manager: MessageManager):
    """Handle /start command with optional error payload."""
    try:
        # Get InputService for handler cleanup
        from core.di import get_service
        from core.input_service import InputService
        input_service = get_service(InputService)

        # FIRST: Check FSM state for existing ticket
        if user.get_fsm_state() == "has_ticket":
            fsm_context = user.get_fsm_context()
            fsm_dialogue_id = fsm_context.get('dialogue_id')

            if fsm_dialogue_id:
                # Check if dialogue from FSM exists and is active
                existing_dialogue = session.query(Dialogue).filter_by(
                    dialogueID=fsm_dialogue_id,
                    status='active'
                ).first()

                if existing_dialogue:
                    # Dialogue is active - show existing ticket
                    logger.info(f"User {user.telegramID} already has active dialogue {fsm_dialogue_id}")
                    await message_manager.send_template(
                        user=user,
                        template_key="/support/already_has_ticket",
                        update=message,
                        variables={"ticket_id": existing_dialogue.ticketID}
                    )
                    return
                else:
                    # Dialogue from FSM is inactive - clear FSM AND handlers
                    logger.warning(
                        f"User {user.telegramID} has stale FSM state for dialogue {fsm_dialogue_id}, clearing")
                    user.clear_fsm()
                    session.commit()

                    if input_service:
                        logger.info(f"[CMD_START] Cleaning up stale handlers for user {user.telegramID}")
                        await input_service.cleanup_user_handlers(user.telegramID)

        # Additional check for active dialogues in DB
        active_dialogue = session.query(Dialogue).filter_by(
            userID=user.userID,
            status='active'
        ).first()

        if active_dialogue:
            # Sync FSM if needed
            if user.get_fsm_state() != "has_ticket":
                logger.warning(
                    f"User {user.telegramID} has active dialogue {active_dialogue.dialogueID} but no FSM state, syncing")
                fsm_context = {
                    "dialogue_id": active_dialogue.dialogueID,
                    "ticket_id": active_dialogue.ticketID,
                    "thread_id": active_dialogue.threadID,
                    "operator_id": active_dialogue.operatorID,
                    "created_at": active_dialogue.createdAt.isoformat() if active_dialogue.createdAt else None
                }
                user.set_fsm_state("has_ticket", fsm_context)
                session.commit()

            await message_manager.send_template(
                user=user,
                template_key="/support/already_has_ticket",
                update=message,
                variables={"ticket_id": active_dialogue.ticketID}
            )
            return

        # Check for open tickets
        open_ticket = session.query(Ticket).filter(
            Ticket.userID == user.userID,
            Ticket.status.in_([TicketStatus.OPEN, TicketStatus.IN_PROGRESS])
        ).first()

        if open_ticket:
            await message_manager.send_template(
                user=user,
                template_key="/support/already_has_ticket",
                update=message,
                variables={"ticket_id": open_ticket.ticketID}
            )
            return

        # Clean up any potential zombie handlers
        if input_service:
            logger.info(
                f"[CMD_START] Preventive cleanup of handlers for user {user.telegramID} before ticket confirmation")
            await input_service.cleanup_user_handlers(user.telegramID)

        # Parse payload if exists
        error_code = None
        context = {}

        if message.text and len(message.text.split()) > 1:
            payload = message.text.split(maxsplit=1)[1]

            if payload.startswith("error_"):
                error_code = payload
                context = {"source": "deeplink", "timestamp": datetime.utcnow().isoformat()}
            else:
                context = {"payload": payload, "timestamp": datetime.utcnow().isoformat()}

        # NEW: Save to FSM and show confirmation
        fsm_context = {
            "error_code": error_code,
            "context": context,
            "timestamp": datetime.utcnow().isoformat()
        }
        user.set_fsm_state("ticket_confirmation", fsm_context)
        session.commit()

        logger.info(f"User {user.telegramID} entered ticket confirmation state with error_code: {error_code}")

        await message_manager.send_template(
            user=user,
            template_key="/support/welcome_confirmation",
            update=message,
            variables={
                "user_name": user.displayName,
                "error_code": error_code or "N/A"
            }
        )

    except Exception as e:
        logger.error(f"Error in start command: {e}", exc_info=True)
        await message_manager.send_template(
            user=user,
            template_key="/errors/general",
            update=message,
            variables={"error": str(e)}
        )


@dialogue_router.callback_query(F.data == "/ticket/confirm")
@with_user()
async def handle_ticket_confirm(callback: CallbackQuery, user, mainbot_user, user_type, session,
                                message_manager: MessageManager):
    """Handle ticket creation confirmation."""
    try:
        await callback.answer()

        # Check FSM state
        if user.get_fsm_state() != "ticket_confirmation":
            await callback.answer("Сессия устарела. Используйте /start для начала.")
            return

        # Get saved context
        fsm_context = user.get_fsm_context()
        error_code = fsm_context.get("error_code")
        context = fsm_context.get("context", {})

        # Create ticket
        ticket = Ticket(
            userID=user.userID,
            mainbot_user_id=mainbot_user.userID if mainbot_user else None,
            status=TicketStatus.OPEN,
            priority=TicketPriority.NORMAL,
            category="general",
            subject=f"Support request from {user.displayName}",
            description="Ticket created via /start command",
            error_code=error_code,
            context=json.dumps(context) if context else None
        )
        session.add(ticket)
        session.commit()

        # Clear FSM state (will be set again when dialogue is created)
        user.clear_fsm()
        session.commit()

        logger.info(f"Created ticket #{ticket.ticketID} for user {user.telegramID} after confirmation")

        # Edit original message to remove buttons
        await callback.message.edit_reply_markup(reply_markup=None)

        # Send confirmation to user
        await message_manager.send_template(
            user=user,
            template_key="/support/ticket_created",
            update=callback.message,
            variables={
                "ticket_id": ticket.ticketID,
                "user_name": user.displayName,
                "error_code": error_code or "N/A"
            }
        )

        # Notify all active operators
        await notify_operators_about_ticket(ticket, user, session)

    except Exception as e:
        logger.error(f"Error in ticket confirmation: {e}", exc_info=True)
        await callback.answer("Ошибка при создании тикета")


@dialogue_router.callback_query(F.data == "/ticket/cancel")
@with_user()
async def handle_ticket_cancel(callback: CallbackQuery, user, mainbot_user, user_type, session,
                               message_manager: MessageManager):
    """Handle ticket creation cancellation."""
    try:
        await callback.answer()

        # Clear FSM state
        user.clear_fsm()
        session.commit()

        logger.info(f"User {user.telegramID} cancelled ticket creation")

        # Edit original message to remove buttons
        await callback.message.edit_reply_markup(reply_markup=None)

        # Get mainbot URL from config
        from config import Config
        mainbot_url = Config.get(Config.MAINBOT_URL, "https://t.me/your_main_bot")

        await message_manager.send_template(
            user=user,
            template_key="/support/cancelled_return",
            update=callback.message,
            variables={
                "user_name": user.displayName,
                "mainbot_url": mainbot_url.replace("https://", "")
            }
        )

    except Exception as e:
        logger.error(f"Error in ticket cancellation: {e}", exc_info=True)
        await callback.answer("Ошибка при отмене")


async def notify_operators_about_ticket(ticket: Ticket, client_user: User, session):
    """Send notifications to all active operators about new ticket."""
    try:
        message_service = get_service(MessageService)
        if not message_service:
            logger.error("MessageService not available for operator notifications")
            return

        # Get all active operators
        operators = session.query(Operator, User).join(
            User, Operator.userID == User.userID
        ).filter(
            Operator.isActive == True,
            User.user_type.in_([UserType.OPERATOR, UserType.ADMIN])
        ).all()

        if not operators:
            logger.warning("No active operators available")
            return

        # Send notification to each operator
        for operator, operator_user in operators:
            try:
                # Send notification to operator's private chat
                endpoint = DialogueEndpoint('user', operator_user.telegramID)

                sent_message = await message_service.send_template_to_endpoint(
                    endpoint=endpoint,
                    template_key="/support/new_ticket_notification",
                    variables={
                        "ticket_id": ticket.ticketID,
                        "user_name": client_user.displayName,
                        "user_telegram_id": client_user.telegramID,
                        "category": ticket.category or "general",
                        "error_code": ticket.error_code or "None",
                        "created_at": ticket.createdAt.strftime('%H:%M') if ticket.createdAt else 'Now',
                        "take_callback": f"/ticket/take/{ticket.ticketID}/{operator.operatorID}"
                    }
                )

                # Store message ID for later deletion
                if sent_message:
                    notification_manager.store_notification(
                        ticket.ticketID,
                        operator_user.telegramID,
                        sent_message.message_id
                    )
                    logger.info(f"Notified operator {operator_user.telegramID} about ticket #{ticket.ticketID}")

            except Exception as e:
                logger.error(f"Failed to notify operator {operator_user.telegramID}: {e}")

    except Exception as e:
        logger.error(f"Error notifying operators: {e}", exc_info=True)


@dialogue_router.callback_query(F.data.startswith("/ticket/take/"))
@with_user(staff_only=True)
async def handle_take_ticket(callback: CallbackQuery, user, user_type, mainbot_user, session,
                             message_manager: MessageManager):
    """Handle operator taking a ticket."""
    try:
        # Parse callback data
        parts = callback.data.split('/')
        if len(parts) != 5:  # ['', 'ticket', 'take', ticket_id, operator_id]
            await callback.answer("Invalid callback data")
            return

        ticket_id = int(parts[3])
        operator_id = int(parts[4])

        # Verify operator
        operator = session.query(Operator).filter_by(operatorID=operator_id).first()
        if not operator or not operator.isActive:
            await callback.answer("You are not an active operator")
            return

        # Check if operator matches the user
        if operator.userID != user.userID:
            await callback.answer("You cannot take this ticket")
            return

        # Check ticket status
        ticket = session.query(Ticket).filter_by(ticketID=ticket_id).first()
        if not ticket:
            await callback.answer("Ticket not found")
            return

        if ticket.status != TicketStatus.OPEN:
            await callback.answer("Ticket already taken or closed")
            await delete_operator_notifications(ticket_id, callback.bot)
            return

        # Update ticket status
        ticket.status = TicketStatus.IN_PROGRESS
        ticket.assignedOperatorID = operator.operatorID
        ticket.assignedAt = datetime.utcnow()

        # Update operator's current tickets count
        operator.currentTicketsCount = (operator.currentTicketsCount or 0) + 1

        # Get DialogueService
        dialogue_service = get_service(DialogueService)
        if not dialogue_service:
            logger.error("DialogueService not available")
            await callback.answer("Service unavailable")
            return

        # Create dialogue
        dialogue_id = await dialogue_service.create_support_dialogue(
            ticket=ticket,
            operator_id=operator.operatorID,
            context={
                'operator_id': operator_id,
                'operator_name': operator.displayName or user.displayName,
                'taken_at': datetime.utcnow().isoformat()
            }
        )

        if dialogue_id:
            session.commit()
            await callback.answer("✅ Ticket assigned! Dialogue created.")

            # Delete notifications from all operators
            await delete_operator_notifications(ticket_id, callback.bot)

            logger.info(f"Operator {user.telegramID} took ticket #{ticket_id}, dialogue {dialogue_id} created")
        else:
            session.rollback()
            await callback.answer("❌ Failed to create dialogue")

    except Exception as e:
        logger.error(f"Error in handle_take_ticket: {e}", exc_info=True)
        await callback.answer("Error processing request")


async def delete_operator_notifications(ticket_id: int, bot):
    """Delete ticket notifications from all operators."""
    try:
        notifications = notification_manager.get_notifications(ticket_id)

        for operator_telegram_id, message_id in notifications.items():
            try:
                await bot.delete_message(
                    chat_id=operator_telegram_id,
                    message_id=message_id
                )
                logger.debug(f"Deleted notification for operator {operator_telegram_id}")
            except Exception as e:
                logger.warning(f"Failed to delete notification for {operator_telegram_id}: {e}")

        # Clear from memory
        notification_manager.clear_notifications(ticket_id)

    except Exception as e:
        logger.error(f"Error deleting notifications: {e}")


@dialogue_router.callback_query(F.data.startswith("/support/rate/"))
@with_user()
async def handle_rating(callback: CallbackQuery, user, user_type, mainbot_user, session, message_manager: MessageManager):
    """Handle client rating after ticket closure."""
    try:
        # Parse callback data
        parts = callback.data.split('/')
        if len(parts) != 5:  # ['', 'support', 'rate', ticket_id, rating]
            await callback.answer("Invalid callback data")
            return

        ticket_id = int(parts[3])
        rating = int(parts[4])

        # Verify ticket belongs to user
        ticket = session.query(Ticket).filter_by(
            ticketID=ticket_id,
            userID=user.userID
        ).first()

        if not ticket:
            await callback.answer("Ticket not found")
            return

        # Update ticket rating
        ticket.clientSatisfaction = rating

        # Update operator rating if assigned
        if ticket.assignedOperatorID:
            operator = session.query(Operator).filter_by(operatorID=ticket.assignedOperatorID).first()
            if operator:
                # Update operator's average rating
                current_rating = operator.satisfactionRating or 0
                total_ratings = operator.totalRatings or 0

                # Calculate new average
                new_total = total_ratings + 1
                new_rating = ((current_rating * total_ratings) + rating) / new_total

                operator.satisfactionRating = new_rating
                operator.totalRatings = new_total

        session.commit()

        # Thank user for feedback
        await message_manager.send_template(
            user=user,
            template_key="/support/thanks_for_rating",
            update=callback,
            variables={
                'rating': rating,
                'ticket_id': ticket_id
            },
            edit=True
        )

        await callback.answer("✅ Thank you for your feedback!")

    except Exception as e:
        logger.error(f"Error in handle_rating: {e}", exc_info=True)
        await callback.answer("Error processing rating")


@dialogue_router.callback_query(F.data.startswith("/support/feedback/"))
@with_user()
async def handle_feedback_prompt(callback: CallbackQuery, user, user_type, mainbot_user, session, message_manager: MessageManager):
    """Handle feedback prompt response."""
    try:
        # Parse callback data
        parts = callback.data.split('/')
        if len(parts) != 5:  # ['', 'support', 'feedback', ticket_id, action]
            await callback.answer("Invalid callback data")
            return

        ticket_id = int(parts[3])
        action = parts[4]  # 'yes' or 'no'

        if action == 'yes':
            # Show rating buttons
            await message_manager.send_template(
                user=user,
                template_key="/support/rate_your_experience",
                update=callback,
                variables={
                    'ticket_id': ticket_id
                },
                edit=True
            )
        else:
            # User doesn't want to rate
            await message_manager.send_template(
                user=user,
                template_key="/support/feedback_declined",
                update=callback,
                variables={
                    'ticket_id': ticket_id
                },
                edit=True
            )

        await callback.answer()

    except Exception as e:
        logger.error(f"Error in handle_feedback_prompt: {e}", exc_info=True)
        await callback.answer("Error processing feedback")


def setup_dialogue_handlers(dp):
    """Register dialogue handlers."""
    dp.include_router(dialogue_router)
    logger.info("Dialogue handlers have been set up")