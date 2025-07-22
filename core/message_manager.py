"""
Enhanced message manager for handling templates, callbacks, and message sending.
This core component manages interaction between users and templates with preAction/postAction support.
"""
import logging
from typing import Optional, Union, Any, Dict, List, Tuple

from aiogram import Bot
from aiogram.types import Message, CallbackQuery
from aiogram.types import InputMediaPhoto, InputMediaVideo
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError

from core.templates import MessageTemplates

logger = logging.getLogger(__name__)


class MessageManager:
    """
    Message manager for aiogram 3.x that handles template processing,
    callbacks, and message delivery with media attachments.
    """

    def __init__(self, bot: Bot):
        """
        Initialize message manager with bot instance.

        Args:
            bot: Aiogram Bot instance
        """
        self.bot = bot

    async def send_template(
            self,
            user,
            template_key: Union[str, List[str]],
            update: Union[Message, CallbackQuery],
            variables: Optional[Dict[str, Any]] = None,
            edit: bool = False,
            delete_original: bool = False,
            override_media_id: Optional[str] = None,
            media_type: Optional[str] = None,
            execute_preaction: bool = True
    ) -> Optional[Message]:
        """
        Send a message based on template.

        Args:
            user: User object for localization
            template_key: Template key or list of keys
            update: Message or CallbackQuery that triggered this action
            variables: Optional variables for template formatting
            edit: Whether to edit existing message or send new one
            delete_original: Whether to delete the original message
            override_media_id: Override media ID from template
            media_type: Override media type from template
            execute_preaction: Whether to execute preAction

        Returns:
            Optional[Message]: The sent or edited message, or None on error
        """
        try:
            print(
                f"SEND_TEMPLATE: template_key={template_key}, variables={variables}, execute_preaction={execute_preaction}")

            chat_id, message_id = self._extract_message_info(update)

            template_data = await self._prepare_template(
                user=user,
                template_key=template_key,
                variables=variables,
                override_media_id=override_media_id,
                media_type=media_type,
                execute_preaction=execute_preaction
            )

            print(f"TEMPLATE_DATA после _prepare_template: {template_data}")

            if not template_data:
                logger.error(f"Failed to prepare template for {template_key}")
                return None

            text, media_id, keyboard, parse_mode_str, disable_preview, preaction, postaction = template_data
            parse_mode = self._parse_mode_to_enum(parse_mode_str)

            if delete_original and message_id:
                try:
                    await self.bot.delete_message(chat_id=chat_id, message_id=message_id)
                    edit = False  # Force new message after deletion
                except TelegramAPIError as e:
                    logger.warning(f"Error deleting message: {e}")

            return await self._send_message(
                chat_id=chat_id,
                text=text,
                media_id=media_id,
                media_type=media_type or "photo" if media_id else None,
                keyboard=keyboard,
                parse_mode=parse_mode,
                disable_preview=disable_preview,
                edit=edit and not delete_original,
                message_id=message_id if edit and not delete_original else None
            )

        except Exception as e:
            logger.error(f"Error sending template message: {e}", exc_info=True)
            if isinstance(update, CallbackQuery):
                await update.answer("Error processing message")
            return None

    async def process_callback(self, callback_query: CallbackQuery, user, current_state: str,
                               variables: Optional[Dict[str, Any]] = None, edit: bool = True,
                               delete_original: bool = False, override_media_id: Optional[str] = None,
                               media_type: Optional[str] = None, execute_preaction: bool = True) -> None:
        try:
            logger.debug(f"Processing callback: {callback_query.data} for template: {current_state}")
            next_state = await self._execute_postaction(
                callback_query=callback_query,
                user=user,
                template_key=current_state,
                variables=variables
            )
            if next_state:
                logger.debug(f"Navigating to next state: {next_state}")
                await self.send_template(
                    user=user,
                    template_key=next_state,
                    update=callback_query,
                    variables=variables,
                    edit=edit,
                    delete_original=delete_original,
                    override_media_id=override_media_id,
                    media_type=media_type,
                    execute_preaction=execute_preaction
                )
            else:
                logger.debug(f"No state change (next_state: {next_state}, current_state: {current_state})")
                await callback_query.answer()
        except Exception as e:
            logger.error(f"Error processing callback: {e}", exc_info=True)
            await callback_query.answer("Error processing your request")

    async def _prepare_template(
            self,
            user,
            template_key: Union[str, List[str]],
            variables: Optional[Dict[str, Any]] = None,
            override_media_id: Optional[str] = None,
            media_type: Optional[str] = None,
            execute_preaction: bool = True
    ) -> Optional[Tuple]:
        """
        Prepare template data for sending.

        Args:
            user: User object
            template_key: Template key or list of keys
            variables: Variables for template
            override_media_id: Override media ID
            media_type: Override media type
            execute_preaction: Whether to execute preAction

        Returns:
            Optional[tuple]: Prepared template data or None on failure
        """
        try:
            print(
                f"_PREPARE_TEMPLATE: template_key={template_key}, variables={variables}, execute_preaction={execute_preaction}")

            variables = variables.copy() if variables else {}
            first_template, preaction = await self._get_first_template_and_preaction(
                user=user,
                template_key=template_key
            )

            print(f"FIRST_TEMPLATE: {first_template}, PREACTION: {preaction}")

            if execute_preaction and preaction:
                print(f"ВЫПОЛНЯЕМ PREACTION: {preaction} с variables={variables}")

                updated_vars = await MessageTemplates.execute_preaction(
                    preaction, user, variables
                )
                print(f"РЕЗУЛЬТАТ PREACTION: {updated_vars}")

                if updated_vars and isinstance(updated_vars, dict):
                    variables = updated_vars
                    if 'template_key' in updated_vars:
                        new_template_key = updated_vars.pop('template_key')
                        if new_template_key:
                            template_key = new_template_key
                            print(f"PreAction {preaction} изменил template_key на {template_key}")
                            logger.debug(f"PreAction {preaction} changed template_key to {template_key}")

            template_data = await MessageTemplates.generate_screen(
                user=user,
                state_keys=template_key,
                variables=variables
            )

            if not template_data:
                return None

            text, media_id, keyboard, parse_mode, disable_preview, _, postaction = template_data

            if override_media_id:
                media_id = override_media_id

            return text, media_id, keyboard, parse_mode, disable_preview, preaction, postaction

        except Exception as e:
            logger.error(f"Error preparing template: {e}", exc_info=True)
            return None

    async def _get_first_template_and_preaction(
            self,
            user,
            template_key: Union[str, List[str]]
    ) -> Tuple[Optional[Dict], Optional[str]]:
        """
        Get first template and its preAction.

        Args:
            user: User object
            template_key: Template key or list of keys

        Returns:
            Tuple of (template, preaction)
        """
        first_template = None

        if isinstance(template_key, list):
            if template_key:
                first_key = template_key[0]
                first_template = await MessageTemplates.get_template(first_key, user.lang)
        else:
            first_template = await MessageTemplates.get_template(template_key, user.lang)

        preaction = first_template.get('preAction', '') if first_template else ''
        return first_template, preaction

    async def _execute_postaction(
            self,
            callback_query: CallbackQuery,
            user,
            template_key: str,
            variables: Optional[Dict[str, Any]] = None
    ) -> Optional[str]:
        """
        Execute postAction for callback query.

        Args:
            callback_query: Callback query
            user: User object
            template_key: Current template key
            variables: Optional context variables

        Returns:
            Optional[str]: Next template key or None
        """
        try:
            template_data = await self._prepare_template(
                user=user,
                template_key=template_key,
                variables=variables,
                execute_preaction=False  # Don't execute preAction again
            )

            if not template_data:
                logger.error(f"Failed to prepare template for postAction: {template_key}")
                return None

            _, _, _, _, _, _, postaction = template_data

            if postaction:
                context_vars = variables or {}

                logger.info(f"Executing postAction: {postaction} with callback_data: {callback_query.data}")

                next_state = await MessageTemplates.execute_postaction(
                    postaction, user, context_vars, callback_query.data
                )

                logger.info(f"PostAction result: {next_state or 'None'}")
                return next_state

            return None

        except Exception as e:
            logger.error(f"Error executing postAction: {e}", exc_info=True)
            return None

    async def _send_message(
            self,
            chat_id: int,
            text: str,
            media_id: Optional[str] = None,
            media_type: Optional[str] = None,
            keyboard: Any = None,
            parse_mode: ParseMode = ParseMode.HTML,
            disable_preview: bool = True,
            edit: bool = False,
            message_id: Optional[int] = None
    ) -> Optional[Message]:
        """
        Universal method to send or edit message with or without media.

        Args:
            chat_id: Chat ID
            text: Message text or caption
            media_id: Optional media file ID
            media_type: Media type (photo, video)
            keyboard: Optional inline keyboard
            parse_mode: Parse mode
            disable_preview: Disable web preview
            edit: Whether to edit existing message
            message_id: ID of message to edit

        Returns:
            Optional[Message]: Sent or edited message
        """
        try:
            if media_id:
                return await self._send_media_message(
                    chat_id=chat_id,
                    text=text,
                    media_id=media_id,
                    media_type=media_type or "photo",
                    keyboard=keyboard,
                    parse_mode=parse_mode,
                    edit=edit,
                    message_id=message_id
                )
            else:
                return await self._send_text_message(
                    chat_id=chat_id,
                    text=text,
                    keyboard=keyboard,
                    parse_mode=parse_mode,
                    disable_preview=disable_preview,
                    edit=edit,
                    message_id=message_id
                )
        except Exception as e:
            logger.error(f"Error sending message: {e}")
            return None

    async def _send_text_message(
            self,
            chat_id: int,
            text: str,
            keyboard: Any = None,
            parse_mode: ParseMode = ParseMode.HTML,
            disable_preview: bool = True,
            edit: bool = False,
            message_id: Optional[int] = None
    ) -> Optional[Message]:
        """
        Send or edit text message.

        Args:
            chat_id: Chat ID
            text: Message text
            keyboard: Optional inline keyboard
            parse_mode: Parse mode
            disable_preview: Whether to disable web preview
            edit: Whether to edit existing message
            message_id: ID of message to edit

        Returns:
            Optional[Message]: Sent or edited message
        """
        try:
            kwargs = {
                'chat_id': chat_id,
                'text': text,
                'parse_mode': parse_mode,
                'reply_markup': keyboard,
                'disable_web_page_preview': disable_preview
            }

            if edit and message_id:
                kwargs['message_id'] = message_id
                return await self.bot.edit_message_text(**kwargs)
            else:
                return await self.bot.send_message(**kwargs)

        except TelegramAPIError as e:
            logger.error(f"Error sending text message: {e}")

            if edit and message_id:
                try:
                    kwargs.pop('message_id', None)
                    return await self.bot.send_message(**kwargs)
                except TelegramAPIError as e2:
                    logger.error(f"Error sending fallback message: {e2}")

            return None

    async def _send_media_message(
            self,
            chat_id: int,
            text: str,
            media_id: str,
            media_type: str = "photo",
            keyboard: Any = None,
            parse_mode: ParseMode = ParseMode.HTML,
            edit: bool = False,
            message_id: Optional[int] = None
    ) -> Optional[Message]:
        """
        Send or edit media message.

        Args:
            chat_id: Chat ID
            text: Caption text
            media_id: Media file ID or URL
            media_type: Type of media (photo, video)
            keyboard: Optional inline keyboard
            parse_mode: Parse mode
            edit: Whether to edit existing message
            message_id: ID of message to edit

        Returns:
            Optional[Message]: Sent or edited message
        """
        try:
            kwargs = {
                'chat_id': chat_id,
                'caption': text,
                'parse_mode': parse_mode,
                'reply_markup': keyboard
            }

            if edit and message_id:
                media = self._create_input_media(
                    media_id=media_id,
                    text=text,
                    media_type=media_type,
                    parse_mode=parse_mode
                )

                return await self.bot.edit_message_media(
                    chat_id=chat_id,
                    message_id=message_id,
                    media=media,
                    reply_markup=keyboard
                )

            if media_type.lower() == "video":
                return await self.bot.send_video(
                    **kwargs,
                    video=media_id
                )
            else:  # Default to photo
                return await self.bot.send_photo(
                    **kwargs,
                    photo=media_id
                )

        except TelegramAPIError as e:
            logger.error(f"Error sending media message: {e}")

            try:
                return await self._send_text_message(
                    chat_id=chat_id,
                    text=text,
                    keyboard=keyboard,
                    parse_mode=parse_mode,
                    edit=False  # Always send new message on fallback
                )
            except TelegramAPIError as e2:
                logger.error(f"Error sending fallback text message: {e2}")

            return None

    def _create_input_media(
            self,
            media_id: str,
            text: str,
            media_type: str,
            parse_mode: ParseMode
    ) -> Union[InputMediaPhoto, InputMediaVideo]:
        """
        Create InputMedia object for message editing.

        Args:
            media_id: Media file ID or URL
            text: Caption text
            media_type: Type of media (photo, video)
            parse_mode: Parse mode

        Returns:
            InputMedia object
        """
        if media_type.lower() == "video":
            return InputMediaVideo(
                media=media_id,
                caption=text,
                parse_mode=parse_mode
            )
        else:  # Default to photo
            return InputMediaPhoto(
                media=media_id,
                caption=text,
                parse_mode=parse_mode
            )

    def _extract_message_info(self, update: Union[Message, CallbackQuery]) -> Tuple[int, Optional[int]]:
        """
        Extract chat_id and message_id from update.

        Args:
            update: Message or CallbackQuery

        Returns:
            Tuple of (chat_id, message_id)
        """
        if isinstance(update, CallbackQuery):
            return update.message.chat.id, update.message.message_id
        else:
            return update.chat.id, getattr(update, 'message_id', None)

    def _parse_mode_to_enum(self, parse_mode_str: str) -> ParseMode:
        """
        Convert string parse mode to aiogram ParseMode enum.

        Args:
            parse_mode_str: Parse mode as string

        Returns:
            ParseMode enum value
        """
        parse_mode_str = parse_mode_str.upper()

        if parse_mode_str == "MARKDOWN":
            return ParseMode.MARKDOWN_V2
        elif parse_mode_str == "MARKDOWNV2":
            return ParseMode.MARKDOWN_V2
        else:
            return ParseMode.HTML