"""
Administrative commands for helpbot.
Provides commands for administrators to manage operators and view statistics.
"""
import logging
import re
from typing import Any, Callable, Awaitable
from datetime import datetime, timedelta

from aiogram import Router, Bot, F, Dispatcher
from aiogram.types import Message, TelegramObject, CallbackQuery
from aiogram import BaseMiddleware
from sqlalchemy import desc, and_, or_, func

from core.templates import MessageTemplates
from core.message_manager import MessageManager
from core.user_decorator import with_user
from config import Config
from models.user import User, UserType
from models.operator import Operator
from models.ticket import Ticket, TicketStatus
from services.mainbot_service import MainbotService
from services.data_importer import ConfigImporter

logger = logging.getLogger(__name__)

admin_router = Router(name="admin_router")


class AdminMiddleware(BaseMiddleware):
    """Middleware to check admin permissions."""

    def __init__(self, bot: Bot):
        self.bot = bot
        super().__init__()

    async def __call__(
            self,
            handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
            event: TelegramObject,
            data: dict[str, Any]
    ) -> Any:
        data["bot"] = self.bot

        if isinstance(event, (Message, CallbackQuery)):
            user_id = event.from_user.id

            if isinstance(event, Message) and event.text and event.text.startswith('&'):
                logger.info(f"AdminMiddleware: processing command '{event.text}' from user {user_id}")

            # Skip admin check for messages in threads (for operator commands)
            if isinstance(event, Message) and hasattr(event, 'message_thread_id') and event.message_thread_id:
                logger.debug(f"Skipping admin check for thread message from {user_id}")
                return await handler(event, data)

            # Get user from data (injected by UserMiddleware)
            user = data.get('user')

            if not user:
                logger.warning(f"User object not found for {user_id}")
                return None

            # Check if user is ADMIN in database (more secure than just checking config)
            if user.user_type != UserType.ADMIN:
                logger.warning(
                    f"Non-admin user {user_id} (type: {user.user_type.value}) attempted to access admin command")
                # Optionally send a message about insufficient permissions
                if isinstance(event, Message) and event.text:
                    await event.answer("⛔ You don't have permission to use admin commands.")
                return None

            if isinstance(event, Message) and event.text:
                logger.info(f"Admin {user_id} executed command: {event.text}")

        return await handler(event, data)


# === Configuration Commands ===

@admin_router.message(F.text == '&upconfig')
@with_user(staff_only=True)
async def handle_upconfig(message: Message, user, user_type, mainbot_user, session, bot: Bot,
                          message_manager: MessageManager):
    """Update bot configuration from Google Sheets."""
    try:
        reply = await message_manager.send_template(
            user=user,
            template_key="/admin/upconfig_start",
            update=message,
            variables={"session": session}
        )

        config_dict = await ConfigImporter.import_config()

        # Only update helpbot-specific configs
        updated_keys = []
        for key, value in config_dict.items():
            if key in ('GROUP_ID', 'TICKET_CATEGORIES', 'AUTO_CLOSE_HOURS', 'REMINDER_INTERVALS'):
                config_key = getattr(Config, key, None)
                if config_key:
                    Config.set(config_key, value, source="sheets")
                    updated_keys.append(key)

        await message_manager.send_template(
            user=user,
            template_key="/admin/upconfig_complete",
            update=reply,
            variables={
                "session": session,
                "updated_keys": updated_keys,
                "total_keys": len(config_dict)
            },
            edit=True
        )

        logger.info(f"Configuration updated by admin {message.from_user.id}")
    except Exception as e:
        logger.error(f"Error in upconfig command: {e}", exc_info=True)
        await message_manager.send_template(
            user=user,
            template_key="/admin/error",
            update=message,
            variables={
                "session": session,
                "error": str(e),
                "command": "upconfig"
            }
        )


@admin_router.message(F.text == '&ut')
@with_user(staff_only=True)
async def handle_update_templates(message: Message, user, user_type, mainbot_user, session,
                                  message_manager: MessageManager):
    """Update message templates from Google Sheets."""
    try:
        reply = await message_manager.send_template(
            user=user,
            template_key="/admin/update_templates_start",
            update=message,
            variables={"session": session}
        )

        await MessageTemplates.load_templates()

        await message_manager.send_template(
            user=user,
            template_key="/admin/update_templates_complete",
            update=reply,
            variables={"session": session},
            edit=True
        )
    except Exception as e:
        logger.error(f"Error updating templates: {e}", exc_info=True)
        await message_manager.send_template(
            user=user,
            template_key="/admin/error",
            update=message,
            variables={
                "session": session,
                "error": str(e),
                "command": "update_templates"
            }
        )


