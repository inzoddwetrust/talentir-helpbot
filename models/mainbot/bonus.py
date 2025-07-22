"""
Bonus model from mainbot - READ ONLY.
"""
from sqlalchemy import Column, Integer, Float, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import relationship
import datetime

from models.mainbot.base import MainbotBase


class Bonus(MainbotBase):
    """Bonus from mainbot database - READ ONLY."""
    __tablename__ = 'bonuses'

    bonusID = Column(Integer, primary_key=True, autoincrement=True)
    createdAt = Column(DateTime, default=datetime.datetime.utcnow)

    userID = Column(Integer, ForeignKey('users.userID'), nullable=False)
    downlineID = Column(Integer, ForeignKey('users.userID'), nullable=True)
    purchaseID = Column(Integer, ForeignKey('purchases.purchaseID'), nullable=True)

    projectID = Column(Integer, nullable=True)
    optionID = Column(Integer, nullable=True)
    packQty = Column(Integer, nullable=True)
    packPrice = Column(Float, nullable=True)

    uplineLevel = Column(Integer, nullable=True)
    bonusRate = Column(Float, nullable=False)
    bonusAmount = Column(Float, nullable=False)

    status = Column(String, default="pending")
    notes = Column(Text, nullable=True)

    # Relationships
    user = relationship('User', foreign_keys=[userID], back_populates='received_bonuses')
    downline = relationship('User', foreign_keys=[downlineID], back_populates='generated_bonuses')
    purchase = relationship('Purchase', backref='bonuses')

    @property
    def status_display(self):
        """Status with emoji"""
        status_map = {
            'pending': '⏳ Pending',
            'processing': '🔄 Processing',
            'paid': '✅ Paid',
            'cancelled': '❌ Cancelled',
            'error': '⚠️ Error'
        }
        return status_map.get(self.status, self.status)

    @property
    def formatted_amount(self):
        """Formatted bonus amount"""
        return f"${self.bonusAmount:,.2f}"

    @property
    def formatted_rate(self):
        """Formatted bonus rate as percentage"""
        return f"{self.bonusRate:.1f}%"

    @property
    def bonus_type(self):
        """Determine bonus type"""
        if self.downlineID:
            return f"Referral Level {self.uplineLevel or 'N/A'}"
        return "System Bonus"