"""
Configuration and registry for operator commands in helpbot.
"""
from enum import Enum
from typing import Dict, Any, Optional, Callable
from services.dialogue_states import DialogueState


class CommandConfig:
    """Configuration for a single operator command."""

    def __init__(
            self,
            name: str,
            handler: str,
            description: str,
            requires_args: bool = False,
            min_state: Optional[DialogueState] = None,
            allowed_states: Optional[list] = None,
            template_success: Optional[str] = None,
            template_error: Optional[str] = None,
            template_help: Optional[str] = None
    ):
        self.name = name
        self.handler = handler
        self.description = description
        self.requires_args = requires_args
        self.min_state = min_state
        self.allowed_states = allowed_states or []
        self.template_success = template_success
        self.template_error = template_error or '/support/operator_command_error'
        self.template_help = template_help or '/support/operator_command_help'


# Registry of all operator commands
OPERATOR_COMMANDS = {
    '&end': CommandConfig(
        name='&end',
        handler='end_ticket',
        description='Close ticket as resolved',
        requires_args=True,
        min_state=DialogueState.IN_PROGRESS,
        template_success='/support/operator_ticket_resolved',
        template_help='/support/help_end_command'
    ),

    '&spam': CommandConfig(
        name='&spam',
        handler='mark_spam',
        description='Mark ticket as spam',
        requires_args=False,
        allowed_states=[DialogueState.WAITING_OPERATOR, DialogueState.IN_PROGRESS],
        template_success='/support/operator_marked_spam',
        template_help='/support/help_spam_command'
    ),

    '&info': CommandConfig(
        name='&info',
        handler='show_info',
        description='Show detailed user and ticket information',
        requires_args=False,
        min_state=DialogueState.IN_PROGRESS,
        template_success='/support/operator_user_info',
        template_help='/support/help_info_command'
    ),

    '&history': CommandConfig(
        name='&history',
        handler='show_history',
        description='Show user ticket history',
        requires_args=False,
        min_state=DialogueState.IN_PROGRESS,
        template_success='/support/operator_ticket_history',
        template_help='/support/help_history_command'
    ),

    '&help': CommandConfig(
        name='&help',
        handler='show_help',
        description='Show available commands',
        requires_args=False,
        template_success='/support/operator_help',
    ),

    # Easy to add new commands here without touching any other code!
    # '&transfer': CommandConfig(
    #     name='&transfer',
    #     handler='transfer_ticket',
    #     description='Transfer ticket to another operator',
    #     requires_args=True,
    #     min_state=DialogueState.IN_PROGRESS,
    #     template_success='/support/operator_ticket_transferred',
    # ),
}


def get_command_config(command: str) -> Optional[CommandConfig]:
    """Get command configuration by name."""
    return OPERATOR_COMMANDS.get(command.lower())


def get_all_commands() -> Dict[str, CommandConfig]:
    """Get all registered commands."""
    return OPERATOR_COMMANDS


def register_command(command: CommandConfig) -> None:
    """Register a new command (for dynamic registration if needed)."""
    OPERATOR_COMMANDS[command.name.lower()] = command