# === Operator Management ===

@admin_router.message(F.text == '&whoami')
@with_user(staff_only=True)
async def handle_whoami(message: Message, user, user_type, mainbot_user, session, message_manager: MessageManager):
    """Show current user status and permissions."""
    try:
        admin_ids = Config.get(Config.ADMINS, [])

        await message_manager.send_template(
            user=user,
            template_key="/admin/whoami",
            update=message,
            variables={
                "session": session,
                "telegram_id": user.telegramID,
                "user_type": user.user_type.value,
                "is_in_admin_config": user.telegramID in admin_ids,
                "display_name": user.displayName,
                "permissions": user.get_permissions() if user.isStaff else {}
            }
        )
    except Exception as e:
        logger.error(f"Error in whoami command: {e}", exc_info=True)
        await message_manager.send_template(
            user=user,
            template_key="/admin/error",
            update=message,
            variables={
                "session": session,
                "error": str(e),
                "command": "whoami"
            }
        )


@admin_router.message(F.text.startswith('&opadd_'))
@with_user(staff_only=True)
async def handle_add_operator(message: Message, user, user_type, mainbot_user, session,
                              message_manager: MessageManager):
    """Add operator. Format: &opadd_12345"""
    try:
        match = re.match(r'^&opadd_(\d+)$', message.text)
        if not match:
            await message_manager.send_template(
                user=user,
                template_key="/admin/operator_invalid_format",
                update=message,
                variables={"session": session, "action": "add"}
            )
            return

        telegram_id = int(match.group(1))

        # Check if user already exists
        target_user = session.query(User).filter_by(telegramID=telegram_id).first()

        if not target_user:
            # Create new user
            target_user = User(
                telegramID=telegram_id,
                user_type=UserType.OPERATOR,  # ТОЛЬКО оператор!
                nickname=f"Operator {telegram_id}",
                status="active"
            )
            session.add(target_user)
            session.flush()

        # Check if operator record exists
        existing_operator = session.query(Operator).filter_by(userID=target_user.userID).first()

        if existing_operator:
            if existing_operator.isActive:
                template_key = "/admin/operator_already_exists"
            else:
                existing_operator.isActive = True
                target_user.user_type = UserType.OPERATOR  # ТОЛЬКО оператор!
                session.commit()
                template_key = "/admin/operator_reactivated"
        else:
            # Create operator record
            new_operator = Operator(
                userID=target_user.userID,
                telegramID=telegram_id,
                displayName=target_user.displayName,
                isActive=True,
                maxConcurrentTickets=5
            )
            session.add(new_operator)
            target_user.user_type = UserType.OPERATOR  # ТОЛЬКО оператор!
            session.commit()
            template_key = "/admin/operator_added"

        await message_manager.send_template(
            user=user,
            template_key=template_key,
            update=message,
            variables={
                "session": session,
                "telegram_id": telegram_id,
                "role": "Operator"  # ТОЛЬКО оператор!
            }
        )

        logger.info(f"Admin {message.from_user.id} added operator {telegram_id}")
    except Exception as e:
        logger.error(f"Error adding operator: {e}", exc_info=True)
        await message_manager.send_template(
            user=user,
            template_key="/admin/error",
            update=message,
            variables={
                "session": session,
                "error": str(e),
                "command": "add_operator"
            }
        )


@admin_router.message(F.text.startswith('&opremove_'))
@with_user(staff_only=True)
async def handle_remove_operator(message: Message, user, user_type, mainbot_user, session,
                                 message_manager: MessageManager):
    """Remove operator."""
    try:
        match = re.match(r'&opremove_(\d+)', message.text)
        if not match:
            await message_manager.send_template(
                user=user,
                template_key="/admin/operator_invalid_format",
                update=message,
                variables={"session": session, "action": "remove"}
            )
            return

        telegram_id = int(match.group(1))
        target_user = session.query(User).filter_by(telegramID=telegram_id).first()

        if not target_user:
            template_key = "/admin/user_not_found"
        else:
            operator = session.query(Operator).filter_by(userID=target_user.userID).first()

            if not operator:
                template_key = "/admin/not_operator"
            else:
                operator.isActive = False
                target_user.user_type = UserType.CLIENT  # Downgrade to client
                session.commit()
                template_key = "/admin/operator_removed"

        await message_manager.send_template(
            user=user,
            template_key=template_key,
            update=message,
            variables={
                "session": session,
                "telegram_id": telegram_id
            }
        )

        logger.info(f"Admin {message.from_user.id} removed operator {telegram_id}")
    except Exception as e:
        logger.error(f"Error removing operator: {e}", exc_info=True)
        await message_manager.send_template(
            user=user,
            template_key="/admin/error",
            update=message,
            variables={
                "session": session,
                "error": str(e),
                "command": "remove_operator"
            }
        )


