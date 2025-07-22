"""
Purchase model from mainbot - READ ONLY.
"""
from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey
from sqlalchemy.orm import relationship
import datetime

from models.mainbot.base import MainbotBase


class Purchase(MainbotBase):
    """Purchase from mainbot database - READ ONLY."""
    __tablename__ = 'purchases'

    purchaseID = Column(Integer, primary_key=True, autoincrement=True)
    createdAt = Column(DateTime, default=datetime.datetime.utcnow)
    userID = Column(Integer, ForeignKey('users.userID'))
    projectID = Column(Integer, nullable=True)
    projectName = Column(String, nullable=False)
    optionID = Column(Integer, nullable=True)
    packQty = Column(Integer, nullable=False)
    packPrice = Column(Float, nullable=False)

    # Relationships - только User
    user = relationship('User', back_populates='purchases')

    @property
    def days_ago(self):
        """Days since purchase"""
        if self.createdAt:
            return (datetime.datetime.utcnow() - self.createdAt).days
        return 0

    @property
    def formatted_price(self):
        """Formatted price with currency"""
        return f"${self.packPrice:,.2f}"

    @property
    def description(self):
        """Purchase description for display"""
        return f"{self.projectName} - {self.packQty} units"