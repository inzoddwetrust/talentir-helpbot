"""
Dialogue model adapted for support conversations.
"""
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import relationship
import datetime

from models.base import Base


class Dialogue(Base):
    """Model representing a support dialogue."""
    __tablename__ = 'dialogues'

    dialogueID = Column(String, primary_key=True)
    dialogueType = Column(String, nullable=False, default='support')

    # Ticket reference
    ticketID = Column(Integer, ForeignKey('tickets.ticketID'), nullable=True)

    # Participants
    userID = Column(Integer, ForeignKey('users.userID'), nullable=False)
    operatorID = Column(Integer, ForeignKey('operators.operatorID'), nullable=True)  # ИСПРАВЛЕНО!

    # Telegram specifics
    groupID = Column(Integer, nullable=True)
    threadID = Column(Integer, nullable=True)

    # Status and timing
    status = Column(String, default='active')  # active, closed, archived
    state = Column(String, default='waiting_operator')  # Current dialogue state
    createdAt = Column(DateTime, default=datetime.datetime.utcnow)
    updatedAt = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
    lastActivityTime = Column(DateTime, default=datetime.datetime.utcnow)

    # Closure info
    closedAt = Column(DateTime, nullable=True)
    closedBy = Column(String, nullable=True)  # 'client', 'operator', 'system'
    closeReason = Column(String, nullable=True)

    # Message count for analytics
    messageCount = Column(Integer, default=0)

    # JSON storage for flexibility
    notes = Column(Text, nullable=True)  # JSON with additional data

    # Relationships
    ticket = relationship("Ticket", backref="dialogues")
    user = relationship("User", foreign_keys=[userID])
    operator = relationship("Operator", foreign_keys=[operatorID])