@admin_router.message(F.text == '&oplist')
@with_user(staff_only=True)
async def handle_list_operators(message: Message, user, user_type, mainbot_user, session,
                                message_manager: MessageManager):
    """List all operators with statistics."""
    try:
        operators = (
            session.query(Operator, User)
                .join(User, Operator.userID == User.userID)
                .filter(Operator.isActive == True)
                .all()
        )

        template_keys = ["/admin/operators_header"]

        if operators:
            template_keys.append("/admin/operators_item")

            # Prepare data for rgroup
            idx_list = []
            telegram_id_list = []
            display_name_list = []
            role_list = []
            active_tickets_list = []
            total_resolved_list = []
            avg_time_list = []
            rating_list = []

            for idx, (operator, user_obj) in enumerate(operators, 1):
                idx_list.append(idx)
                telegram_id_list.append(operator.telegramID)
                display_name_list.append(operator.displayName or user_obj.nickname or 'Unknown')
                role_list.append("Admin" if user_obj.user_type == UserType.ADMIN else "Operator")
                active_tickets_list.append(operator.currentTicketsCount or 0)
                total_resolved_list.append(operator.totalTicketsResolved or 0)
                avg_time_list.append(f"{operator.avgResolutionTime or 0} min")

                if operator.satisfactionRating and operator.totalRatings:
                    rating_list.append(f"{operator.satisfactionRating:.1f}⭐ ({operator.totalRatings})")
                else:
                    rating_list.append("No ratings")

            rgroup_data = {
                "idx": idx_list,
                "telegram_id": telegram_id_list,
                "display_name": display_name_list,
                "role": role_list,
                "active_tickets": active_tickets_list,
                "total_resolved": total_resolved_list,
                "avg_time": avg_time_list,
                "rating": rating_list
            }
        else:
            template_keys.append("/admin/operators_empty")
            rgroup_data = {}

        template_keys.append("/admin/operators_footer")

        await message_manager.send_template(
            user=user,
            template_key=template_keys,
            update=message,
            variables={
                "session": session,
                "rgroup": rgroup_data
            }
        )
    except Exception as e:
        logger.error(f"Error listing operators: {e}", exc_info=True)
        await message_manager.send_template(
            user=user,
            template_key="/admin/error",
            update=message,
            variables={
                "session": session,
                "error": str(e),
                "command": "list_operators"
            }
        )


@admin_router.message(F.text == '&handlers')
@with_user(staff_only=True)
async def handle_show_handlers(message: Message, user, user_type, mainbot_user, session,
                               message_manager: MessageManager):
    """Show handler statistics and debug info."""
    try:
        from core.di import get_service
        from core.input_service import InputService

        input_service = get_service(InputService)
        if not input_service:
            await message_manager.send_template(
                user=user,
                template_key="/admin/error",
                update=message,
                variables={
                    "session": session,
                    "error": "InputService not available",
                    "command": "handlers"
                }
            )
            return

        # Get overall statistics
        stats = input_service.get_all_handlers_stats()

        # Build message
        message_text = "📊 <b>Handler Statistics</b>\n\n"
        message_text += f"Router handlers: {stats['total_in_router']}\n"
        message_text += f"Tracked handlers: {stats['total_in_dict']}\n"
        message_text += f"User handlers: {stats['user_handlers']}\n"
        message_text += f"Thread handlers: {stats['thread_handlers']}\n"

        # Potential zombies
        if stats['potential_zombies'] > 0:
            message_text += f"\n⚠️ <b>Potential zombies: {stats['potential_zombies']}</b>\n"

        # Users with handlers
        if stats['users_with_handlers']:
            message_text += "\n👥 <b>Users with handlers:</b>\n"
            for user_id, count in stats['users_with_handlers'].items():
                # Get user info
                handler_user = session.query(User).filter_by(telegramID=int(user_id)).first()
                user_name = handler_user.displayName if handler_user else f"Unknown ({user_id})"

                # Check if user has active dialogue
                active_dialogue = session.query(Dialogue).filter_by(
                    userID=handler_user.userID if handler_user else None,
                    status='active'
                ).first() if handler_user else None

                status = "✅ Active" if active_dialogue else "⚠️ No active dialogue"

                message_text += f"  • {user_name}: {count} handler(s) - {status}\n"

                # If no active dialogue but has handlers - these are zombies!
                if not active_dialogue and count > 0:
                    message_text += f"    <i>→ Zombie handlers detected!</i>\n"

        # Send message
        await message.answer(message_text, parse_mode="HTML")

    except Exception as e:
        logger.error(f"Error in show_handlers command: {e}", exc_info=True)
        await message_manager.send_template(
            user=user,
            template_key="/admin/error",
            update=message,
            variables={
                "session": session,
                "error": str(e),
                "command": "handlers"
            }
        )


