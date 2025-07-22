"""
Configuration and data importer for helpbot.
Loads configuration and data from Google Sheets.
"""
import json
import logging
from typing import Dict, Any, Optional, Union, List, Type, Callable, TypeVar
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from core.google_services import get_google_services
from core.utils import parse_date, parse_bool, parse_int, parse_float, clean_str
from core.db import get_db_session_ctx
from config import Config

logger = logging.getLogger(__name__)

T = TypeVar('T')
ModelType = TypeVar('ModelType')


@dataclass
class ImportStats:
    total: int = 0
    updated: int = 0
    added: int = 0
    skipped: int = 0
    errors: int = 0
    error_rows: list = field(default_factory=list)

    def add_error(self, row: int, error: str):
        self.errors += 1
        self.error_rows.append((row, error))

    def get_report(self) -> str:
        report = [
            f"Import statistics:",
            f"Total rows: {self.total}",
            f"Updated: {self.updated}",
            f"Added: {self.added}",
            f"Skipped: {self.skipped}",
            f"Errors: {self.errors}"
        ]
        if self.error_rows:
            report.append("\nErrors:")
            for row, error in self.error_rows:
                report.append(f"Row {row}: {error}")
        return "\n".join(report)


class ConfigImporter:
    """Import configuration from Google Sheets for helpbot."""

    @staticmethod
    async def import_config(sheet_id: str = None, sheet_name: str = "Config") -> Dict[str, Any]:
        """
        Import configuration from Google Sheets.

        Expected sheet format:
        | key | value | description |
        |-----|-------|-------------|
        | TICKET_CATEGORIES | ["payment", "kyc", "technical", "other"] | Available ticket categories |
        | AUTO_CLOSE_HOURS | 24 | Hours before auto-closing inactive tickets |
        | REMINDER_INTERVALS | [30, 60, 120] | Minutes between inactivity reminders |
        | MAX_TICKETS_PER_OPERATOR | 5 | Maximum concurrent tickets per operator |
        | WELCOME_MESSAGE_DELAY | 2 | Seconds before sending welcome message |
        | OPERATOR_NOTIFICATION_DELAY | 5 | Seconds before notifying operators |
        | AUTO_ASSIGN_ENABLED | true | Enable auto-assignment to operators |
        | FEEDBACK_ENABLED | true | Request feedback after closure |
        | FEEDBACK_DELAY_HOURS | 2 | Hours before requesting feedback |

        Args:
            sheet_id: Google Sheet ID (defaults to Config.GOOGLE_SHEET_ID)
            sheet_name: Sheet name containing config (default: "Config")

        Returns:
            Dictionary with configuration values
        """
        try:
            sheet_id = sheet_id or Config.get(Config.GOOGLE_SHEET_ID)
            sheets_client, _ = await get_google_services()
            spreadsheet = await sheets_client.open_by_key(sheet_id)
            sheet = await spreadsheet.worksheet(sheet_name)
            records = await sheet.get_all_records()

            if not records:
                raise ValueError(f"{sheet_name} sheet is empty or has no valid records")

            config_dict = {}
            for record in records:
                if 'key' not in record or 'value' not in record:
                    logger.warning(f"Invalid config record: {record}")
                    continue

                key = record['key'].strip()
                value = record['value']

                if not key:
                    continue

                try:
                    # Parse value based on key
                    parsed_value = ConfigImporter.parse_config_value(key, value)
                    config_dict[key] = parsed_value

                    # Update Config for known helpbot keys
                    if key in (
                            'GROUP_ID',
                            'TICKET_CATEGORIES',
                            'AUTO_CLOSE_HOURS',
                            'REMINDER_INTERVALS',
                            'MAX_TICKETS_PER_OPERATOR',
                            'WELCOME_MESSAGE_DELAY',
                            'OPERATOR_NOTIFICATION_DELAY',
                            'AUTO_ASSIGN_ENABLED',
                            'FEEDBACK_ENABLED',
                            'FEEDBACK_DELAY_HOURS'
                    ):
                        config_key = getattr(Config, key.upper(), None)
                        if config_key:
                            Config.set(config_key, parsed_value, source="sheets")
                        else:
                            # Dynamic config key
                            Config.set(key, parsed_value, source="sheets")

                except Exception as e:
                    logger.warning(f"Error parsing value for key {key}: {e}")
                    config_dict[key] = value

            logger.info(f"Imported {len(config_dict)} config variables from Google Sheets")
            return config_dict

        except Exception as e:
            logger.error(f"Error importing config: {str(e)}", exc_info=True)
            raise

    @staticmethod
    def parse_config_value(key: str, value: Any) -> Any:
        """
        Parse configuration value based on key and value type.

        Args:
            key: Configuration key
            value: Raw value from sheet

        Returns:
            Parsed value
        """
        # Handle empty values
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None

        # Try to parse JSON for complex types
        if isinstance(value, str) and (value.startswith('{') or value.startswith('[')):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse JSON for {key}: {value}")
                pass

        # Parse booleans
        if isinstance(value, str) and value.lower() in ['true', 'false']:
            return value.lower() == 'true'

        # Parse numbers
        if isinstance(value, str):
            # Check if it's an integer
            try:
                if '.' not in value:
                    return int(value)
                else:
                    return float(value)
            except ValueError:
                pass

        # Return as is
        return value

    @staticmethod
    def get_nested_value(config_dict: Dict[str, Any], key_path: str, default: Any = None) -> Any:
        keys = key_path.split('.')
        value = config_dict
        try:
            for k in keys:
                if isinstance(value, dict):
                    value = value[k]
                else:
                    return default
            return value
        except (KeyError, TypeError):
            return default


