"""
Operator model - extending User for operator-specific features.
"""
from sqlalchemy import Column, Integer, ForeignKey, Boolean, Float, String, Text, BigInteger
from sqlalchemy.orm import relationship

from models.base import Base


class Operator(Base):
    """Extended operator information."""
    __tablename__ = 'operators'

    operatorID = Column(Integer, primary_key=True)
    userID = Column(Integer, ForeignKey('users.userID'), unique=True)

    # Telegram ID for quick reference (denormalized for convenience)
    telegramID = Column(BigInteger, nullable=False)
    displayName = Column(String, nullable=True)  # Cached display name

    # Work schedule
    isActive = Column(Boolean, default=True)
    workingHours = Column(Text, nullable=True)  # JSON with schedule

    # Specializations
    specializations = Column(Text, nullable=True)  # JSON array of specializations
    languages = Column(Text, nullable=True)  # JSON array of languages

    # ДОБАВЛЯЕМ ОПЕРАТОРСКИЕ ПОЛЯ СЮДА:
    maxConcurrentTickets = Column(Integer, default=5)
    currentTicketsCount = Column(Integer, default=0)
    totalTicketsResolved = Column(Integer, default=0)
    avgResolutionTime = Column(Integer, default=0)  # in minutes

    # Performance metrics
    satisfactionRating = Column(Float, default=0.0)
    totalRatings = Column(Integer, default=0)

    # Manager notes
    managerNotes = Column(Text, nullable=True)

    # Relationships
    user = relationship("User")

    def __repr__(self):
        return f"<Operator(ID={self.operatorID}, TG={self.telegramID}, Name='{self.displayName}')>"