"""
Import implementations for specific models.
"""
import logging
from typing import Dict, Any, List

from models.user import User, UserType
from models.operator import Operator
from models.ticket import Ticket, TicketStatus, TicketPriority
from models.dialogue import Dialogue
from services.data_importer import BaseImporter, create_model_importer, DataImportManager
from config import Config

logger = logging.getLogger(__name__)


class UserImporter(BaseImporter):
    def __init__(self):
        super().__init__()
        self.REQUIRED_FIELDS = ["telegramID", "nickname"]

    def process_row(self, row: Dict[str, Any], session) -> bool:
        telegram_id = int(row["telegramID"])
        user = session.query(User).filter_by(telegramID=telegram_id).first()
        is_update = user is not None
        if not user:
            user = User(telegramID=telegram_id)
            session.add(user)

        user.nickname = row["nickname"]
        user.lang = row.get("lang", "en")
        user.status = row.get("status", "active")

        # Handle user_type if present
        if "user_type" in row:
            try:
                user.user_type = UserType(row["user_type"])
            except ValueError:
                logger.warning(f"Invalid user_type: {row['user_type']}")

        return is_update


class OperatorImporter(BaseImporter):
    def __init__(self):
        super().__init__()
        self.REQUIRED_FIELDS = ["telegramID"]

    def process_row(self, row: Dict[str, Any], session) -> bool:
        telegram_id = int(row["telegramID"])

        # Get or create user
        user = session.query(User).filter_by(telegramID=telegram_id).first()
        if not user:
            user = User(
                telegramID=telegram_id,
                nickname=row.get("displayName", f"Operator {telegram_id}"),
                user_type=UserType.OPERATOR,
                lang=row.get("lang", "en"),
                status="active"
            )
            session.add(user)
            session.flush()

        # Get or create operator
        operator = session.query(Operator).filter_by(userID=user.userID).first()
        is_update = operator is not None

        if not operator:
            operator = Operator(
                userID=user.userID,
                telegramID=telegram_id
            )
            session.add(operator)

        # Update operator fields
        operator.isActive = row.get("isActive", "true").lower() in ("true", "yes", "1", "y", "t")
        operator.displayName = row.get("displayName", user.nickname)

        if "workingHours" in row:
            operator.workingHours = row["workingHours"]
        if "specializations" in row:
            operator.specializations = row["specializations"]
        if "languages" in row:
            operator.languages = row["languages"]
        if "managerNotes" in row:
            operator.managerNotes = row["managerNotes"]

        # Update user type to ensure it's operator
        if operator.isActive:
            user.user_type = UserType.OPERATOR

        return is_update


class TicketImporter(BaseImporter):
    def __init__(self):
        super().__init__()
        self.REQUIRED_FIELDS = ["ticketID"]

    def process_row(self, row: Dict[str, Any], session) -> bool:
        ticket_id = int(row["ticketID"])
        ticket = session.query(Ticket).filter_by(ticketID=ticket_id).first()
        is_update = ticket is not None

        if not ticket:
            # For new tickets, we need at least userID
            if "userID" not in row:
                self.stats.add_error(ticket_id, "Missing userID for new ticket")
                return False

            ticket = Ticket(
                ticketID=ticket_id,
                userID=int(row["userID"])
            )
            session.add(ticket)

        # Update fields
        if "status" in row:
            try:
                ticket.status = TicketStatus(row["status"])
            except ValueError:
                logger.warning(f"Invalid status: {row['status']}")

        if "priority" in row:
            try:
                ticket.priority = TicketPriority(row["priority"])
            except ValueError:
                logger.warning(f"Invalid priority: {row['priority']}")

        if "category" in row:
            ticket.category = row["category"]
        if "subject" in row:
            ticket.subject = row["subject"]
        if "description" in row:
            ticket.description = row["description"]
        if "error_code" in row:
            ticket.error_code = row["error_code"]
        if "context" in row:
            ticket.context = row["context"]
        if "resolution" in row:
            ticket.resolution = row["resolution"]
        if "clientFeedback" in row:
            ticket.clientFeedback = row["clientFeedback"]

        # Handle numeric fields
        if "assignedOperatorID" in row and row["assignedOperatorID"]:
            ticket.assignedOperatorID = int(row["assignedOperatorID"])
        if "clientSatisfaction" in row and row["clientSatisfaction"]:
            ticket.clientSatisfaction = int(row["clientSatisfaction"])
        if "resolutionTime" in row and row["resolutionTime"]:
            ticket.resolutionTime = int(row["resolutionTime"])

        return is_update


