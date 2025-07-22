"""
User model from mainbot - READ ONLY.
"""
from sqlalchemy import Column, Integer, String, Text, Float, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import relationship, backref
import datetime

from models.mainbot.base import MainbotBase


class User(MainbotBase):
    """User from mainbot database - READ ONLY."""
    __tablename__ = 'users'

    userID = Column(Integer, primary_key=True)
    createdAt = Column(DateTime, default=datetime.datetime.utcnow)
    upline = Column(Integer, ForeignKey('users.telegramID'), nullable=True)
    lang = Column(String)
    firstname = Column(String)
    surname = Column(String, nullable=True)
    birthday = Column(DateTime, nullable=True)
    address = Column(String, nullable=True)
    phoneNumber = Column(String, nullable=True)
    country = Column(String, nullable=True)
    passport = Column(String, nullable=True)
    city = Column(String, nullable=True)
    telegramID = Column(Integer, unique=True, nullable=False)
    email = Column(String, nullable=True)
    balanceActive = Column(Float, default=0.00)
    balancePassive = Column(Float, default=0.00)
    isFilled = Column(Boolean, default=False)
    kyc = Column(Boolean, default=False)
    lastActive = Column(DateTime, nullable=True)
    status = Column(String, default="active")
    notes = Column(Text, nullable=True)
    settings = Column(String, nullable=True)

    # Relationships
    referrals = relationship('User', backref=backref('referrer', remote_side=[telegramID]))
    purchases = relationship('Purchase', back_populates='user')
    payments = relationship('Payment', back_populates='user')
    received_bonuses = relationship('Bonus', foreign_keys='Bonus.userID', back_populates='user')
    generated_bonuses = relationship('Bonus', foreign_keys='Bonus.downlineID', back_populates='downline')

    # Properties for operator display
    @property
    def full_name(self):
        """Full name for display"""
        parts = []
        if self.firstname:
            parts.append(self.firstname)
        if self.surname:
            parts.append(self.surname)
        return ' '.join(parts) if parts else f"User {self.userID}"

    @property
    def total_balance(self):
        """Total balance (active + passive)"""
        return (self.balanceActive or 0) + (self.balancePassive or 0)

    @property
    def kyc_status(self):
        """KYC status display"""
        return "✅ Verified" if self.kyc else "❌ Not verified"

    @property
    def profile_completeness(self):
        """Profile completeness percentage"""
        fields = [
            self.firstname, self.surname, self.email,
            self.phoneNumber, self.country, self.city,
            self.birthday, self.address
        ]
        filled = sum(1 for f in fields if f)
        return int((filled / len(fields)) * 100)

    @property
    def days_since_registration(self):
        """Days since registration"""
        if self.createdAt:
            return (datetime.datetime.utcnow() - self.createdAt).days
        return 0

    @property
    def referral_count(self):
        """Number of direct referrals"""
        return len(self.referrals) if self.referrals else 0