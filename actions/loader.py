# Файл: actions/loader.py
"""
Action loader stub for HelpBot compatibility.
HelpBot doesn't use actions, but core system expects this module.
"""
import logging
from typing import Dict, Any, Optional

from actions import PRE_ACTION_REGISTRY, POST_ACTION_REGISTRY, initialize_registries

logger = logging.getLogger(__name__)


def load_action(action_type: str, action_name: str) -> Optional[callable]:
    """Stub for loading actions - always returns None."""
    logger.debug(f"load_action called for {action_type}Action '{action_name}' (stub)")
    return None


def get_action_metadata(action_type: str, action_name: str) -> Optional[Dict[str, Any]]:
    """Stub for getting action metadata - always returns None."""
    logger.debug(f"get_action_metadata called for {action_type}Action '{action_name}' (stub)")
    return None


async def execute_preaction(name: str, user, context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Stub for executing preAction - always returns original context.

    Args:
        name: PreAction name
        user: User object
        context: Variables context

    Returns:
        Original context unchanged
    """
    if name:
        logger.debug(f"execute_preaction called for '{name}' (stub)")
    return context


async def execute_postaction(name: str, user, context: Dict[str, Any], callback_data: Optional[str] = None) -> Optional[
    str]:
    """
    Stub for executing postAction - always returns None.

    Args:
        name: PostAction name
        user: User object
        context: Variables context
        callback_data: Optional callback data

    Returns:
        None (no state transition)
    """
    if name:
        logger.debug(f"execute_postaction called for '{name}' with callback_data: {callback_data} (stub)")
    return None


def initialize_actions() -> None:
    """
    Initialize action registries (empty for HelpBot).
    """
    try:
        initialize_registries()
        logger.info("Action registries initialized (empty for HelpBot)")
    except Exception as e:
        logger.error(f"Error initializing action registries: {e}", exc_info=True)