"""
Data export system for synchronizing database with external sources.
Provides a background service for exporting data to Google Sheets.
"""
import asyncio
import logging
from datetime import datetime
from typing import Dict, List, Iterable, Any, Optional, Type, Callable, TypeVar

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from core.google_services import get_google_services
from core.db import get_db_session_ctx
from config import Config

logger = logging.getLogger(__name__)

# Type variable for generic model type
ModelType = TypeVar('ModelType')


class DataExporter:
    """Base data exporter with common functionality."""

    def __init__(self, update_interval: int = 300):
        """
        Initialize data exporter with update interval.

        Args:
            update_interval: Update interval in seconds (default: 300)
        """
        self.update_interval = update_interval
        self._running = False
        self.last_sync = {}

    async def start(self):
        """Start the export service."""
        if self._running:
            logger.warning(f"{self.__class__.__name__} is already running")
            return

        self._running = True
        logger.info(f"Starting {self.__class__.__name__}")
        await self.run()

    async def stop(self):
        """Stop the export service."""
        self._running = False
        logger.info(f"Stopping {self.__class__.__name__}")

    async def run(self):
        """
        Main export loop.
        Should be implemented in subclasses.
        """
        raise NotImplementedError("Subclasses must implement run method")


class SheetsExporter(DataExporter):
    """Service for exporting data from database to Google Sheets."""

    def __init__(self,
                 sheet_id: str = None,
                 session_factory=None,
                 exporters: Optional[Dict[str, 'ModelExporter']] = None,
                 update_interval: int = 300):
        """
        Initialize sheets exporter service.

        Args:
            sheet_id: Google Sheet ID (defaults to config.GOOGLE_SHEET_ID)
            session_factory: Function that returns a database session
            exporters: Dictionary mapping sheet names to exporters
            update_interval: Update interval in seconds (default: 300)
        """
        super().__init__(update_interval)

        self.sheet_id = sheet_id or Config.get(Config.GOOGLE_SHEET_ID)
        self.session_factory = session_factory or get_db_session_ctx
        self.sheets_client = None
        self.drive_service = None
        self.worksheets = {}
        self.exporters = exporters or {}

        # Last sync metadata
        self.last_sync = {name: None for name in self.exporters.keys()}

    def register_exporter(self, sheet_name: str, exporter: 'ModelExporter'):
        """
        Register a model exporter for a specific sheet.

        Args:
            sheet_name: Name of the worksheet in Google Sheets
            exporter: ModelExporter instance
        """
        self.exporters[sheet_name] = exporter
        self.last_sync[sheet_name] = None

    async def connect(self):
        """Establish connection to Google Sheets."""
        try:
            # Connect to Google Sheets
            self.sheets_client, self.drive_service = await get_google_services()

            # Get access to required worksheets
            spreadsheet = await self.sheets_client.open_by_key(self.sheet_id)

            # Get all worksheets at once to reduce API calls
            all_worksheets = await spreadsheet.worksheets()

            # Map worksheet names to worksheets (case-insensitive)
            self.worksheets = {}
            for ws in all_worksheets:
                self.worksheets[ws.title.lower()] = ws

            logger.info(f"Successfully connected to Google Sheets (ID: {self.sheet_id})")
            return True

        except Exception as e:
            logger.error(f"Failed to connect to Google Sheets: {e}")
            return False

    async def sync_worksheet(self, sheet_name: str) -> bool:
        """
        Synchronize data to a specific worksheet.

        Args:
            sheet_name: Name of the worksheet

        Returns:
            True if successful, False otherwise
        """
        sheet_name_lower = sheet_name.lower()

        try:
            # Check if worksheet exists
            if sheet_name_lower not in self.worksheets:
                logger.warning(f"{sheet_name} worksheet not found. Attempting to reconnect...")
                if not await self.connect() or sheet_name_lower not in self.worksheets:
                    logger.error(f"{sheet_name} worksheet still not found after reconnect")
                    return False

            # Get worksheet and exporter
            sheet = self.worksheets[sheet_name_lower]
            exporter = self.exporters.get(sheet_name)

            if not exporter:
                logger.error(f"No exporter registered for {sheet_name}")
                return False

            # Get all records from sheet
            records = await sheet.get_all_records()

            # Create dictionary for efficient lookup
            sheet_data = exporter.create_sheet_index(records)

            # Get database records
            with self.session_factory() as session:
                # Use exporter to get and format database records
                db_records = exporter.get_records(session)
                updates, new_records = exporter.compare_records(db_records, sheet_data)

            # Apply changes in batches
            updates_count = 0
            if updates:
                update_tasks = []
                for batch in batch_items(updates, 10):
                    for row_idx, data in batch:
                        update_tasks.append(sheet.update(f"A{row_idx}:Z{row_idx}", [data]))

                # Execute updates in parallel with error handling
                if update_tasks:
                    results = await asyncio.gather(*update_tasks, return_exceptions=True)
                    updates_count = sum(1 for r in results if not isinstance(r, Exception))

                    # Log errors
                    for i, result in enumerate(results):
                        if isinstance(result, Exception):
                            logger.warning(f"Error updating row in {sheet_name}: {result}")

            # Add new records in batches
            new_records_count = 0
            if new_records:
                for batch in batch_items(new_records, 10):
                    try:
                        await sheet.append_rows(batch)
                        new_records_count += len(batch)
                        # Add a small delay to avoid rate limits
                        await asyncio.sleep(1)
                    except Exception as e:
                        logger.error(f"Error adding rows to {sheet_name}: {e}")

            # Update last sync time
            self.last_sync[sheet_name] = datetime.now()
            logger.info(f"{sheet_name} sync completed: {updates_count} updated, {new_records_count} added")
            return True

        except Exception as e:
            logger.error(f"Error syncing {sheet_name}: {e}")
            return False

    async def run(self):
        # Initial delay to allow system to stabilize
        logger.info(f"Starting export service with interval {self.update_interval} seconds")
        await asyncio.sleep(30)  # 30 секунд

        while self._running:
            try:
                logger.debug("Starting export cycle")

                # Connect if not connected
                if not self.sheets_client:
                    logger.info("No active sheets client, attempting to connect")
                    if not await self.connect():
                        logger.warning("Failed to connect, will retry in 60 seconds")
                        await asyncio.sleep(60)
                        continue

                logger.info(f"Preparing to sync exporters: {list(self.exporters.keys())}")

                # Run all sync operations concurrently
                tasks = []
                for sheet_name in self.exporters.keys():
                    tasks.append(self.sync_worksheet(sheet_name))

                # Execute all tasks with error handling
                results = await asyncio.gather(*tasks, return_exceptions=True)

                # Check results for errors
                for i, result in enumerate(results):
                    sheet_name = list(self.exporters.keys())[i]
                    if isinstance(result, Exception):
                        logger.error(f"Error in {sheet_name} sync: {result}")
                    elif result is False:
                        logger.warning(f"Sync failed for {sheet_name}")

            except Exception as e:
                logger.error(f"Export error: {e}")
                self.sheets_client = None
                # Add delay before retry
                await asyncio.sleep(60)

            logger.debug("Completed export cycle, waiting for next interval")
            await asyncio.sleep(self.update_interval)