@admin_router.message(F.text.startswith('&handlers_'))
@with_user(staff_only=True)
async def handle_user_handlers(message: Message, user, user_type, mainbot_user, session,
                               message_manager: MessageManager):
    """Show handlers for specific user. Format: &handlers_12345"""
    try:
        match = re.match(r'^&handlers_(\d+)$', message.text)
        if not match:
            await message.answer("Invalid format. Use: &handlers_12345")
            return

        target_telegram_id = int(match.group(1))

        from core.di import get_service
        from core.input_service import InputService

        input_service = get_service(InputService)
        if not input_service:
            await message_manager.send_template(
                user=user,
                template_key="/admin/error",
                update=message,
                variables={
                    "session": session,
                    "error": "InputService not available",
                    "command": "handlers"
                }
            )
            return

        # Get user handlers
        user_handlers = input_service.get_user_handlers(target_telegram_id)

        # Get user info
        target_user = session.query(User).filter_by(telegramID=target_telegram_id).first()

        # Build message
        message_text = f"🔍 <b>Handlers for user {target_telegram_id}</b>\n"
        if target_user:
            message_text += f"Name: {target_user.displayName}\n"
            message_text += f"FSM State: {target_user.get_fsm_state() or 'None'}\n"

            # Check FSM context
            fsm_context = target_user.get_fsm_context()
            if fsm_context and 'dialogue_id' in fsm_context:
                message_text += f"FSM Dialogue: {fsm_context['dialogue_id']}\n"
        else:
            message_text += "User not found in DB\n"

        message_text += f"\n<b>Handlers ({len(user_handlers)}):</b>\n"

        if user_handlers:
            for handler in user_handlers:
                message_text += f"\n• Handler ID: {handler['handler_id']}\n"
                message_text += f"  State: {handler['state'] or 'any'}\n"
                message_text += f"  Unique ID: {handler['unique_id']}\n"
                message_text += f"  Registered at: {handler['registered_at']}\n"
                message_text += f"  In Router: {'✅' if handler['has_router_object'] else '❌'}\n"
        else:
            message_text += "No handlers found\n"

        # Check for active dialogue
        if target_user:
            active_dialogue = session.query(Dialogue).filter_by(
                userID=target_user.userID,
                status='active'
            ).first()

            if active_dialogue:
                message_text += f"\n✅ <b>Active dialogue:</b> {active_dialogue.dialogueID}\n"
            else:
                message_text += "\n⚠️ <b>No active dialogue</b>\n"
                if user_handlers:
                    message_text += "→ These are zombie handlers!\n"

        await message.answer(message_text, parse_mode="HTML")

    except Exception as e:
        logger.error(f"Error in user_handlers command: {e}", exc_info=True)
        await message_manager.send_template(
            user=user,
            template_key="/admin/error",
            update=message,
            variables={
                "session": session,
                "error": str(e),
                "command": "user_handlers"
            }
        )


