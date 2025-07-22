"""
Support ticket model.
"""
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Boolean, Enum
from sqlalchemy.orm import relationship
import datetime
import enum

from models.base import Base


class TicketStatus(enum.Enum):
    """Ticket statuses"""
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    WAITING_CLIENT = "waiting_client"
    WAITING_OPERATOR = "waiting_operator"
    RESOLVED = "resolved"
    CLOSED = "closed"
    SPAM = "spam"


class TicketPriority(enum.Enum):
    """Ticket priority levels"""
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class Ticket(Base):
    """Support ticket model."""
    __tablename__ = 'tickets'

    ticketID = Column(Integer, primary_key=True)
    createdAt = Column(DateTime, default=datetime.datetime.utcnow)
    updatedAt = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    # Client info
    userID = Column(Integer, ForeignKey('users.userID'))
    mainbot_user_id = Column(Integer, nullable=True)

    # Ticket metadata
    status = Column(Enum(TicketStatus), default=TicketStatus.OPEN)
    priority = Column(Enum(TicketPriority), default=TicketPriority.NORMAL)

    # Issue details
    category = Column(String, nullable=True)  # payment, kyc, technical, other
    subject = Column(String, nullable=True)
    description = Column(Text, nullable=True)

    # From start payload
    error_code = Column(String, nullable=True)
    context = Column(Text, nullable=True)  # JSON with additional context

    # Assignment - ИСПРАВЛЕНО!
    assignedOperatorID = Column(Integer, ForeignKey('operators.operatorID'), nullable=True)
    assignedAt = Column(DateTime, nullable=True)

    # Resolution
    resolvedAt = Column(DateTime, nullable=True)
    resolutionTime = Column(Integer, nullable=True)  # in minutes
    resolution = Column(Text, nullable=True)

    # Dialogue info
    dialogueID = Column(String, nullable=True)

    # Feedback
    clientSatisfaction = Column(Integer, nullable=True)  # 1-5 stars
    clientFeedback = Column(Text, nullable=True)

    # Relationships
    user = relationship("User", foreign_keys=[userID])
    operator = relationship("Operator", foreign_keys=[assignedOperatorID])