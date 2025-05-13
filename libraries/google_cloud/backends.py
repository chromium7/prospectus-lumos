import mimetypes
from pathlib import Path
from typing import Any, Iterable, List, Optional, IO

# Google drive
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build as build_google_api_client
from googleapiclient.http import MediaFileUpload, MediaIoBaseUpload

from .tuples import File


class BaseBackend:
    """
    Data import backend to help simplify managing files in directory/storage
    """

    def list_files(self, path: str, extensions: Optional[Iterable[str]] = None) -> List[File]:
        """
        path: str -> path like string, ex folder-one/folder-two
        extensions: Itrable[str] -> Filtering files returned by it's extensions, without dot, ex: ['pdf', 'jpg']
        """
        raise NotImplementedError("Backend not implemented: get_files")

    def upload_file(
        self,
        target_path: str,
        source_file: Optional[IO] = None,
        source_file_path: Optional[Path] = None,
        *args: Any,
        **kwargs: Any,
    ) -> File:
        """
        Upload a file to a specified target_path.
        target_path can be contain filename only, meaning it will be uploaded to root path
        if the account file has permission.

        target_path: str -> path like file string, ex: folder-one/file.pdf
        source_file: IO -> file in format python IO(BytesIO, StringIO, etc).
        source_file_path: Path -> python Path object to determine file location in local storage
        """
        raise NotImplementedError("Backend not implemented: upload_file")

    def download(self, file: File) -> bytes:
        raise NotImplementedError("Backend not implemented: download")

    def save_to_local_dir(self, key: str, target_path: str) -> None:
        raise NotImplementedError("Backend not implemented: save_to_local_dir")

    @staticmethod
    def get_extension(filename: str) -> str:
        return Path(filename).suffix.replace(".", "")

    def delete_file(self, key: str) -> None:
        raise NotImplementedError("Backend not implemented: delete_file")

    def move_file(self, key: str, target_key: str) -> None:
        raise NotImplementedError("Backend not implemented: archive_file")

    def create_dirs(self, path: str) -> None:
        raise NotImplementedError("Backend not implemented: create_dirs")


class GoogleDriveBackend(BaseBackend):
    """
        Args:
            service_account_file (str): path-like string contain location of json
                                        formatted google service account credential
            scopes (optional, List[str]): List of scopes used to authenticate to google drive api.

    Attributes:
        _path_id_mapping (Optional[dict]): cached path to id mapping data
        creds (Credential): google drive credential object
        client (Client): main google drive client

    Note in GoogleDriveBackend extension is in mimetype format see: https://mimetype.io
    """

    _path_id_mapping: Optional[dict] = None

    def __init__(self, service_account_file: str, scopes: list[str] | None = None) -> None:
        if not scopes:
            scopes = ["https://www.googleapis.com/auth/drive"]
        self.creds = Credentials.from_service_account_file(service_account_file, scopes=scopes)
        self.client = build_google_api_client("drive", "v3", credentials=self.creds)

    def __del__(self) -> None:
        self.client.close()

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
        results = self.client.files().list(pageSize=max_size, fields="files(id, name, parents)", q=query).execute()
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
            self.client.files()
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

        result = self.client.files().create(body=file_metadata, media_body=media, fields="id,name,size").execute()
        return File(result["id"], result["name"], extension=self.get_extension(target_path), size=result["size"])

    def delete_file(self, key: str) -> None:
        self.client.files().delete(fileId=key).execute()