@admin_router.message(F.text == '&cleanup_zombies')
@with_user(staff_only=True)
async def handle_cleanup_zombies(message: Message, user, user_type, mainbot_user, session,
                                 message_manager: MessageManager):
    """Clean up all zombie handlers (handlers without active dialogues)."""
    try:
        from core.di import get_service
        from core.input_service import InputService

        input_service = get_service(InputService)
        if not input_service:
            await message_manager.send_template(
                user=user,
                template_key="/admin/error",
                update=message,
                variables={
                    "session": session,
                    "error": "InputService not available",
                    "command": "cleanup_zombies"
                }
            )
            return

        # Get all handlers stats
        stats = input_service.get_all_handlers_stats()

        cleaned_count = 0
        zombie_users = []

        # Check each user with handlers
        for user_id_str, handler_count in stats['users_with_handlers'].items():
            user_telegram_id = int(user_id_str)

            # Check if user has active dialogue
            handler_user = session.query(User).filter_by(telegramID=user_telegram_id).first()
            if handler_user:
                active_dialogue = session.query(Dialogue).filter_by(
                    userID=handler_user.userID,
                    status='active'
                ).first()

                if not active_dialogue:
                    # No active dialogue - these are zombies!
                    logger.info(
                        f"[CLEANUP_ZOMBIES] Cleaning {handler_count} zombie handlers for user {user_telegram_id}")
                    await input_service.cleanup_user_handlers(user_telegram_id)
                    cleaned_count += handler_count
                    zombie_users.append(user_telegram_id)

        # Build response
        message_text = "🧹 <b>Zombie Cleanup Complete</b>\n\n"
        message_text += f"Cleaned handlers: {cleaned_count}\n"
        message_text += f"Affected users: {len(zombie_users)}\n"

        if zombie_users:
            message_text += "\n<b>Cleaned users:</b>\n"
            for telegram_id in zombie_users[:10]:  # Show max 10 users
                message_text += f"  • {telegram_id}\n"
            if len(zombie_users) > 10:
                message_text += f"  ... and {len(zombie_users) - 10} more\n"

        # Get new stats
        new_stats = input_service.get_all_handlers_stats()
        message_text += f"\n<b>After cleanup:</b>\n"
        message_text += f"Router handlers: {new_stats['total_in_router']}\n"
        message_text += f"Tracked handlers: {new_stats['total_in_dict']}\n"

        await message.answer(message_text, parse_mode="HTML")

        logger.info(f"Admin {message.from_user.id} cleaned {cleaned_count} zombie handlers")

    except Exception as e:
        logger.error(f"Error in cleanup_zombies command: {e}", exc_info=True)
        await message_manager.send_template(
            user=user,
            template_key="/admin/error",
            update=message,
            variables={
                "session": session,
                "error": str(e),
                "command": "cleanup_zombies"
            }
        )


# === Statistics Commands ===

@admin_router.message(F.text == '&stats')
@with_user(staff_only=True)
async def handle_stats(message: Message, user, user_type, mainbot_user, session, message_manager: MessageManager):
    """Show general helpbot statistics."""
    try:
        # Get statistics
        total_tickets = session.query(Ticket).count()
        open_tickets = session.query(Ticket).filter(
            Ticket.status.in_([TicketStatus.OPEN, TicketStatus.IN_PROGRESS])
        ).count()

        # Statistics for last 7 days
        week_ago = datetime.utcnow() - timedelta(days=7)
        recent_tickets = session.query(Ticket).filter(
            Ticket.createdAt >= week_ago
        ).count()

        resolved_tickets = session.query(Ticket).filter(
            Ticket.status == TicketStatus.RESOLVED,
            Ticket.resolvedAt >= week_ago
        ).count()

        # Average resolution time
        avg_resolution = session.query(Ticket).filter(
            Ticket.status == TicketStatus.RESOLVED,
            Ticket.resolutionTime.isnot(None)
        ).with_entities(
            func.avg(Ticket.resolutionTime)
        ).scalar() or 0

        # Client satisfaction
        avg_satisfaction = session.query(Ticket).filter(
            Ticket.clientSatisfaction.isnot(None)
        ).with_entities(
            func.avg(Ticket.clientSatisfaction)
        ).scalar() or 0

        # Categories breakdown
        category_stats = session.query(
            Ticket.category,
            func.count(Ticket.ticketID)
        ).group_by(Ticket.category).all()

        await message_manager.send_template(
            user=user,
            template_key="/admin/stats",
            update=message,
            variables={
                "session": session,
                "total_tickets": total_tickets,
                "open_tickets": open_tickets,
                "recent_tickets": recent_tickets,
                "resolved_tickets": resolved_tickets,
                "avg_resolution_minutes": int(avg_resolution),
                "avg_satisfaction": f"{avg_satisfaction:.1f}" if avg_satisfaction else "N/A",
                "category_stats": category_stats
            }
        )
    except Exception as e:
        logger.error(f"Error getting stats: {e}", exc_info=True)
        await message_manager.send_template(
            user=user,
            template_key="/admin/error",
            update=message,
            variables={
                "session": session,
                "error": str(e),
                "command": "stats"
            }
        )


