"""
Dialogue states for helpbot support system.
"""
from enum import Enum


class DialogueState(Enum):
    """States for support dialogues."""

    # Waiting for operator to take the ticket
    WAITING_OPERATOR = "waiting_operator"

    # Operator took the ticket, dialogue in progress
    IN_PROGRESS = "in_progress"

    # Ticket resolved by operator
    RESOLVED = "resolved"

    # Ticket closed
    CLOSED = "closed"

    # Marked as spam
    SPAM = "spam"

    def __str__(self) -> str:
        """String representation for database storage."""
        return self.value

    @classmethod
    def from_string(cls, state_str: str) -> 'DialogueState':
        """Create enum from string, with fallback to WAITING_OPERATOR."""
        try:
            return cls(state_str.lower())
        except ValueError:
            return cls.WAITING_OPERATOR