"""
Transfer model from mainbot - READ ONLY.
"""
from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
import datetime

from models.mainbot.base import MainbotBase


class Transfer(MainbotBase):
    """Transfer from mainbot database - READ ONLY."""
    __tablename__ = 'transfers'

    transferID = Column(Integer, primary_key=True, autoincrement=True)
    createdAt = Column(DateTime, default=datetime.datetime.utcnow)
    senderUserID = Column(Integer, ForeignKey('users.userID'))
    senderFirstname = Column(String, nullable=False)
    senderSurname = Column(String, nullable=True)
    fromBalance = Column(String, nullable=False)
    amount = Column(Float, nullable=False)
    recieverUserID = Column(Integer, ForeignKey('users.userID'))
    receiverFirstname = Column(String, nullable=False)
    receiverSurname = Column(String, nullable=True)
    toBalance = Column(String, nullable=False)
    status = Column(String, nullable=False)
    notes = Column(Text, nullable=True)

    # Relationships
    sender = relationship('User', foreign_keys=[senderUserID], backref='sent_transfers')
    receiver = relationship('User', foreign_keys=[recieverUserID], backref='received_transfers')

    @property
    def formatted_amount(self):
        """Formatted transfer amount"""
        return f"${self.amount:,.2f}"

    @property
    def balance_flow(self):
        """Balance flow description"""
        return f"{self.fromBalance} → {self.toBalance}"

    @property
    def sender_name(self):
        """Sender full name"""
        parts = [self.senderFirstname]
        if self.senderSurname:
            parts.append(self.senderSurname)
        return ' '.join(parts)

    @property
    def receiver_name(self):
        """Receiver full name"""
        parts = [self.receiverFirstname]
        if self.receiverSurname:
            parts.append(self.receiverSurname)
        return ' '.join(parts)

    @property
    def days_ago(self):
        """Days since transfer"""
        if self.createdAt:
            return (datetime.datetime.utcnow() - self.createdAt).days
        return 0