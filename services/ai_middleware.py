"""
AI Middleware for dialogue translation using Claude API.
Handles automatic translation between users and operators with different languages.
"""
import logging
import json
from typing import Optional, Dict
from anthropic import AsyncAnthropic
from anthropic.types import MessageParam

from config import Config

logger = logging.getLogger(__name__)


class AIMiddleware:
    """
    Middleware for AI-powered features in dialogues.
    Currently implements translation, will be extended for assist and auto-reply modes.
    """

    def __init__(self):
        """Initialize AI middleware with lazy loading of Claude client."""
        self.claude_client = None
        self.message_store = None  # Placeholder for future Redis integration
        self.rate_limiter = None  # TODO: implement rate limiting

        # Language name mapping for better prompts
        self.lang_names = {
            'ru': 'Russian',
            'en': 'English',
            'es': 'Spanish',
            'de': 'German',
            'fr': 'French',
            'it': 'Italian',
            'pt': 'Portuguese',
            'zh': 'Chinese',
            'ja': 'Japanese',
            'ko': 'Korean',
            'ar': 'Arabic',
            'hi': 'Hindi',
            'tr': 'Turkish',
            'uk': 'Ukrainian',
            'pl': 'Polish',
        }

        logger.info("AIMiddleware initialized")

    async def _get_claude(self) -> AsyncAnthropic:
        if not self.claude_client:
            test_token = Config.get(Config.API_TOKEN)
            logger.info(f"DEBUG: API_TOKEN exists: {bool(test_token)}")

            api_key = Config.get(Config.CLAUDE_API_KEY)
            logger.info(f"DEBUG: CLAUDE_API_KEY = {api_key[:10] if api_key else 'NOT SET'}")

            import os
            env_key = os.getenv('CLAUDE_API_KEY')
            logger.info(f"DEBUG: Direct env CLAUDE_API_KEY = {env_key[:10] if env_key else 'NOT SET'}")

            if not api_key:
                raise ValueError("CLAUDE_API_KEY not configured")

            self.claude_client = AsyncAnthropic(
                api_key=api_key
            )
            logger.info("Claude client initialized successfully")

        return self.claude_client  # И ЭТУ ТОЖЕ! ⬇️

    async def _translate(self, text: str, source_lang: str, target_lang: str) -> Optional[str]:
        try:
            claude = await self._get_claude()
            target_name = self.lang_names.get(target_lang, target_lang)
            prompt_template = Config.get(Config.TRANSLATION_PROMPT)

            if not prompt_template:
                prompt_template = """You are a translation service. Your task is to ensure the message is in {target_name}.

    Instructions:
    1. If the message is already in {target_name}, output it unchanged
    2. If the message is in any other language, translate it to {target_name}
    3. Output ONLY the final text in {target_name}, no explanations

    Message: {text}

    Output in {target_name}:"""

            # Форматируем промпт с переменными
            prompt = prompt_template.format(
                target_name=target_name,
                text=text
            )

            logger.debug(f"Translating to {target_lang}: {text[:50]}...")

            response = await claude.messages.create(
                model=Config.get(Config.CLAUDE_MODEL, "claude-3-5-sonnet-20241022"),
                max_tokens=int(Config.get(Config.CLAUDE_MAX_TOKENS, "1000")),
                messages=[{"role": "user", "content": prompt}],
                timeout=float(Config.get(Config.CLAUDE_TIMEOUT, "30"))
            )

            translated = response.content[0].text
            logger.debug(f"Translation successful: {translated[:50]}...")

            return translated

        except Exception as e:
            logger.error(f"Translation failed: {e}", exc_info=True)
            return None

    async def process_dialogue_message(
            self,
            text: str,
            source_lang: str,
            target_lang: str,
            direction: str,  # 'client_to_operator' or 'operator_to_client'
            dialogue_id: str
    ) -> Dict[str, str]:
        """
        Main method for processing dialogue messages with translation.

        Args:
            text: Message text to process
            source_lang: Source language code
            target_lang: Target language code
            direction: Message direction ('client_to_operator' or 'operator_to_client')
            dialogue_id: Dialogue ID for logging

        Returns:
            Dict with keys:
                - 'display': How to display ('both', 'translation_only', or original text)
                - 'original': Original text (if display='both')
                - 'translated': Translated text (if translation successful)
                - 'translation_failed': True if translation failed
        """
        logger.info(f"Processing message for dialogue {dialogue_id}, direction: {direction}, "
                    f"langs: {source_lang}->{target_lang}")

        # Log original message (placeholder for Redis)
        if self.message_store:
            await self.message_store.save_message(
                dialogue_id, text, f'{direction}_original'
            )

        # If languages match - no translation needed
        if source_lang == target_lang:
            logger.debug(f"Languages match ({source_lang}), no translation needed")
            return {'display': text}

        # Translate
        translated = await self._translate(text, source_lang, target_lang)

        if not translated:
            logger.warning(f"Translation failed for dialogue {dialogue_id}")
            return {'display': text, 'translation_failed': True}

        # Log translated message (placeholder for Redis)
        if self.message_store:
            await self.message_store.save_message(
                dialogue_id, translated, f'{direction}_translated'
            )

        # Return based on direction
        if direction == 'client_to_operator':
            # Operator sees both original and translation
            return {
                'display': 'both',
                'original': text,
                'translated': translated
            }
        else:  # operator_to_client
            # Client sees only translation
            return {
                'display': 'translation_only',
                'translated': translated
            }