class DialogueImporter(BaseImporter):
    def __init__(self):
        super().__init__()
        self.REQUIRED_FIELDS = ["dialogueID"]

    def process_row(self, row: Dict[str, Any], session) -> bool:
        dialogue_id = row["dialogueID"]
        dialogue = session.query(Dialogue).filter_by(dialogueID=dialogue_id).first()
        is_update = dialogue is not None

        if not dialogue:
            # For new dialogues, we need at least userID
            if "userID" not in row:
                self.stats.add_error(dialogue_id, "Missing userID for new dialogue")
                return False

            dialogue = Dialogue(
                dialogueID=dialogue_id,
                userID=int(row["userID"]),
                dialogueType=row.get("dialogueType", "support")
            )
            session.add(dialogue)

        # Update fields
        if "ticketID" in row and row["ticketID"]:
            dialogue.ticketID = int(row["ticketID"])
        if "operatorID" in row and row["operatorID"]:
            dialogue.operatorID = int(row["operatorID"])
        if "groupID" in row and row["groupID"]:
            dialogue.groupID = int(row["groupID"])
        if "threadID" in row and row["threadID"]:
            dialogue.threadID = int(row["threadID"])

        if "status" in row:
            dialogue.status = row["status"]
        if "state" in row:
            dialogue.state = row["state"]
        if "closedBy" in row:
            dialogue.closedBy = row["closedBy"]
        if "closeReason" in row:
            dialogue.closeReason = row["closeReason"]
        if "notes" in row:
            dialogue.notes = row["notes"]

        if "messageCount" in row and row["messageCount"]:
            dialogue.messageCount = int(row["messageCount"])

        return is_update


# Create model importers using factory function
user_importer = create_model_importer(
    User,
    "telegramID",
    {
        "telegramID": "int",
        "nickname": "str",
        "firstname": "str",
        "lastname": "str",
        "lang": "str",
        "status": "str",
        "notes": "str"
    }
)

ticket_importer = create_model_importer(
    Ticket,
    "ticketID",
    {
        "ticketID": "int",
        "userID": "int",
        "mainbot_user_id": "int",
        "status": "str",
        "priority": "str",
        "category": "str",
        "subject": "str",
        "description": "str",
        "error_code": "str",
        "context": "json",
        "assignedOperatorID": "int",
        "resolutionTime": "int",
        "resolution": "str",
        "dialogueID": "str",
        "clientSatisfaction": "int",
        "clientFeedback": "str"
    }
)

dialogue_importer = create_model_importer(
    Dialogue,
    "dialogueID",
    {
        "dialogueID": "str",
        "dialogueType": "str",
        "ticketID": "int",
        "userID": "int",
        "operatorID": "int",
        "groupID": "int",
        "threadID": "int",
        "status": "str",
        "state": "str",
        "closedBy": "str",
        "closeReason": "str",
        "messageCount": "int",
        "notes": "json"
    }
)


async def import_all(bot) -> Dict[str, Any]:
    """Import all data from Google Sheets."""
    manager = DataImportManager()

    # Register all importers
    manager.register_importer("Users", UserImporter())
    manager.register_importer("Operators", OperatorImporter())
    manager.register_importer("Tickets", TicketImporter())
    manager.register_importer("Dialogues", DialogueImporter())

    async def notify_admins(message: str, admin_ids: List[int] = None):
        if not admin_ids:
            admin_ids = Config.get(Config.ADMINS, [])
        for admin_id in admin_ids:
            try:
                await bot.send_message(admin_id, message)
            except Exception as e:
                logger.error(f"Error notifying admin {admin_id}: {e}")

    results = await manager.import_all(admin_notifier=notify_admins)
    return results