"""
User decorator for automatically getting user objects in handlers.
Supports both client users and staff (operators/admins).

Usage examples:
    @with_user(require_mainbot=True)  # Client users only (must exist in mainbot)
    @with_user(staff_only=True)        # Staff only (operators/admins)
    @with_user()                       # Any user (creates record if needed)
"""
import logging
import functools
from typing import Callable, Any, Union
from aiogram import BaseMiddleware, Router
from aiogram.types import Message, CallbackQuery, TelegramObject
from sqlalchemy.orm import Session

from core.db import get_helpbot_session, get_mainbot_session
from models.user import User, UserType
from models.mainbot.user import User as MainbotUser
from core.message_manager import MessageManager
from config import Config

logger = logging.getLogger(__name__)


class UserMiddleware(BaseMiddleware):
    """
    Middleware for automatically getting user objects and injecting them into handlers.
    Handles both client users (from mainbot) and staff users (operators/admins).
    """

    def __init__(self, bot):
        self.bot = bot
        super().__init__()

    async def __call__(
            self,
            handler: Callable[[TelegramObject, dict[str, Any]], Any],
            event: TelegramObject,
            data: dict[str, Any]
    ) -> Any:
        with get_helpbot_session() as session:
            try:
                if isinstance(event, (Message, CallbackQuery)):
                    user, user_type, mainbot_user = get_or_create_user(event, session)

                    if not user:
                        # User not found and not authorized
                        message_manager = MessageManager(self.bot)
                        await message_manager.send_template(
                            user=None,
                            template_key="/errors/unauthorized",
                            update=event,
                            variables={}
                        )
                        return

                    # Validate configuration
                    await Config.validate_critical_keys()

                    data["user"] = user
                    data["user_type"] = user_type
                    data["mainbot_user"] = mainbot_user  # Can be None for staff
                    data["session"] = session
                    data["message_manager"] = MessageManager(self.bot)
                    data["bot"] = self.bot

                    return await handler(event, data)
                else:
                    return await handler(event, data)

            except ValueError as e:
                logger.error(f"Configuration error: {e}")
                Config.set(Config.SYSTEM_STATUS, "maintenance", source="UserMiddleware")
                if isinstance(event, (Message, CallbackQuery)):
                    message_manager = MessageManager(self.bot)
                    await message_manager.send_template(
                        user=user,
                        template_key="/system/maintenance",
                        update=event,
                        variables={},
                        edit=True
                    )
                return
            except Exception as e:
                logger.error(f"Error in UserMiddleware: {e}", exc_info=True)
                raise


def get_or_create_user(update: Union[Message, CallbackQuery], session: Session):
    telegram_id = update.from_user.id

    # СНАЧАЛА проверяем mainbot - это обязательно для ВСЕХ
    mainbot_user = None
    with get_mainbot_session() as mainbot_session:
        mainbot_user = mainbot_session.query(MainbotUser).filter_by(telegramID=telegram_id).first()

    # НЕТ в mainbot = НЕТ доступа, точка!
    if not mainbot_user:
        logger.warning(f"REJECTED: User {telegram_id} not found in mainbot")
        return None, None, None

    # Теперь проверяем/создаем в helpbot
    helpbot_user = session.query(User).filter_by(telegramID=telegram_id).first()

    if not helpbot_user:
        # Определяем тип на основе конфига админов
        admin_ids = Config.get(Config.ADMINS, [])
        user_type = UserType.ADMIN if telegram_id in admin_ids else UserType.CLIENT

        # Создаем с данными из mainbot
        helpbot_user = User(
            telegramID=telegram_id,
            user_type=user_type,
            lang=mainbot_user.lang,  # Всегда из mainbot!
            mainbot_user_id=mainbot_user.userID,
            nickname=mainbot_user.full_name,
            firstname=mainbot_user.firstname,
            lastname=mainbot_user.surname,
            status="active"
        )
        session.add(helpbot_user)
        session.commit()
        logger.info(f"Created helpbot user {telegram_id} from mainbot user {mainbot_user.userID}")

    return helpbot_user, helpbot_user.user_type, mainbot_user


def with_user(require_mainbot: bool = False, staff_only: bool = False):
    """
    Decorator for handlers to automatically get user, session, and message_manager.

    Args:
        require_mainbot: If True, handler requires user to exist in mainbot DB
        staff_only: If True, handler is only for staff (operators/admins)

    Example usage:
        @router.message(Command("help"))
        @with_user(require_mainbot=True)
        async def cmd_help(message: Message, user, mainbot_user, session):
            # Handler for clients only

        @router.message(Command("stats"))
        @with_user(staff_only=True)
        async def cmd_stats(message: Message, user, session):
            # Handler for staff only

        @router.message(Command("start"))
        @with_user()
        async def cmd_start(message: Message, user, user_type, mainbot_user, session):
            # Handler for any user
    """
    def decorator(handler):
        @functools.wraps(handler)
        async def wrapper(event: TelegramObject, *args, **kwargs):
            with get_helpbot_session() as session:
                try:
                    if isinstance(event, (Message, CallbackQuery)):
                        user, user_type, mainbot_user = get_or_create_user(event, session)

                        if not user:
                            return

                        # Check permissions
                        if staff_only and user_type == UserType.CLIENT:
                            logger.warning(f"Client {user.telegramID} tried to access staff-only handler")
                            return

                        if require_mainbot and not mainbot_user:
                            message_manager = kwargs.get('message_manager') or MessageManager(kwargs.get('bot'))
                            await message_manager.send_template(
                                user=user,
                                template_key="/errors/not_registered",
                                update=event,
                                variables={}
                            )
                            return

                        # Validate configuration
                        await Config.validate_critical_keys()

                        updated_kwargs = {
                            'user': user,
                            'user_type': user_type,
                            'mainbot_user': mainbot_user,
                            'session': session,
                            'message_manager': kwargs.get('message_manager')
                        }

                        # Add to kwargs if they don't exist
                        for key, value in updated_kwargs.items():
                            if key not in kwargs:
                                kwargs[key] = value

                        return await handler(event, *args, **kwargs)
                    else:
                        return await handler(event, *args, **kwargs)

                except ValueError as e:
                    logger.error(f"Configuration error: {e}")
                    Config.set(Config.SYSTEM_STATUS, "maintenance", source=handler.__name__)
                    message_manager = kwargs.get('message_manager')
                    if user and message_manager:
                        await message_manager.send_template(
                            user=user,
                            template_key="/system/maintenance",
                            update=event,
                            variables={},
                            edit=True
                        )
                    return
                except Exception as e:
                    logger.error(f"Error in handler {handler.__name__}: {e}", exc_info=True)
                    raise

        return wrapper
    return decorator


def setup_user_middleware(router: Router, bot):
    """
    Register UserMiddleware with a router.

    Args:
        router: Router to attach middleware to
        bot: Bot instance
    """
    router.message.middleware(UserMiddleware(bot))
    router.callback_query.middleware(UserMiddleware(bot))