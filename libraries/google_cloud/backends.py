import mimetypes
from pathlib import Path
from typing import Iterable, List, Optional, IO

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

    def open_sheet(self, id: str, sheet_name: str | None = None, range: str | None = None):
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

    def get_sheet_names(self, id: str):
        """Get all sheet names from a spreadsheet"""
        spreadsheet = self.sheets_client.spreadsheets().get(spreadsheetId=id).execute()
        return [sheet["properties"]["title"] for sheet in spreadsheet["sheets"]]

    def parse_monthly_budget_sheet(self, sheet_id: str):
        """
        Parse a monthly budget Google Sheet and extract expenses and income data.
        Expected to find 'Transactions' sheet with expenses and income tables.
        """
        try:
            # Get all sheet names first
            sheet_names = self.get_sheet_names(sheet_id)

            # Look for 'Transactions' sheet (second sheet as mentioned in requirements)
            transactions_sheet = None
            if len(sheet_names) > 1:
                transactions_sheet = sheet_names[1]  # Second sheet
            elif "Transactions" in sheet_names:
                transactions_sheet = "Transactions"
            else:
                # Fallback to first available sheet
                transactions_sheet = sheet_names[0] if sheet_names else None

            if not transactions_sheet:
                raise ValueError("No suitable transactions sheet found")

            # Get all data from the transactions sheet
            sheet_data = self.open_sheet(sheet_id, transactions_sheet)
            values = sheet_data.get("values", [])

            if not values:
                return [], []

            # Parse the sheet data to extract expenses and income
            expenses = []
            income = []

            # Find the headers and data sections
            current_section = None
            headers = None

            for i, row in enumerate(values):
                if not row:
                    continue

                # Check if this row contains "Expenses" or "Income" header
                first_cell = str(row[0]).strip().lower() if row else ""

                if "expenses" in first_cell:
                    current_section = "expenses"
                    # Next non-empty row should contain headers
                    for j in range(i + 1, len(values)):
                        if values[j] and any(values[j]):
                            headers = [str(cell).strip().lower() for cell in values[j]]
                            break
                    continue
                elif "income" in first_cell:
                    current_section = "income"
                    # Next non-empty row should contain headers
                    for j in range(i + 1, len(values)):
                        if values[j] and any(values[j]):
                            headers = [str(cell).strip().lower() for cell in values[j]]
                            break
                    continue

                # Skip if we don't have a current section or headers
                if not current_section or not headers:
                    continue

                # Skip header rows
                if any(header in str(row[0]).lower() for header in ["date", "amount", "description"]):
                    continue

                # Parse data rows
                if len(row) >= 3 and current_section:  # At least date, amount, description
                    try:
                        # Map the row data to expected columns
                        date_idx = next((i for i, h in enumerate(headers) if "date" in h), 0)
                        amount_idx = next((i for i, h in enumerate(headers) if "amount" in h), 1)
                        desc_idx = next((i for i, h in enumerate(headers) if "description" in h), 2)
                        category_idx = next((i for i, h in enumerate(headers) if "category" in h), 3)

                        date = row[date_idx] if date_idx < len(row) else ""
                        amount = row[amount_idx] if amount_idx < len(row) else ""
                        description = row[desc_idx] if desc_idx < len(row) else ""
                        category = row[category_idx] if category_idx < len(row) else ""

                        # Clean amount (remove currency symbols and convert to float)
                        amount_str = str(amount).replace("Rp", "").replace(",", "").replace(".", "").strip()
                        try:
                            amount_value = float(amount_str) if amount_str else 0.0
                        except ValueError:
                            amount_value = 0.0

                        if date and amount_value > 0:  # Only include rows with valid data
                            record = {
                                "date": str(date).strip(),
                                "amount": amount_value,
                                "description": str(description).strip(),
                                "category": str(category).strip(),
                                "type": current_section,
                            }

                            if current_section == "expenses":
                                expenses.append(record)
                            else:
                                income.append(record)

                    except (ValueError, IndexError):
                        # Skip invalid rows
                        continue

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
