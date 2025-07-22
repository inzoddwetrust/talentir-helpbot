"""
Balance models from mainbot - READ ONLY.
"""
from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
import datetime

from models.mainbot.base import MainbotBase


class ActiveBalance(MainbotBase):
    """Active balance records from mainbot - READ ONLY."""
    __tablename__ = 'active_balance'

    paymentID = Column(Integer, primary_key=True, autoincrement=True)
    createdAt = Column(DateTime, default=datetime.datetime.utcnow)
    userID = Column(Integer, ForeignKey('users.userID'))
    firstname = Column(String, nullable=False)
    surname = Column(String, nullable=True)
    amount = Column(Float, nullable=False)
    status = Column(String, nullable=False)
    reason = Column(String, nullable=False)
    link = Column(String, nullable=True)
    notes = Column(Text, nullable=True)

    # Relationships
    user = relationship('User', backref='active_balance_records')

    @property
    def formatted_amount(self):
        """Formatted amount with sign"""
        sign = '+' if self.amount > 0 else ''
        return f"{sign}${self.amount:,.2f}"

    @property
    def days_ago(self):
        """Days since transaction"""
        if self.createdAt:
            return (datetime.datetime.utcnow() - self.createdAt).days
        return 0


class PassiveBalance(MainbotBase):
    """Passive balance records from mainbot - READ ONLY."""
    __tablename__ = 'passive_balance'

    paymentID = Column(Integer, primary_key=True, autoincrement=True)
    createdAt = Column(DateTime, default=datetime.datetime.utcnow)
    userID = Column(Integer, ForeignKey('users.userID'))
    firstname = Column(String, nullable=False)
    surname = Column(String, nullable=True)
    amount = Column(Float, nullable=False)
    status = Column(String, nullable=False)
    reason = Column(String, nullable=False)
    link = Column(String, nullable=True)
    notes = Column(Text, nullable=True)

    # Relationships
    user = relationship('User', backref='passive_balance_records')

    @property
    def formatted_amount(self):
        """Formatted amount with sign"""
        sign = '+' if self.amount > 0 else ''
        return f"{sign}${self.amount:,.2f}"

    @property
    def days_ago(self):
        """Days since transaction"""
        if self.createdAt:
            return (datetime.datetime.utcnow() - self.createdAt).days
        return 0