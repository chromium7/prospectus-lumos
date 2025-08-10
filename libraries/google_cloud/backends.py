import mimetypes
from pathlib import Path
from typing import Iterable, List, Optional, IO, Any, Dict, Tuple

# Google drive
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseUpload

from .tuples import File


class GoogleDriveBackend:
    """
        Args:
            service_account_file (str): path-like string contain location of json
                                        formatted google service account credential

    Attributes:
        _path_id_mapping (Optional[dict]): cached path to id mapping data
        creds (Credential): google drive credential object
        client (Client): main google drive client

    Note in GoogleDriveBackend extension is in mimetype format see: https://mimetype.io
    """

    _path_id_mapping: Optional[dict] = None

    def __init__(self, service_account_file: str) -> None:
        scopes = [
            # Google drive
            "https://www.googleapis.com/auth/drive",
            # Google sheet
            "https://www.googleapis.com/auth/spreadsheets.readonly",
        ]
        self.creds = Credentials.from_service_account_file(service_account_file, scopes=scopes)
        self.drive_client = build("drive", "v3", credentials=self.creds)
        self.sheets_client = build("sheets", "v4", credentials=self.creds)

    def __del__(self) -> None:
        self.drive_client.close()

    def get_path_to_id_mapping(self, max_size: int = 100) -> dict:
        """
        will return dict of path to it's id mapping, limited to 100 keys
        example: {
            'folder-one/folder-one-one': 'abcd11',
            'folder-one/folder-one-two': 'abcd12',
            'folder-two': 'abcd20'
        }
        """
        if self._path_id_mapping:
            return self._path_id_mapping

        query = "trashed = false and mimeType = 'application/vnd.google-apps.folder'"
        results = (
            self.drive_client.files().list(pageSize=max_size, fields="files(id, name, parents)", q=query).execute()
        )
        folders = results.get("files", [])

        id_to_folder_data = {folder["id"]: folder for folder in folders}

        path_to_id_mapping = {}
        for folder in id_to_folder_data.values():
            path = folder["name"]
            if "parents" not in folder or not folder["parents"]:
                path_to_id_mapping[path] = folder["id"]
                continue

            parent_id = folder["parents"][0]
            parent_data = id_to_folder_data[parent_id]

            path = Path(parent_data["name"], path).as_posix()
            while "parents" in parent_data and parent_data["parents"]:
                parent_id = parent_data["parents"][0]
                parent_data = id_to_folder_data[parent_id]
                path = Path(parent_data["name"], path).as_posix()
            path_to_id_mapping[path] = folder["id"]

        self._path_id_mapping = path_to_id_mapping
        return path_to_id_mapping

    def list_files(self, path: str, extensions: Optional[Iterable[str]] = None, page_size: int = 500) -> List[File]:
        # exclude trashed and folders
        query = "trashed = false and mimeType != 'application/vnd.google-apps.folder'"

        if path and path != ".":
            path_id_mapping = self.get_path_to_id_mapping()
            path_id = path_id_mapping.get(path)
            if not path_id:
                return []
            query += f" and '{path_id}' in parents"

        if extensions:
            for extension in extensions:
                mimetype = mimetypes.guess_type(f"file.{extension}")[0]
                if not mimetype:
                    raise ValueError(f"Unknown mimetype for extension: {extension}")
                query += f" and mimeType='{mimetype}'"

        response = (
            self.drive_client.files()
            .list(q=query, fields="nextPageToken, files(id, name, size)", pageSize=page_size)
            .execute()
        )

        files = []
        for file_data in response.get("files", []):
            files.append(
                File(file_data["id"], file_data["name"], self.get_extension(file_data["name"]), file_data["size"])
            )
        return files

    def upload_file(
        self,
        target_path: str,
        source_file: Optional[IO] = None,  # type: ignore
        source_file_path: Optional[str] = None,
    ) -> File:
        if not source_file and not source_file_path:
            raise ValueError("file and filename must be provided")

        path = Path(target_path)

        # try to guess mimetype from file name
        mimetype = mimetypes.guess_type(path.name)[0]
        if not mimetype:
            raise ValueError(f"Failed to guess mimetype for file {path.name}")

        file_metadata: dict = {"name": path.name}

        parent = path.parent.as_posix()
        if parent != ".":
            path_id_mapping = self.get_path_to_id_mapping()
            path_id = path_id_mapping.get(parent)
            if not path_id:
                raise ValueError(f"path {parent} not found")

            file_metadata["parents"] = [path_id]

        if source_file:
            media = MediaIoBaseUpload(source_file, mimetype=mimetype)
        else:
            media = MediaFileUpload(source_file_path, mimetype=mimetype)

        result = self.drive_client.files().create(body=file_metadata, media_body=media, fields="id,name,size").execute()
        return File(result["id"], result["name"], extension=self.get_extension(target_path), size=result["size"])

    def delete_file(self, key: str) -> None:
        self.drive_client.files().delete(fileId=key).execute()

    def open_sheet(self, id: str, sheet_name: str | None = None, range: str | None = None) -> Dict[str, Any]:
        """Open and read data from a Google Sheet"""
        if sheet_name and range:
            range_param = f"{sheet_name}!{range}"
        elif sheet_name:
            range_param = sheet_name
        elif range:
            range_param = range
        else:
            range_param = "A:Z"  # Default range

        return self.sheets_client.spreadsheets().values().get(spreadsheetId=id, range=range_param).execute()

    def get_sheet_names(self, id: str) -> List[str]:
        """Get all sheet names from a spreadsheet"""
        spreadsheet = self.sheets_client.spreadsheets().get(spreadsheetId=id).execute()
        return [sheet["properties"]["title"] for sheet in spreadsheet["sheets"]]

    def parse_monthly_budget_sheet(self, sheet_id: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Parse a monthly budget Google Sheet and extract expenses and income data.

        Many templates place "Expenses" and "Income" side-by-side with optional
        leading empty columns. This parser:
          - Locates the row containing the section titles ("Expenses"/"Income")
            anywhere in the row, not just the first column
          - Detects the corresponding header row (Date, Amount, Description, Category)
            for each section and records the starting column index
          - Iterates subsequent rows and extracts both sections in parallel
        """
        try:
            # Discover the transactions sheet
            sheet_names = self.get_sheet_names(sheet_id)
            if not sheet_names:
                return [], []

            transactions_sheet = None
            # Prefer a sheet named exactly "Transactions"; else fall back to second, then first
            for name in sheet_names:
                if str(name).strip().lower() == "transactions":
                    transactions_sheet = name
                    break
            if transactions_sheet is None:
                transactions_sheet = sheet_names[1] if len(sheet_names) > 1 else sheet_names[0]

            values = self.open_sheet(sheet_id, transactions_sheet).get("values", [])
            if not values:
                return [], []

            def normalize_amount(raw: str) -> float:
                s = str(raw)
                # Remove currency and separators used commonly in IDR formats
                cleaned = (
                    s.replace("Rp", "").replace("rp", "").replace(" ", "").replace(",", "").replace(".", "").strip()
                )
                try:
                    return float(cleaned) if cleaned else 0.0
                except ValueError:
                    return 0.0

            # Locate the columns where sections begin
            expenses_col = None
            income_col = None
            header_row_idx = None

            for i, row in enumerate(values):
                # Find a row that contains the words 'expenses' or 'income' anywhere
                lowered = [str(c).strip().lower() for c in row]
                for j, cell in enumerate(lowered):
                    if cell == "expenses" and expenses_col is None:
                        expenses_col = j
                    if cell == "income" and income_col is None:
                        income_col = j
                if expenses_col is not None or income_col is not None:
                    # Search a few rows below for the header labels near these columns
                    for k in range(i, min(i + 5, len(values))):
                        probe = [str(c).strip().lower() for c in values[k]]
                        if expenses_col is not None and expenses_col < len(probe) and probe[expenses_col] == "date":
                            header_row_idx = header_row_idx or k
                        if income_col is not None and income_col < len(probe) and probe[income_col] == "date":
                            header_row_idx = header_row_idx or k
                    if header_row_idx is not None:
                        break

            # If not found via titles, try to find two repeated blocks of headers in one row
            if header_row_idx is None:
                for i, row in enumerate(values):
                    lowered = [str(c).strip().lower() for c in row]
                    # Look for 'date, amount, description' sequence twice
                    try:
                        first_date = lowered.index("date")
                    except ValueError:
                        continue
                    # Look ahead for the next 'date'
                    try:
                        second_date = lowered.index("date", first_date + 1)
                    except ValueError:
                        second_date = None
                    expenses_col = expenses_col if expenses_col is not None else first_date
                    income_col = income_col if income_col is not None else second_date
                    header_row_idx = i
                    break

            if header_row_idx is None or (expenses_col is None and income_col is None):
                return [], []

            # Extract rows
            expenses: list[dict] = []
            income: list[dict] = []

            start_row = header_row_idx + 1
            for r in range(start_row, len(values)):
                row = values[r]
                # Expenses block
                if expenses_col is not None and expenses_col < len(row):
                    date = row[expenses_col] if expenses_col < len(row) else ""
                    amount = row[expenses_col + 1] if expenses_col + 1 < len(row) else ""
                    description = row[expenses_col + 2] if expenses_col + 2 < len(row) else ""
                    category = row[expenses_col + 3] if expenses_col + 3 < len(row) else ""
                    if any([date, amount, description, category]):
                        amount_value = normalize_amount(amount)
                        if amount_value > 0 and (date or description):
                            expenses.append(
                                {
                                    "date": str(date).strip(),
                                    "amount": amount_value,
                                    "description": str(description).strip(),
                                    "category": str(category).strip(),
                                    "type": "expenses",
                                }
                            )

                # Income block
                if income_col is not None and income_col < len(row):
                    date_i = row[income_col] if income_col < len(row) else ""
                    amount_i = row[income_col + 1] if income_col + 1 < len(row) else ""
                    description_i = row[income_col + 2] if income_col + 2 < len(row) else ""
                    category_i = row[income_col + 3] if income_col + 3 < len(row) else ""
                    if any([date_i, amount_i, description_i, category_i]):
                        amount_value_i = normalize_amount(amount_i)
                        if amount_value_i > 0 and (date_i or description_i):
                            income.append(
                                {
                                    "date": str(date_i).strip(),
                                    "amount": amount_value_i,
                                    "description": str(description_i).strip(),
                                    "category": str(category_i).strip(),
                                    "type": "income",
                                }
                            )

            return expenses, income

        except Exception as e:
            raise ValueError(f"Failed to parse sheet {sheet_id}: {str(e)}")

    def list_monthly_budget_files(self, path: str = "") -> List[File]:
        """List all Google Sheets that match the monthly budget naming pattern"""
        # Get all Google Sheets files
        query = "trashed = false and mimeType = 'application/vnd.google-apps.spreadsheet'"

        if path and path != ".":
            path_id_mapping = self.get_path_to_id_mapping()
            path_id = path_id_mapping.get(path)
            if not path_id:
                return []
            query += f" and '{path_id}' in parents"

        response = (
            self.drive_client.files()
            .list(q=query, fields="nextPageToken, files(id, name, size)", pageSize=500)
            .execute()
        )

        files = []
        for file_data in response.get("files", []):
            file_name = file_data["name"].lower()
            # Check if the file name matches the monthly budget pattern
            if "monthly budget" in file_name and any(
                month in file_name
                for month in ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
            ):
                files.append(
                    File(
                        file_data["id"],
                        file_data["name"],
                        "gsheet",  # Custom extension for Google Sheets
                        int(file_data.get("size", 0)),
                    )
                )
        return files

    def get_extension(self, filename: str) -> str:
        """Get file extension from filename"""
        return Path(filename).suffix.lstrip(".")