# === User Information Commands ===

@admin_router.message(F.text.startswith('&user_'))
@with_user(staff_only=True)
async def handle_user_info(message: Message, user, user_type, mainbot_user, session, message_manager: MessageManager):
    """Show detailed user information from mainbot. Format: &user_12345"""
    try:
        match = re.match(r'&user_(\d+)', message.text)
        if not match:
            await message_manager.send_template(
                user=user,
                template_key="/admin/invalid_user_format",
                update=message,
                variables={"session": session}
            )
            return

        telegram_id = int(match.group(1))

        # Get user info from mainbot
        user_summary = await MainbotService.get_user_summary(telegram_id)

        if not user_summary:
            await message_manager.send_template(
                user=user,
                template_key="/admin/mainbot_user_not_found",
                update=message,
                variables={
                    "session": session,
                    "telegram_id": telegram_id
                }
            )
            return

        # Get recent activity
        recent_activity = await MainbotService.get_recent_activity(
            user_summary['user_id'], days=30
        )

        # Get latest purchases
        purchases = await MainbotService.get_user_purchases(
            user_summary['user_id'], limit=5
        )

        # Get latest payments
        payments = await MainbotService.get_user_payments(
            user_summary['user_id'], limit=5
        )

        await message_manager.send_template(
            user=user,
            template_key="/admin/user_info",
            update=message,
            variables={
                "session": session,
                "user_info": user_summary,
                "recent_activity": recent_activity,
                "purchases": purchases,
                "payments": payments
            }
        )

    except Exception as e:
        logger.error(f"Error getting user info: {e}", exc_info=True)
        await message_manager.send_template(
            user=user,
            template_key="/admin/error",
            update=message,
            variables={
                "session": session,
                "error": str(e),
                "command": "user_info"
            }
        )


# === Ticket Commands ===

@admin_router.message(F.text.startswith('&ticket_'))
@with_user(staff_only=True)
async def handle_ticket_info(message: Message, user, user_type, mainbot_user, session, message_manager: MessageManager):
    """Show detailed ticket information. Format: &ticket_123"""
    try:
        match = re.match(r'&ticket_(\d+)', message.text)
        if not match:
            await message_manager.send_template(
                user=user,
                template_key="/admin/invalid_ticket_format",
                update=message,
                variables={"session": session}
            )
            return

        ticket_id = int(match.group(1))

        # Get ticket with relations
        ticket = session.query(Ticket).filter_by(ticketID=ticket_id).first()

        if not ticket:
            await message_manager.send_template(
                user=user,
                template_key="/admin/ticket_not_found",
                update=message,
                variables={
                    "session": session,
                    "ticket_id": ticket_id
                }
            )
            return

        # Get user info from mainbot if available
        user_info = None
        if ticket.mainbot_user_id:
            user_info = await MainbotService.get_user_summary(
                ticket.user.telegramID
            )

        await message_manager.send_template(
            user=user,
            template_key="/admin/ticket_info",
            update=message,
            variables={
                "session": session,
                "ticket": ticket,
                "user_info": user_info,
                "operator_name": ticket.operator.displayName if ticket.operator else "Not assigned"
            }
        )

    except Exception as e:
        logger.error(f"Error getting ticket info: {e}", exc_info=True)
        await message_manager.send_template(
            user=user,
            template_key="/admin/error",
            update=message,
            variables={
                "session": session,
                "error": str(e),
                "command": "ticket_info"
            }
        )


# === System Commands ===