class ModelExporter:
    """Exporter for specific model type to Google Sheets."""

    def __init__(self,
                 model_class: Type[ModelType],
                 id_column: str,
                 field_mapping: Dict[str, str] = None,
                 query_filter: Optional[Callable] = None,
                 format_funcs: Dict[str, Callable] = None):
        """
        Initialize model exporter.

        Args:
            model_class: SQLAlchemy model class
            id_column: Primary key column name
            field_mapping: Optional mapping of sheet columns to model attributes
            query_filter: Optional function to filter query results
            format_funcs: Optional dictionary of formatting functions for fields
        """
        self.model_class = model_class
        self.id_column = id_column
        self.field_mapping = field_mapping or {}
        self.query_filter = query_filter
        self.format_funcs = format_funcs or {}

        # Auto-detect model columns if field_mapping not provided
        if not self.field_mapping:
            self._discover_model_columns()

    def _discover_model_columns(self):
        """Automatically discover model columns for field mapping."""
        mapper = inspect(self.model_class)
        for column in mapper.columns:
            self.field_mapping[column.name] = column.name

    def get_records(self, session: Session) -> List[ModelType]:
        """
        Get records from database.

        Args:
            session: SQLAlchemy session

        Returns:
            List of model instances
        """
        query = session.query(self.model_class)

        # Apply filter if provided
        if self.query_filter:
            query = self.query_filter(query)

        return query.all()

    def format_record(self, record: ModelType) -> List[Any]:
        """
        Format model instance for sheet export.

        Args:
            record: Model instance

        Returns:
            List of values for sheet row
        """
        result = []

        # Get all mapped field values in order
        for field_name in self.field_mapping.values():
            # Get raw value
            value = self._get_attribute_value(record, field_name)

            # Apply custom formatting if available
            if field_name in self.format_funcs:
                value = self.format_funcs[field_name](value)
            else:
                # Default formatting based on type
                value = self._format_value(value)

            result.append(value)

        return result

    def _get_attribute_value(self, record: ModelType, attr_name: str) -> Any:
        """
        Get attribute value, supporting nested attributes with dot notation.

        Args:
            record: Model instance
            attr_name: Attribute name (can include dots for nested attributes)

        Returns:
            Attribute value
        """
        if '.' not in attr_name:
            return getattr(record, attr_name, None)

        # Handle nested attributes (e.g., "user.name")
        parts = attr_name.split('.')
        value = record

        for part in parts:
            value = getattr(value, part, None)
            if value is None:
                break

        return value

    def _format_value(self, value: Any) -> str:
        """
        Format value for sheet export based on type.

        Args:
            value: Raw value

        Returns:
            Formatted value suitable for Google Sheets
        """
        if value is None:
            return ""
        elif isinstance(value, datetime):
            return value.strftime("%Y-%m-%d %H:%M:%S")
        elif isinstance(value, bool):
            return "true" if value else "false"
        else:
            return str(value)

    def create_sheet_index(self, records: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        """
        Create index of sheet records by ID for efficient comparison.

        Args:
            records: Sheet records

        Returns:
            Dictionary mapping ID to record with row index
        """
        sheet_id_column = None

        # Find ID column in sheet
        for sheet_column, model_field in self.field_mapping.items():
            if model_field == self.id_column:
                sheet_id_column = sheet_column
                break

        if not sheet_id_column:
            logger.warning(f"ID column {self.id_column} not found in field mapping")
            return {}

        # Create index
        return {
            str(row[sheet_id_column]): {"data": row, "row_index": idx + 2}  # +2 for header row
            for idx, row in enumerate(records)
            if sheet_id_column in row and row[sheet_id_column]
        }

    def compare_records(self,
                        db_records: List[ModelType],
                        sheet_data: Dict[str, Dict[str, Any]]) -> tuple[List[tuple[int, List[Any]]], List[List[Any]]]:
        updates = []
        new_records = []

        for record in db_records:
            # Get record ID
            record_id = getattr(record, self.id_column)
            record_id_str = str(record_id)

            # Format record data
            record_data = self.format_record(record)

            # Check if record exists in sheet
            sheet_record = sheet_data.get(record_id_str)

            if sheet_record:
                # Check if update is needed
                if self.record_needs_update(record_data, sheet_record["data"]):
                    updates.append((sheet_record["row_index"], record_data))
            else:
                # New record
                new_records.append(record_data)

        return updates, new_records

    def record_needs_update(self, record_data: List[Any], sheet_record: Dict[str, Any]) -> bool:
        """
        Check if record needs to be updated.

        Args:
            record_data: Formatted database record data
            sheet_record: Sheet record data

        Returns:
            True if update is needed, False otherwise
        """
        # Get mapping of sheet columns to indices in record_data
        col_to_idx = {col: idx for idx, col in enumerate(self.field_mapping.keys())}

        for sheet_col, idx in col_to_idx.items():
            # Skip if index is out of range
            if idx >= len(record_data):
                continue

            # Get values for comparison
            db_value = record_data[idx]
            sheet_value = sheet_record.get(sheet_col, "")

            # Compare values (with special handling for numbers)
            if self._is_numeric(db_value) and self._is_numeric(sheet_value):
                # Compare numbers with tolerance
                db_num = float(db_value) if db_value != "" else 0.0
                sheet_num = float(sheet_value) if sheet_value != "" else 0.0

                if abs(db_num - sheet_num) > 0.001:
                    return True
            else:
                # String comparison
                db_str = str(db_value).strip() if db_value not in [None, ""] else ""
                sheet_str = str(sheet_value).strip() if sheet_value not in [None, ""] else ""

                if db_str != sheet_str:
                    return True

        return False

    @staticmethod
    def _is_numeric(value: Any) -> bool:
        """
        Check if value is numeric.

        Args:
            value: Value to check

        Returns:
            True if numeric, False otherwise
        """
        if isinstance(value, (int, float)):
            return True

        if isinstance(value, str):
            try:
                float(value)
                return True
            except (ValueError, TypeError):
                pass

        return False


# Utility function for batch processing
def batch_items(iterable: Iterable, size: int) -> Iterable:
    """
    Split a list into batches of specified size.

    Args:
        iterable: The list to split
        size: Batch size

    Yields:
        Batches of the list
    """
    if not iterable:
        return

    items = list(iterable)  # Convert to list if it's not already
    length = len(items)

    for ndx in range(0, length, size):
        yield items[ndx:min(ndx + size, length)]


# Helper function to create common formatters
def create_formatters(date_format: str = "%Y-%m-%d %H:%M:%S") -> Dict[str, Callable]:
    """
    Create common format functions for export.

    Args:
        date_format: Format string for datetime values

    Returns:
        Dictionary of formatter functions
    """
    return {
        "date": lambda d: d.strftime(date_format) if d else "",
        "bool": lambda b: "true" if b else "false",
        "int": lambda i: str(i) if i is not None else "0",
        "float": lambda f: f"{f:.2f}" if f is not None else "0.00",
        "str": lambda s: str(s) if s else "",
        "json": lambda j: j[:100] + "..." if isinstance(j, str) and len(j) > 100 else j,
        "percent": lambda p: f"{p:.2f}%" if p is not None else "0.00%",
        "money": lambda m: f"${m / 100:.2f}" if m is not None else "$0.00",  # Convert cents to dollars
    }
