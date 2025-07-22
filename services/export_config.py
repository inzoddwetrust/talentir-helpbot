"""
Centralized configuration for data export to Google Sheets.
"""
from services.data_exporter import SheetsExporter, ModelExporter, create_formatters
from models.user import User, UserType
from models.ticket import Ticket
from models.dialogue import Dialogue
from models.operator import Operator
from config import Config, ConfigurationError

import logging

logger = logging.getLogger(__name__)


def setup_sheets_exporter(sheet_id: str = None) -> SheetsExporter:
    """
    Setup standard sheets exporter for main models.

    Args:
        sheet_id: Optional custom Google Sheet ID
    """
    try:
        if sheet_id is None:
            sheet_id = Config.get(Config.GOOGLE_SHEET_ID)
            if not sheet_id:
                raise ConfigurationError("Google Sheet ID not configured")

        logger.info(f"Setting up Sheets Exporter with sheet ID: {sheet_id}")

        sheets_exporter = SheetsExporter(sheet_id=sheet_id)
        date_formatters = create_formatters()

        # Register User exporter
        sheets_exporter.register_exporter('Users', ModelExporter(
            model_class=User,
            id_column='userID',
            field_mapping={
                'userID': 'userID',
                'createdAt': 'createdAt',
                'telegramID': 'telegramID',
                'user_type': 'user_type',
                'mainbot_user_id': 'mainbot_user_id',
                'lang': 'lang',
                'nickname': 'nickname',
                'firstname': 'firstname',
                'lastname': 'lastname',
                'status': 'status',
                'lastActive': 'lastActive',
                'isOnline': 'isOnline',
                'currentTicketsCount': 'currentTicketsCount',
                'totalTicketsResolved': 'totalTicketsResolved',
                'avgResolutionTime': 'avgResolutionTime',
                'stateFSM': 'stateFSM'
            },
            format_funcs={
                'createdAt': date_formatters['date'],
                'lastActive': date_formatters['date'],
                'isOnline': date_formatters['bool'],
                'user_type': lambda x: x.value if x else ''
            }
        ))

        # Register Ticket exporter
        sheets_exporter.register_exporter('Tickets', ModelExporter(
            model_class=Ticket,
            id_column='ticketID',
            field_mapping={
                'ticketID': 'ticketID',
                'createdAt': 'createdAt',
                'updatedAt': 'updatedAt',
                'userID': 'userID',
                'user_telegramID': 'user.telegramID',
                'user_name': 'user.displayName',
                'mainbot_user_id': 'mainbot_user_id',
                'status': 'status',
                'priority': 'priority',
                'category': 'category',
                'subject': 'subject',
                'description': 'description',
                'error_code': 'error_code',
                'context': 'context',
                'assignedOperatorID': 'assignedOperatorID',
                'operator_name': 'operator.displayName',
                'assignedAt': 'assignedAt',
                'resolvedAt': 'resolvedAt',
                'resolutionTime': 'resolutionTime',
                'resolution': 'resolution',
                'dialogueID': 'dialogueID',
                'clientSatisfaction': 'clientSatisfaction',
                'clientFeedback': 'clientFeedback'
            },
            format_funcs={
                'createdAt': date_formatters['date'],
                'updatedAt': date_formatters['date'],
                'assignedAt': date_formatters['date'],
                'resolvedAt': date_formatters['date'],
                'status': lambda x: x.value if x else '',
                'priority': lambda x: x.value if x else '',
                'context': lambda x: x[:255] if x else ''  # Truncate long JSON
            }
        ))

        # Register Dialogue exporter
        sheets_exporter.register_exporter('Dialogues', ModelExporter(
            model_class=Dialogue,
            id_column='dialogueID',
            field_mapping={
                'dialogueID': 'dialogueID',
                'dialogueType': 'dialogueType',
                'ticketID': 'ticketID',
                'userID': 'userID',
                'user_telegramID': 'user.telegramID',
                'user_name': 'user.displayName',
                'operatorID': 'operatorID',
                'operator_name': 'operator.displayName',
                'groupID': 'groupID',
                'threadID': 'threadID',
                'status': 'status',
                'state': 'state',
                'createdAt': 'createdAt',
                'updatedAt': 'updatedAt',
                'lastActivityTime': 'lastActivityTime',
                'closedAt': 'closedAt',
                'closedBy': 'closedBy',
                'closeReason': 'closeReason',
                'messageCount': 'messageCount',
                'notes': 'notes'
            },
            format_funcs={
                'createdAt': date_formatters['date'],
                'updatedAt': date_formatters['date'],
                'lastActivityTime': date_formatters['date'],
                'closedAt': date_formatters['date'],
                'notes': lambda x: x[:100] + '...' if x and len(x) > 100 else x
            }
        ))

        # Register Operator exporter
        sheets_exporter.register_exporter('Operators', ModelExporter(
            model_class=Operator,
            id_column='operatorID',
            field_mapping={
                'operatorID': 'operatorID',
                'userID': 'userID',
                'telegramID': 'telegramID',
                'displayName': 'displayName',
                'user_firstname': 'user.firstname',
                'user_lastname': 'user.lastname',
                'isActive': 'isActive',
                'workingHours': 'workingHours',
                'specializations': 'specializations',
                'languages': 'languages',
                'currentTicketsCount': 'user.currentTicketsCount',
                'totalTicketsResolved': 'user.totalTicketsResolved',
                'avgResolutionTime': 'user.avgResolutionTime',
                'satisfactionRating': 'satisfactionRating',
                'totalRatings': 'totalRatings',
                'managerNotes': 'managerNotes'
            },
            format_funcs={
                'isActive': date_formatters['bool'],
                'workingHours': lambda x: x[:100] if x else '',
                'specializations': lambda x: x[:100] if x else '',
                'languages': lambda x: x[:100] if x else '',
                'satisfactionRating': lambda x: f"{x:.2f}" if x else "0.00"
            }
        ))

        logger.info("Sheets Exporter configured successfully")
        return sheets_exporter

    except ConfigurationError as e:
        logger.error(f"Failed to setup sheets exporter due to configuration error: {e}")
        # Provide a fallback exporter with a dummy sheet ID
        # This allows the application to start but export won't work
        return SheetsExporter(sheet_id="fallback_sheet_id")

    except Exception as e:
        logger.error(f"Failed to setup sheets exporter: {e}", exc_info=True)
        raise