class BaseImporter:
    """Base class for model importers."""
    REQUIRED_FIELDS: List[str] = []

    def __init__(self):
        self.stats = ImportStats()

    def validate_required_fields(self, row: Dict[str, Any], row_num: int) -> bool:
        for field in self.REQUIRED_FIELDS:
            if not row.get(field):
                self.stats.add_error(row_num, f"Missing required field: {field}")
                return False
        return True

    def validate_row(self, row: Dict[str, Any], row_num: int) -> bool:
        return self.validate_required_fields(row, row_num)

    async def import_sheet(self, sheet, session_factory=None) -> ImportStats:
        rows = await sheet.get_all_records()
        self.stats.total = len(rows)
        session_factory = session_factory or get_db_session_ctx

        with session_factory() as session:
            try:
                for idx, row in enumerate(rows, start=2):
                    try:
                        if not self.validate_row(row, idx):
                            self.stats.skipped += 1
                            continue

                        if self.process_row(row, session):
                            self.stats.updated += 1
                        else:
                            self.stats.added += 1

                    except Exception as e:
                        self.stats.add_error(idx, str(e))
                        logger.error(f"Row {idx} error: {e}", exc_info=True)

                session.commit()

            except Exception as e:
                logger.error(f"Import failed: {e}", exc_info=True)
                raise

        return self.stats

    def process_row(self, row: Dict[str, Any], session: Session) -> bool:
        raise NotImplementedError("Subclasses must implement process_row")


class ModelImporter(BaseImporter):
    """Generic model importer using field mappings."""

    def __init__(self, model_class: Type[ModelType], id_field: str, field_mapping: Dict[str, Callable[[Any], Any]]):
        super().__init__()
        self.model_class = model_class
        self.id_field = id_field
        self.field_mapping = field_mapping
        self.REQUIRED_FIELDS = [id_field]

    def process_row(self, row: Dict[str, Any], session: Session) -> bool:
        pk_value = row[self.id_field]
        instance = session.query(self.model_class).filter_by(**{self.id_field: pk_value}).first()
        is_update = bool(instance)

        if not instance:
            instance = self.model_class()

        for field, conversion_func in self.field_mapping.items():
            if field in row:
                value = conversion_func(row.get(field))
                setattr(instance, field, value)

        if not is_update:
            session.add(instance)

        return is_update


class DataImportManager:
    """Manager for importing data from Google Sheets."""

    def __init__(self, sheet_id: str = None, session_factory=None):
        self.sheet_id = sheet_id or Config.get(Config.GOOGLE_SHEET_ID)
        if not self.sheet_id:
            logger.warning("Google Sheet ID not configured, imports will fail")
        self.session_factory = session_factory or get_db_session_ctx
        self.importers: Dict[str, BaseImporter] = {}

    def register_importer(self, sheet_name: str, importer: BaseImporter):
        self.importers[sheet_name] = importer

    async def import_all(self, admin_notifier: Optional[Callable[[str, List[int]], None]] = None) -> Dict[
        str, Union[ImportStats, str]]:
        try:
            sheets_client, _ = await get_google_services()
            spreadsheet = await sheets_client.open_by_key(self.sheet_id)

            results = {}
            # Обновляем конфигурацию напрямую через ConfigImporter
            try:
                config_dict = await ConfigImporter.import_config(self.sheet_id)
                results["Config"] = f"Updated successfully ({len(config_dict)} keys)"
            except Exception as e:
                results["Config"] = f"Failed: {str(e)}"
                logger.error(f"Failed to import Config: {e}")

            worksheets = await spreadsheet.worksheets()
            worksheet_map = {ws.title: ws for ws in worksheets}

            for sheet_name, importer in self.importers.items():
                if sheet_name in worksheet_map:
                    try:
                        stats = await importer.import_sheet(worksheet_map[sheet_name], self.session_factory)
                        results[sheet_name] = stats

                    except Exception as e:
                        error_msg = f"Failed: {str(e)}"
                        results[sheet_name] = error_msg
                        logger.error(f"Failed to import {sheet_name}: {e}")
                else:
                    results[sheet_name] = f"Failed: Worksheet {sheet_name} not found"

            return results

        except Exception as e:
            logger.error(f"Error during import_all: {e}")
            return {"Error": str(e)}


def create_model_importer(model_class: Type[ModelType], id_field: str,
                          field_mappings: Dict[str, str]) -> ModelImporter:
    """Factory function to create model importers with standard conversions."""
    conversion_map = {
        'str': clean_str,
        'int': parse_int,
        'float': parse_float,
        'bool': parse_bool,
        'date': parse_date,
        'json': lambda v: json.loads(v) if v and isinstance(v, str) else {},
        'raw': lambda v: v
    }

    field_mapping = {}
    for field, conv_type in field_mappings.items():
        if conv_type in conversion_map:
            field_mapping[field] = conversion_map[conv_type]
        else:
            logger.warning(f"Unknown conversion type '{conv_type}' for field '{field}'")
            field_mapping[field] = conversion_map['raw']

    return ModelImporter(model_class, id_field, field_mapping)