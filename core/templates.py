from typing import Optional, Dict, Tuple, List, Union, Any, Callable
import logging
from core.google_services import get_google_services
from config import Config
from core.utils import SafeDict
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from actions.loader import execute_preaction, execute_postaction

logger = logging.getLogger(__name__)


class MessageTemplates:
    """
    Manager for message templates stored in Google Sheets.
    Handles loading, caching, and formatting templates.
    """
    _cache: Dict[Tuple[str, str], Dict] = {}
    _sheet_client = None

    @classmethod
    async def _get_sheet_client(cls):
        """Get and cache client for Google Sheets"""
        if cls._sheet_client is None:
            sheets_client, _ = await get_google_services()
            cls._sheet_client = sheets_client
        return cls._sheet_client

    @staticmethod
    async def load_templates():
        """Load all templates from Google Sheets to memory cache."""
        try:
            sheets_client, _ = await get_google_services()
            spreadsheet = await sheets_client.open_by_key(Config.get(Config.GOOGLE_SHEET_ID))
            sheet = await spreadsheet.worksheet("Templates")
            rows = await sheet.get_all_records()

            new_cache = {
                (row['stateKey'], row['lang']): {
                    'preAction': row.get('preAction', ''),
                    'text': row['text'],
                    'buttons': row['buttons'],
                    'postAction': row.get('postAction', ''),
                    'parseMode': row['parseMode'],
                    'disablePreview': MessageTemplates._parse_boolean(row['disablePreview']),
                    'mediaType': row['mediaType'],
                    'mediaID': row['mediaID']
                } for row in rows
            }

            MessageTemplates._cache = new_cache
            logger.info(f"Loaded {len(rows)} templates from Google Sheets")
        except Exception as e:
            logger.error(f"Error loading templates: {e}")
            raise

    @staticmethod
    def _parse_boolean(value):
        """
        Universal function to convert different types to boolean.
        Replaces repetitive type checking.

        Args:
            value: Value to convert (bool, int, str)

        Returns:
            bool: Converted boolean value
        """
        if isinstance(value, bool):
            return value
        elif isinstance(value, int):
            return value == 1
        elif isinstance(value, str):
            return value.upper() in ("TRUE", "1", "YES")
        return False

    @staticmethod
    async def get_template(state_key: str, lang: str = 'en') -> Optional[Dict]:
        """
        Get template by state key and language.
        Falls back to English if the requested language is not available.

        Args:
            state_key: Template identifier
            lang: Language code (default: 'en')

        Returns:
            Template dictionary or None if not found
        """
        if not MessageTemplates._cache:
            await MessageTemplates.load_templates()

        template = MessageTemplates._cache.get((state_key, lang))

        if not template:
            template = MessageTemplates._cache.get((state_key, 'en'))

        return template

    @staticmethod
    async def get_raw_template(state_key: str, variables: dict, lang: str = 'en') -> tuple[str, Optional[str]]:
        """
        Gets raw template without media formatting.
        Used primarily for notifications.

        Args:
            state_key: Template identifier
            variables: Dictionary with variables for substitution
            lang: Language code (default: 'en')

        Returns:
            tuple[str, Optional[str]]: (formatted text, formatted buttons in JSON)
        """
        if not MessageTemplates._cache:
            await MessageTemplates.load_templates()

        template = MessageTemplates._cache.get((state_key, lang))
        if not template:
            template = MessageTemplates._cache.get((state_key, 'en'))
            if not template:
                logger.error(
                    f"Template not found in cache: {state_key}. Cache keys: {list(MessageTemplates._cache.keys())}")  # Добавить эту строку
                raise ValueError(f"Template not found: {state_key}")

        text = template['text'].replace('\\n', '\n')
        buttons = template['buttons']

        if 'rgroup' in variables:
            text = MessageTemplates.process_repeating_group(text, variables['rgroup'])
            if buttons:
                buttons = MessageTemplates.process_repeating_group(buttons, variables['rgroup'])

        formatted_text = text.format_map(SafeDict(variables))
        if buttons:
            formatted_buttons = buttons.format_map(SafeDict(variables))
        else:
            formatted_buttons = None

        return formatted_text, formatted_buttons

    @staticmethod
    def sequence_format(template: str, variables: dict, sequence_index: int = 0) -> str:
        """
        Formats string with variables, supporting both scalar and sequence values.
        Also supports accessing object attributes with dot notation.
        For sequence values, uses value at sequence_index or last value if index out of range.

        Args:
            template: Text template with placeholders
            variables: Dictionary with variables for substitution
            sequence_index: Index for sequence variables

        Returns:
            Formatted string
        """
        formatted_vars = {}

        for key, value in variables.items():
            if isinstance(value, (list, tuple)):
                try:
                    formatted_vars[key] = value[min(sequence_index, len(value) - 1)]
                except (IndexError, ValueError):
                    continue
            else:
                formatted_vars[key] = value

        return template.format_map(SafeDict(formatted_vars))

    @staticmethod
    def enhanced_sequence_format(template: str, variables: dict, sequence_index: int = 0) -> str:
        """
        Enhanced format function with support for sequence variables.
        Now just a wrapper around sequence_format that uses SafeDict.

        Args:
            template: Text template with placeholders
            variables: Dictionary with variables for substitution
            sequence_index: Index for sequence variables

        Returns:
            Formatted string
        """
        formatted_vars = {}

        for key, value in variables.items():
            if isinstance(value, (list, tuple)):
                try:
                    formatted_vars[key] = value[min(sequence_index, len(value) - 1)]
                except (IndexError, ValueError):
                    continue
            else:
                formatted_vars[key] = value

        return template.format_map(SafeDict(formatted_vars))

    @staticmethod
    def create_keyboard(buttons_str: str, variables: dict = None) -> Optional[InlineKeyboardMarkup]:
        """
        Creates keyboard object from configuration string with variable support.
        Supports both scalar and sequence variables, applying sequence values in order.
        Now uses enhanced_sequence_format for maximum flexibility.

        Args:
            buttons_str: String defining buttons structure
            variables: Optional dictionary with variables for substitution

        Returns:
            InlineKeyboardMarkup or None if no valid buttons
        """
        if not buttons_str or not buttons_str.strip():
            return None

        try:
            keyboard_buttons = []
            rows = buttons_str.split('\n')
            sequence_index = 0

            for row in rows:
                if not row.strip():
                    continue

                button_row = []
                buttons = row.split(';')

                for button in buttons:
                    button = button.strip()
                    if not button or ':' not in button:
                        continue

                    # Special handling for webapp and url buttons
                    if '|webapp|' in button:
                        # Format: |webapp|http://example.com:Button text
                        webapp_parts = button.split(':', 1)
                        webapp_url_part = webapp_parts[0].strip()
                        button_text = webapp_parts[1].strip() if len(webapp_parts) > 1 else "Open WebApp"

                        if webapp_url_part.startswith('|webapp|'):
                            url = webapp_url_part[8:]

                            if not url.startswith(('http://', 'https://')):
                                url = 'https://' + url

                            if variables:
                                try:
                                    button_text = button_text.format_map(SafeDict(variables))
                                    if '{}' in url or '{' in url:
                                        url = url.format_map(SafeDict(variables))
                                except Exception as e:
                                    logger.error(f"Error formatting webapp button: {e}")
                                    continue

                            try:
                                button_row.append(
                                    InlineKeyboardButton(
                                        text=button_text,
                                        web_app=WebAppInfo(url=url)
                                    )
                                )
                                continue
                            except Exception as e:
                                logger.error(f"Error creating webapp button: {e}")

                    elif '|url|' in button:
                        # Format: |url|example.com:Button text
                        url_parts = button.split(':', 1)
                        url_part = url_parts[0].strip()
                        button_text = url_parts[1].strip() if len(url_parts) > 1 else "Open URL"

                        if url_part.startswith('|url|'):
                            url = url_part[5:]

                            if not url.startswith(('http://', 'https://')):
                                url = 'http://' + url

                            if variables:
                                try:
                                    button_text = button_text.format_map(SafeDict(variables))
                                    if '{}' in url or '{' in url:
                                        url = url.format_map(SafeDict(variables))
                                except Exception as e:
                                    logger.error(f"Error formatting url button: {e}")
                                    continue

                            try:
                                # Create URL button (aiogram 3.x style)
                                button_row.append(
                                    InlineKeyboardButton(
                                        text=button_text,
                                        url=url
                                    )
                                )
                                continue  # Skip to next button
                            except Exception as e:
                                logger.error(f"Error creating url button: {e}")

                    # Standard callback buttons
                    callback, text = button.split(':', 1)
                    callback, text = callback.strip(), text.strip()

                    # Format both callback and text with variables if provided
                    if variables:
                        try:
                            # Используем enhanced_sequence_format для максимальной гибкости
                            text = MessageTemplates.enhanced_sequence_format(
                                text, variables, sequence_index
                            )
                            callback = MessageTemplates.enhanced_sequence_format(
                                callback, variables, sequence_index
                            )
                            sequence_index += 1
                        except Exception as e:
                            logger.error(f"Error formatting callback button: {e}")
                            continue

                    try:
                        button_row.append(
                            InlineKeyboardButton(
                                text=text,
                                callback_data=callback
                            )
                        )
                    except Exception as e:
                        logger.error(f"Error creating callback button: {e}")

                if button_row:
                    keyboard_buttons.append(button_row)

            # В aiogram 3.x структура клавиатуры отличается
            return InlineKeyboardMarkup(inline_keyboard=keyboard_buttons) if keyboard_buttons else None

        except Exception as e:
            logger.error(f"Error creating keyboard: {e}")
            return None

    @staticmethod
    async def execute_preaction(preaction_name: str, user, context: dict) -> dict:
        """
        Executes preAction function before sending a message.
        Supports standardized preactions created with @preaction decorator.
        """
        if not preaction_name:
            return context

        try:
            # Используем loader из actions
            result = await execute_preaction(preaction_name, user, context)
            return result

        except Exception as e:
            logger.error(f"Error executing preAction '{preaction_name}': {str(e)}")
            return context

    @staticmethod
    async def execute_postaction(postaction_name: str, user, context: dict, callback_data: Optional[str] = None) -> \
            Optional[str]:
        """
        Executes postAction function after user interaction.
        """
        if not postaction_name:
            return None

        try:
            # Используем loader из actions
            result = await execute_postaction(postaction_name, user, context, callback_data)
            return result

        except Exception as e:
            logger.error(f"Error executing postAction '{postaction_name}': {str(e)}", exc_info=True)
            return None

    @staticmethod
    def merge_buttons(buttons_list: List[str]) -> str:
        """
        Merges multiple button configurations into a single string.

        Args:
            buttons_list: List of button configuration strings

        Returns:
            Merged button configuration string
        """
        valid_configs = [b.strip() for b in buttons_list if b and b.strip()]
        if not valid_configs:
            return ''

        # Collect all rows from each configuration
        all_rows = []

        for config in valid_configs:
            # Split by newlines
            rows = config.split('\n')

            # Add each non-empty row
            for row in rows:
                if row.strip():
                    all_rows.append(row.strip())

        # Join all rows with newlines
        return '\n'.join(all_rows)

    @staticmethod
    def process_repeating_group(template_text: str, rgroup_data: Dict[str, List[Any]]) -> str:
        """
        Processes repeating group in template text.

        Args:
            template_text: Text template with repeating group placeholder
            rgroup_data: Dictionary with data for repeating groups

        Returns:
            Processed text with repeating groups expanded
        """
        start = template_text.find('|rgroup:')
        if start == -1:
            return template_text

        end = template_text.find('|', start + 8)
        if end == -1:
            return template_text

        item_template = template_text[start + 8:end]
        full_template = template_text[start:end + 1]

        if not rgroup_data or not all(rgroup_data.values()):
            return template_text.replace(full_template, '')

        # Check that all arrays in rgroup_data have the same length
        lengths = {len(arr) for arr in rgroup_data.values()}
        if len(lengths) != 1:
            logger.warning(f"Inconsistent lengths in rgroup data: {lengths}")
            return template_text.replace(full_template, '')

        result = []
        for i in range(next(iter(lengths))):
            item_data = {key: values[i] for key, values in rgroup_data.items()}
            # Используем SafeDict для обратной совместимости с rgroup
            result.append(item_template.format_map(SafeDict(item_data)))

        return template_text.replace(full_template, '\n'.join(result))

    @classmethod
    async def generate_screen(
            cls,
            user,
            state_keys: Union[str, List[str]],
            variables: Optional[dict] = None
    ) -> Tuple[str, Optional[str], Optional[InlineKeyboardMarkup], str, bool, Optional[str], Optional[str]]:
        """
        Generates screen content from templates.

        Args:
            user: User object for localization
            state_keys: Template key or list of keys
            variables: Optional dictionary with template variables

        Returns:
            Tuple[str, Optional[str], Optional[InlineKeyboardMarkup], str, bool, Optional[str], Optional[str]]:
                - Formatted text
                - Media ID (if any)
                - Keyboard (if any)
                - Parse mode
                - Disable preview flag
                - preAction name (if any)
                - postAction name (if any)
        """
        if isinstance(state_keys, str):
            state_keys = [state_keys]

        templates = []
        # Load cache if needed
        if not cls._cache:
            await cls.load_templates()

        for key in state_keys:
            template = cls._cache.get((key, user.lang)) or cls._cache.get((key, 'en'))
            if not template:
                logger.warning(f"Template not found for state {key}")
                continue
            templates.append(template)

        if not templates:
            # Try to get fallback template
            fallback = cls._cache.get(('fallback', user.lang)) or cls._cache.get(('fallback', 'en'))
            if fallback:
                templates = [fallback]
            else:
                logger.error("Fallback template not found")
                return "Template not found", None, None, "HTML", True, None, None

        try:
            texts = []
            buttons_list = []
            format_vars = (variables or {}).copy()

            # Добавляем user в контекст для прямого доступа к его атрибутам
            format_vars['user'] = user

            for template in templates:
                text = template['text'].replace('\\n', '\n')

                if 'rgroup' in format_vars:
                    text = cls.process_repeating_group(text, format_vars['rgroup'])

                # Используем SafeDict вместо AdvancedSafeDict
                text = text.format_map(SafeDict(format_vars))
                texts.append(text)

                if template['buttons']:
                    buttons_list.append(template['buttons'])

            final_text = '\n\n'.join(text for text in texts if text)
            merged_buttons = cls.merge_buttons(buttons_list)
            keyboard = cls.create_keyboard(merged_buttons, variables=format_vars)

            first_template = templates[0]
            media_id = first_template['mediaID'] if first_template.get('mediaType') != 'None' else None
            parse_mode = first_template['parseMode']

            disable_preview = first_template['disablePreview']

            pre_action = first_template.get('preAction', '') if first_template.get('preAction') else None
            post_action = first_template.get('postAction', '') if first_template.get('postAction') else None

            return final_text, media_id, keyboard, parse_mode, disable_preview, pre_action, post_action

        except Exception as e:
            logger.error(f"Error generating screen: {str(e)}")
            return f"Error generating screen: {str(e)}", None, None, "HTML", True, None, None
