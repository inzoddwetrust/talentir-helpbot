"""
User model for helpbot - supports both clients and staff.
"""
from sqlalchemy import Column, Integer, String, Text, DateTime, Enum, Boolean
import datetime
import json
import enum

from models.base import Base


class UserType(enum.Enum):
    """User types in helpbot"""
    CLIENT = "client"
    OPERATOR = "operator"
    ADMIN = "admin"


class User(Base):
    """User model for helpbot."""
    __tablename__ = 'users'

    userID = Column(Integer, primary_key=True)
    createdAt = Column(DateTime, default=datetime.datetime.utcnow)
    telegramID = Column(Integer, unique=True, nullable=False)

    # User type - determines permissions
    user_type = Column(Enum(UserType), default=UserType.CLIENT, nullable=False)

    # Link to mainbot user (for clients only)
    mainbot_user_id = Column(Integer, nullable=True)

    # Basic info
    lang = Column(String, default="en")
    nickname = Column(String)
    firstname = Column(String, nullable=True)
    lastname = Column(String, nullable=True)

    # Status and activity
    status = Column(String, default="active")  # active, blocked, inactive
    lastActive = Column(DateTime, nullable=True)

    # For operators/admins
    isOnline = Column(Boolean, default=False)

    # JSON fields for flexibility
    notes = Column(Text, nullable=True)
    settings = Column(Text, nullable=True)  # JSON with user preferences
    permissions = Column(Text, nullable=True)  # JSON with specific permissions for staff

    # FSM state (from original bot)
    stateFSM = Column(String, nullable=True)

    @property
    def displayName(self):
        """Returns display name for user"""
        if self.firstname:
            name = self.firstname
            if self.lastname:
                name += f" {self.lastname}"
            return name
        return self.nickname or f"User {self.telegramID}"

    @property
    def isStaff(self):
        """Check if user is staff member"""
        return self.user_type in [UserType.OPERATOR, UserType.ADMIN]

    def get_permissions(self):
        """Get staff permissions as dict"""
        if not self.permissions or not self.isStaff:
            return {}
        try:
            return json.loads(self.permissions)
        except json.JSONDecodeError:
            return {}

    # FSM methods
    def get_fsm_state(self):
        """Gets current FSM state."""
        if not self.stateFSM:
            return None
        try:
            fsm_data = json.loads(self.stateFSM)
            return fsm_data.get("state")
        except json.JSONDecodeError:
            return None

    def set_fsm_state(self, state, context=None):
        """
        Sets FSM state with optional context.

        Args:
            state: State name
            context: Optional context dict for this state
        """
        fsm_data = {
            "state": state,
            "context": context or {}
        }
        self.stateFSM = json.dumps(fsm_data)

    def get_fsm_context(self):
        """Gets FSM context dictionary."""
        if not self.stateFSM:
            return {}
        try:
            fsm_data = json.loads(self.stateFSM)
            return fsm_data.get("context", {})
        except json.JSONDecodeError:
            return {}

    def get_fsm_data(self):
        """
        Legacy method for backward compatibility.
        Returns FSM context.
        """
        return self.get_fsm_context()

    def clear_fsm(self):
        """Clears FSM state."""
        self.stateFSM = None