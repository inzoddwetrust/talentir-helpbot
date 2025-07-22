"""
Module for working with Google services (Sheets, Drive).
Provides asynchronous interface for Google Sheets.
"""
import logging
import asyncio
from typing import Tuple, List, Dict, Any

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
import gspread
from config import Config

logger = logging.getLogger(__name__)

THREAD_SEMAPHORE = asyncio.Semaphore(10)


class AsyncGspreadClient:
    """Asynchronous wrapper around gspread."""

    def __init__(self, client: gspread.Client, drive_service: Any):
        """
        Initialize asynchronous client.

        Args:
            client: Synchronous gspread client
            drive_service: Google Drive service
        """
        self.client = client
        self.drive_service = drive_service
        self._spreadsheet_cache = {}

    @classmethod
    async def create(cls) -> 'AsyncGspreadClient':
        """
        Create asynchronous gspread client.

        Returns:
            AsyncGspreadClient: Asynchronous client
        """
        try:
            def create_sync_clients():
                # Получаем путь к файлу креденшиалов и скоупы из Config
                credentials_file = Config.get(Config.GOOGLE_CREDENTIALS_JSON)
                scopes = Config.get(Config.GOOGLE_SCOPES)

                creds = Credentials.from_service_account_file(
                    credentials_file,
                    scopes=scopes
                )
                sync_sheets_client = gspread.authorize(creds)
                sync_drive_service = build(
                    "drive", "v3",
                    credentials=creds,
                    cache_discovery=False
                )
                return sync_sheets_client, sync_drive_service

            client, drive_service = await to_thread_with_limit(create_sync_clients)
            return cls(client, drive_service)
        except Exception as e:
            logger.error(f"Failed to initialize Google services: {e}")
            raise

    async def open_by_key(self, key: str) -> 'AsyncSpreadsheet':
        """
        Open spreadsheet by key.

        Args:
            key: Spreadsheet ID

        Returns:
            AsyncSpreadsheet: Asynchronous wrapper for spreadsheet
        """
        if key in self._spreadsheet_cache:
            return self._spreadsheet_cache[key]

        try:
            spreadsheet = await to_thread_with_limit(self.client.open_by_key, key)
            async_spreadsheet = AsyncSpreadsheet(spreadsheet)
            # Save to cache
            self._spreadsheet_cache[key] = async_spreadsheet
            return async_spreadsheet
        except Exception as e:
            logger.error(f"Error opening spreadsheet by key {key}: {e}")
            raise


class AsyncSpreadsheet:
    """Asynchronous wrapper around gspread.Spreadsheet."""

    def __init__(self, spreadsheet: gspread.Spreadsheet):
        """
        Initialize.

        Args:
            spreadsheet: Synchronous spreadsheet object
        """
        self.spreadsheet = spreadsheet
        self._worksheet_cache = {}  # Cache for already loaded worksheets

    async def worksheet(self, title: str) -> 'AsyncWorksheet':
        """
        Get worksheet by title.

        Args:
            title: Worksheet title

        Returns:
            AsyncWorksheet: Asynchronous wrapper for worksheet
        """
        # Check cache
        if title in self._worksheet_cache:
            return self._worksheet_cache[title]

        try:
            worksheet = await to_thread_with_limit(self.spreadsheet.worksheet, title)
            async_worksheet = AsyncWorksheet(worksheet)
            # Save to cache
            self._worksheet_cache[title] = async_worksheet
            return async_worksheet
        except Exception as e:
            logger.error(f"Error getting worksheet {title}: {e}")
            raise

    async def worksheets(self) -> List['AsyncWorksheet']:
        """
        Get all worksheets in spreadsheet.

        Returns:
            List[AsyncWorksheet]: List of asynchronous wrappers for worksheets
        """
        try:
            worksheets = await to_thread_with_limit(self.spreadsheet.worksheets)
            async_worksheets = []

            for worksheet in worksheets:
                title = worksheet.title
                if title not in self._worksheet_cache:
                    self._worksheet_cache[title] = AsyncWorksheet(worksheet)
                async_worksheets.append(self._worksheet_cache[title])

            return async_worksheets
        except Exception as e:
            logger.error(f"Error getting worksheets: {e}")
            raise


class AsyncWorksheet:
    """Asynchronous wrapper around gspread.Worksheet."""

    def __init__(self, worksheet: gspread.Worksheet):
        """
        Initialize.

        Args:
            worksheet: Synchronous worksheet object
        """
        self.worksheet = worksheet
        self.title = worksheet.title

    async def get_all_records(self) -> List[Dict[str, Any]]:
        """
        Get all records from worksheet.

        Returns:
            List[Dict[str, Any]]: List of dictionaries with data
        """
        try:
            return await to_thread_with_limit(self.worksheet.get_all_records)
        except Exception as e:
            logger.error(f"Error getting records from worksheet {self.title}: {e}")
            raise

    async def row_values(self, row: int) -> List[str]:
        """
        Get row values.

        Args:
            row: Row number

        Returns:
            List[str]: List of values
        """
        try:
            return await to_thread_with_limit(self.worksheet.row_values, row)
        except Exception as e:
            logger.error(f"Error getting row values from worksheet {self.title}: {e}")
            raise

    async def update_cell(self, row: int, col: int, value: str) -> None:
        """
        Update cell.

        Args:
            row: Row number
            col: Column number
            value: New value
        """
        try:
            await to_thread_with_limit(self.worksheet.update_cell, row, col, value)
        except Exception as e:
            logger.error(f"Error updating cell ({row}, {col}) in worksheet {self.title}: {e}")
            raise

    async def update(self, range_name: str, values: List[List[Any]]) -> None:
        """
        Update range of cells.

        Args:
            range_name: Range in A1 notation
            values: Values to update
        """
        try:
            await to_thread_with_limit(self.worksheet.update, range_name, values)
        except Exception as e:
            logger.error(f"Error updating range {range_name} in worksheet {self.title}: {e}")
            raise

    async def append_rows(self, values: List[List[Any]]) -> None:
        """
        Append rows to worksheet.

        Args:
            values: Values to append
        """
        try:
            await to_thread_with_limit(self.worksheet.append_rows, values)
        except Exception as e:
            logger.error(f"Error appending rows to worksheet {self.title}: {e}")
            raise


async def get_google_services() -> Tuple[AsyncGspreadClient, Any]:
    """
    Get asynchronous clients for Google services.

    Returns:
        Tuple[AsyncGspreadClient, Any]: Asynchronous gspread client and Drive service
    """
    client = await AsyncGspreadClient.create()
    return client, client.drive_service


async def to_thread_with_limit(func, *args, **kwargs):
    """Run function in separate thread with limiter via semaphore"""
    async with THREAD_SEMAPHORE:
        try:
            return await asyncio.to_thread(func, *args, **kwargs)
        except RuntimeError as e:
            if "can't start new thread" in str(e):
                logger.error("Thread limit reached despite semaphore. Consider reducing concurrency further.")
            raise
