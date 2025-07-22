"""
Actions package stub for HelpBot compatibility.
HelpBot doesn't use actions, but core system expects this module.
"""
import logging
from typing import Dict

logger = logging.getLogger(__name__)

# Empty registries for HelpBot
PRE_ACTION_REGISTRY = {}
POST_ACTION_REGISTRY = {}


def register_preaction(name: str, path: str) -> None:
    """Stub for registering preAction."""
    logger.debug(f"register_preaction called (stub): {name} -> {path}")


def register_postaction(name: str, path: str) -> None:
    """Stub for registering postAction."""
    logger.debug(f"register_postaction called (stub): {name} -> {path}")


def get_registry(action_type: str) -> Dict[str, str]:
    """Get empty action registry."""
    if action_type.lower() == "pre":
        return PRE_ACTION_REGISTRY
    elif action_type.lower() == "post":
        return POST_ACTION_REGISTRY
    else:
        raise ValueError(f"Unknown action type: {action_type}")


def initialize_registries():
    """Initialize empty registries for HelpBot."""
    # HelpBot doesn't use actions, so we don't register anything
    logger.info("Initialized empty action registries for HelpBot")