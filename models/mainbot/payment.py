"""
Payment model from mainbot - READ ONLY.
"""
from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey
from sqlalchemy.orm import relationship
import datetime

from models.mainbot.base import MainbotBase


class Payment(MainbotBase):
    """Payment from mainbot database - READ ONLY."""
    __tablename__ = 'payments'

    paymentID = Column(Integer, primary_key=True, autoincrement=True)
    createdAt = Column(DateTime, default=datetime.datetime.utcnow)
    userID = Column(Integer, ForeignKey('users.userID'))
    firstname = Column(String, nullable=False)
    surname = Column(String, nullable=True)
    direction = Column(String, nullable=False, default='incoming')
    amount = Column(Float, nullable=False)
    method = Column(String, nullable=False)
    fromWallet = Column(String, nullable=True)
    toWallet = Column(String, nullable=True)
    txid = Column(String, unique=True, nullable=True)
    sumCurrency = Column(Float, nullable=False)
    status = Column(String, nullable=False)
    confirmedBy = Column(String, nullable=True)
    confirmationTime = Column(DateTime, nullable=True)

    # Relationships
    user = relationship('User', back_populates='payments')

    @property
    def status_emoji(self):
        """Status with emoji for display"""
        status_map = {
            'completed': '✅',
            'pending': '⏳',
            'failed': '❌',
            'cancelled': '🚫'
        }
        return f"{status_map.get(self.status, '❓')} {self.status}"

    @property
    def direction_arrow(self):
        """Direction arrow for display"""
        return '⬇️' if self.direction == 'incoming' else '⬆️'

    @property
    def formatted_amount(self):
        """Formatted amount with direction"""
        sign = '+' if self.direction == 'incoming' else '-'
        return f"{sign}${self.amount:,.2f}"

    @property
    def days_ago(self):
        """Days since payment"""
        if self.createdAt:
            return (datetime.datetime.utcnow() - self.createdAt).days
        return 0