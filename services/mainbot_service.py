"""
Service for read-only access to mainbot database.
Provides convenient methods for retrieving user data for support operators.
"""
import logging
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta
from sqlalchemy import desc, and_, or_

from core.db import get_mainbot_session
from models.mainbot import (
    User as MainbotUser,
    Purchase, Payment, Bonus,
    ActiveBalance, PassiveBalance, Transfer
)

logger = logging.getLogger(__name__)


class MainbotService:
    """Service for retrieving data from mainbot database."""

    @staticmethod
    async def get_user_by_telegram_id(telegram_id: int) -> Optional[MainbotUser]:
        """
        Get user from mainbot by telegram ID.

        Args:
            telegram_id: Telegram user ID

        Returns:
            MainbotUser object or None
        """
        try:
            with get_mainbot_session() as session:
                user = session.query(MainbotUser).filter_by(
                    telegramID=telegram_id
                ).first()

                if user:
                    # Detach from session to use outside context
                    session.expunge(user)

                return user

        except Exception as e:
            logger.error(f"Error getting mainbot user {telegram_id}: {e}")
            return None

    @staticmethod
    async def get_user_summary(telegram_id: int) -> Dict[str, Any]:
        """
        Get comprehensive user summary for operator display.

        Args:
            telegram_id: Telegram user ID

        Returns:
            Dictionary with user summary data
        """
        try:
            with get_mainbot_session() as session:
                user = session.query(MainbotUser).filter_by(
                    telegramID=telegram_id
                ).first()

                if not user:
                    return None

                # Basic info
                summary = {
                    'user_id': user.userID,
                    'telegram_id': user.telegramID,
                    'full_name': user.full_name,
                    'email': user.email,
                    'phone': user.phoneNumber,
                    'country': user.country,
                    'city': user.city,
                    'lang': user.lang,
                    'status': user.status,
                    'kyc_status': user.kyc_status,
                    'profile_completeness': user.profile_completeness,
                    'days_since_registration': user.days_since_registration,
                    'last_active': user.lastActive,
                    'created_at': user.createdAt,

                    # Balances
                    'balance_active': user.balanceActive or 0,
                    'balance_passive': user.balancePassive or 0,
                    'balance_total': user.total_balance,

                    # Referral info
                    'upline_telegram_id': user.upline,
                    'referral_count': len(user.referrals) if user.referrals else 0,

                    # Counts
                    'total_purchases': session.query(Purchase).filter_by(
                        userID=user.userID
                    ).count(),
                    'total_payments': session.query(Payment).filter_by(
                        userID=user.userID
                    ).count(),
                    'total_bonuses': session.query(Bonus).filter_by(
                        userID=user.userID
                    ).count(),
                }

                # Get upline info if exists
                if user.upline:
                    upline = session.query(MainbotUser).filter_by(
                        telegramID=user.upline
                    ).first()
                    if upline:
                        summary['upline_name'] = upline.full_name
                        summary['upline_user_id'] = upline.userID

                return summary

        except Exception as e:
            logger.error(f"Error getting user summary for {telegram_id}: {e}")
            return None

    @staticmethod
    async def get_user_purchases(user_id: int, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get user's recent purchases.

        Args:
            user_id: Mainbot user ID
            limit: Maximum number of purchases to return

        Returns:
            List of purchase dictionaries
        """
        try:
            with get_mainbot_session() as session:
                purchases = session.query(Purchase).filter_by(
                    userID=user_id
                ).order_by(
                    desc(Purchase.createdAt)
                ).limit(limit).all()

                return [
                    {
                        'purchase_id': p.purchaseID,
                        'created_at': p.createdAt,
                        'project_name': p.projectName,
                        'pack_qty': p.packQty,
                        'pack_price': p.packPrice,
                        'formatted_price': p.formatted_price,
                        'days_ago': p.days_ago,
                        'description': p.description
                    }
                    for p in purchases
                ]

        except Exception as e:
            logger.error(f"Error getting purchases for user {user_id}: {e}")
            return []

    @staticmethod
    async def get_user_payments(user_id: int, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get user's recent payments.

        Args:
            user_id: Mainbot user ID
            limit: Maximum number of payments to return

        Returns:
            List of payment dictionaries
        """
        try:
            with get_mainbot_session() as session:
                payments = session.query(Payment).filter_by(
                    userID=user_id
                ).order_by(
                    desc(Payment.createdAt)
                ).limit(limit).all()

                return [
                    {
                        'payment_id': p.paymentID,
                        'created_at': p.createdAt,
                        'direction': p.direction,
                        'direction_arrow': p.direction_arrow,
                        'amount': p.amount,
                        'formatted_amount': p.formatted_amount,
                        'method': p.method,
                        'status': p.status,
                        'status_emoji': p.status_emoji,
                        'txid': p.txid,
                        'days_ago': p.days_ago
                    }
                    for p in payments
                ]

        except Exception as e:
            logger.error(f"Error getting payments for user {user_id}: {e}")
            return []

    @staticmethod
    async def get_user_bonuses(user_id: int, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get user's recent bonuses.

        Args:
            user_id: Mainbot user ID
            limit: Maximum number of bonuses to return

        Returns:
            List of bonus dictionaries
        """
        try:
            with get_mainbot_session() as session:
                bonuses = session.query(Bonus).filter_by(
                    userID=user_id
                ).order_by(
                    desc(Bonus.createdAt)
                ).limit(limit).all()

                result = []
                for b in bonuses:
                    bonus_dict = {
                        'bonus_id': b.bonusID,
                        'created_at': b.createdAt,
                        'type': b.bonus_type,
                        'amount': b.bonusAmount,
                        'formatted_amount': b.formatted_amount,
                        'rate': b.bonusRate,
                        'formatted_rate': b.formatted_rate,
                        'status': b.status,
                        'status_display': b.status_display,
                        'from_purchase_id': b.purchaseID
                    }

                    # Get downline info if referral bonus
                    if b.downlineID:
                        downline = session.query(MainbotUser).filter_by(
                            userID=b.downlineID
                        ).first()
                        if downline:
                            bonus_dict['from_user'] = downline.full_name
                            bonus_dict['from_telegram_id'] = downline.telegramID

                    result.append(bonus_dict)

                return result

        except Exception as e:
            logger.error(f"Error getting bonuses for user {user_id}: {e}")
            return []

    @staticmethod
    async def get_user_balance_history(user_id: int, limit: int = 20) -> List[Dict[str, Any]]:
        """
        Get combined balance history (active + passive).

        Args:
            user_id: Mainbot user ID
            limit: Maximum number of records to return

        Returns:
            List of balance operation dictionaries sorted by date
        """
        try:
            with get_mainbot_session() as session:
                # Get active balance records
                active_records = session.query(ActiveBalance).filter_by(
                    userID=user_id
                ).order_by(desc(ActiveBalance.createdAt)).limit(limit).all()

                # Get passive balance records
                passive_records = session.query(PassiveBalance).filter_by(
                    userID=user_id
                ).order_by(desc(PassiveBalance.createdAt)).limit(limit).all()

                # Combine and format
                history = []

                for r in active_records:
                    history.append({
                        'created_at': r.createdAt,
                        'balance_type': 'active',
                        'amount': r.amount,
                        'formatted_amount': r.formatted_amount,
                        'reason': r.reason,
                        'status': r.status,
                        'days_ago': r.days_ago
                    })

                for r in passive_records:
                    history.append({
                        'created_at': r.createdAt,
                        'balance_type': 'passive',
                        'amount': r.amount,
                        'formatted_amount': r.formatted_amount,
                        'reason': r.reason,
                        'status': r.status,
                        'days_ago': r.days_ago
                    })

                # Sort by date descending
                history.sort(key=lambda x: x['created_at'], reverse=True)

                return history[:limit]

        except Exception as e:
            logger.error(f"Error getting balance history for user {user_id}: {e}")
            return []

    @staticmethod
    async def get_user_transfers(user_id: int, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get user's transfers (sent and received).

        Args:
            user_id: Mainbot user ID
            limit: Maximum number of transfers to return

        Returns:
            List of transfer dictionaries
        """
        try:
            with get_mainbot_session() as session:
                # Get transfers where user is sender or receiver
                transfers = session.query(Transfer).filter(
                    or_(
                        Transfer.senderUserID == user_id,
                        Transfer.recieverUserID == user_id
                    )
                ).order_by(
                    desc(Transfer.createdAt)
                ).limit(limit).all()

                result = []
                for t in transfers:
                    is_sender = t.senderUserID == user_id

                    result.append({
                        'transfer_id': t.transferID,
                        'created_at': t.createdAt,
                        'direction': 'sent' if is_sender else 'received',
                        'amount': t.amount,
                        'formatted_amount': t.formatted_amount,
                        'balance_flow': t.balance_flow,
                        'counterparty_name': t.receiver_name if is_sender else t.sender_name,
                        'counterparty_id': t.recieverUserID if is_sender else t.senderUserID,
                        'status': t.status,
                        'days_ago': t.days_ago
                    })

                return result

        except Exception as e:
            logger.error(f"Error getting transfers for user {user_id}: {e}")
            return []

    @staticmethod
    async def get_recent_activity(user_id: int, days: int = 7) -> Dict[str, Any]:
        """
        Get user's recent activity summary.

        Args:
            user_id: Mainbot user ID
            days: Number of days to look back

        Returns:
            Dictionary with activity summary
        """
        try:
            with get_mainbot_session() as session:
                since_date = datetime.utcnow() - timedelta(days=days)

                # Count recent activities
                activity = {
                    'purchases': session.query(Purchase).filter(
                        and_(
                            Purchase.userID == user_id,
                            Purchase.createdAt >= since_date
                        )
                    ).count(),

                    'payments': session.query(Payment).filter(
                        and_(
                            Payment.userID == user_id,
                            Payment.createdAt >= since_date
                        )
                    ).count(),

                    'bonuses': session.query(Bonus).filter(
                        and_(
                            Bonus.userID == user_id,
                            Bonus.createdAt >= since_date
                        )
                    ).count(),

                    'transfers_sent': session.query(Transfer).filter(
                        and_(
                            Transfer.senderUserID == user_id,
                            Transfer.createdAt >= since_date
                        )
                    ).count(),

                    'transfers_received': session.query(Transfer).filter(
                        and_(
                            Transfer.recieverUserID == user_id,
                            Transfer.createdAt >= since_date
                        )
                    ).count(),
                }

                # Get total amounts
                recent_payment_sum = session.query(
                    Payment.amount
                ).filter(
                    and_(
                        Payment.userID == user_id,
                        Payment.createdAt >= since_date,
                        Payment.direction == 'incoming',
                        Payment.status == 'completed'
                    )
                ).scalar() or 0

                activity['total_deposited'] = recent_payment_sum

                return activity

        except Exception as e:
            logger.error(f"Error getting recent activity for user {user_id}: {e}")
            return {}

    @staticmethod
    async def search_payment_by_txid(txid: str) -> Optional[Dict[str, Any]]:
        """
        Search for payment by transaction ID.

        Args:
            txid: Transaction ID

        Returns:
            Payment info or None
        """
        try:
            with get_mainbot_session() as session:
                payment = session.query(Payment).filter_by(txid=txid).first()

                if payment:
                    user = session.query(MainbotUser).filter_by(
                        userID=payment.userID
                    ).first()

                    return {
                        'payment_id': payment.paymentID,
                        'user_id': payment.userID,
                        'user_name': user.full_name if user else 'Unknown',
                        'user_telegram_id': user.telegramID if user else None,
                        'amount': payment.amount,
                        'formatted_amount': payment.formatted_amount,
                        'method': payment.method,
                        'status': payment.status,
                        'created_at': payment.createdAt
                    }

                return None

        except Exception as e:
            logger.error(f"Error searching payment by txid {txid}: {e}")
            return None