@admin_router.message(F.text == '&maintenance')
@with_user(staff_only=True)
async def handle_maintenance_mode(message: Message, user, user_type, mainbot_user, session,
                                  message_manager: MessageManager):
    """Enable maintenance mode."""
    try:
        current_status = Config.get(Config.SYSTEM_STATUS)

        if current_status == "maintenance":
            template_key = "/admin/already_maintenance"
        else:
            Config.set(Config.SYSTEM_STATUS, "maintenance", source="admin")
            template_key = "/admin/maintenance_enabled"
            logger.warning(f"System set to maintenance mode by admin {user.telegramID}")

        await message_manager.send_template(
            user=user,
            template_key=template_key,
            update=message,
            variables={"session": session}
        )
    except Exception as e:
        logger.error(f"Error setting maintenance mode: {e}", exc_info=True)
        await message_manager.send_template(
            user=user,
            template_key="/admin/error",
            update=message,
            variables={
                "session": session,
                "error": str(e),
                "command": "maintenance"
            }
        )


@admin_router.message(F.text == '&online')
@with_user(staff_only=True)
async def handle_online_mode(message: Message, user, user_type, mainbot_user, session, bot: Bot,
                             message_manager: MessageManager):
    """Enable online mode."""
    try:
        current_status = Config.get(Config.SYSTEM_STATUS)

        if current_status == "online":
            template_key = "/admin/already_online"
        else:
            try:
                await Config.validate_critical_keys()
                Config.set(Config.SYSTEM_STATUS, "online", source="admin")
                template_key = "/admin/online_enabled"
                logger.info(f"System set to online mode by admin {user.telegramID}")
            except Exception as validation_error:
                template_key = "/admin/online_failed"
                logger.error(f"Failed to set system online: {validation_error}")

        await message_manager.send_template(
            user=user,
            template_key=template_key,
            update=message,
            variables={
                "session": session,
                "error": validation_error if 'validation_error' in locals() else None
            }
        )
    except Exception as e:
        logger.error(f"Error setting online mode: {e}", exc_info=True)
        await message_manager.send_template(
            user=user,
            template_key="/admin/error",
            update=message,
            variables={
                "session": session,
                "error": str(e),
                "command": "online"
            }
        )


@admin_router.message(F.text.startswith('&'))
@with_user(staff_only=True)
async def handle_unknown_admin_command(message: Message, user, user_type, mainbot_user, session,
                                       message_manager: MessageManager):
    """Handle unknown admin commands - show help."""
    command = message.text.strip()
    logger.info(f"Admin {message.from_user.id} requested unknown command: {command}")

    await message_manager.send_template(
        user=user,
        template_key="/admin/help",
        update=message,
        variables={"session": session}
    )


@admin_router.message(
    F.content_type.in_({'document', 'photo', 'video', 'sticker'})
)
@with_user(staff_only=True)
async def handle_admin_file(message: Message, user, user_type, mainbot_user, session, message_manager: MessageManager):
    """Get file_id of uploaded media for use in templates."""
    try:
        file_type = None
        file_id = None
        file_details = {}

        if message.document:
            document = message.document
            file_type = "document"
            file_id = document.file_id
            file_details = {
                "name": document.file_name or "unknown_file",
                "mime_type": document.mime_type or "unknown/type",
                "size": document.file_size or 0
            }
        elif message.photo:
            photo = message.photo[-1]
            file_type = "photo"
            file_id = photo.file_id
            file_details = {
                "width": photo.width,
                "height": photo.height,
                "size": photo.file_size
            }
        elif message.video:
            video = message.video
            file_type = "video"
            file_id = video.file_id
            file_details = {
                "duration": video.duration,
                "width": video.width,
                "height": video.height,
                "size": video.file_size
            }
        elif message.sticker:
            sticker = message.sticker
            file_type = "sticker"
            file_id = sticker.file_id
            file_details = {
                "set_name": sticker.set_name,
                "emoji": sticker.emoji,
                "width": sticker.width,
                "height": sticker.height,
                "is_animated": sticker.is_animated,
                "is_video": sticker.is_video
            }

        await message_manager.send_template(
            user=user,
            template_key="/admin/file_info",
            update=message,
            variables={
                "session": session,
                "file_type": file_type,
                "file_id": file_id,
                "file_details": file_details
            }
        )
    except Exception as e:
        logger.error(f"Error processing file: {e}", exc_info=True)
        await message_manager.send_template(
            user=user,
            template_key="/admin/error",
            update=message,
            variables={
                "session": session,
                "error": str(e),
                "command": "file_upload"
            }
        )


def setup_admin_handlers(dp: Dispatcher, bot: Bot):
    """Register admin handlers."""
    logger.info("Setting up admin handlers")
    admin_router.message.middleware(AdminMiddleware(bot))
    dp.include_router(admin_router)
    logger.info("Admin handlers have been set up")
