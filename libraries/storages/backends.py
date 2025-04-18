import mimetypes
import os
from contextlib import contextmanager
from io import BytesIO, StringIO
from pathlib import Path
from shutil import copyfile, copyfileobj
from typing import Any, Iterable, List, Optional, Generator, IO
from stat import S_ISREG

from django.core.exceptions import ImproperlyConfigured

import paramiko
from boto3.session import Session
from botocore.client import Config
from botocore.handlers import set_list_objects_encoding_type_url

# Google drive
# from google.oauth2.service_account import Credentials
# from googleapiclient.discovery import build as build_google_api_client
# from googleapiclient.http import MediaFileUpload, MediaIoBaseUpload

from .tuples import File


class BaseBackend:
    '''
    Data import backend to help simplify managing files in directory/storage
    '''

    def list_files(self, path: str, extensions: Optional[Iterable[str]] = None) -> List[File]:
        '''
            path: str -> path like string, ex folder-one/folder-two
            extensions: Itrable[str] -> Filtering files returned by it's extensions, without dot, ex: ['pdf', 'jpg']
        '''
        raise NotImplementedError('Backend not implemented: get_files')

    def upload_file(self, target_path: str, source_file: Optional[IO] = None,
                    source_file_path: Optional[Path] = None, *args: Any, **kwargs: Any) -> File:
        '''
            Upload a file to a specified target_path.
            target_path can be contain filename only, meaning it will be uploaded to root path
            if the account file has permission.

            target_path: str -> path like file string, ex: folder-one/file.pdf
            source_file: IO -> file in format python IO(BytesIO, StringIO, etc).
            source_file_path: Path -> python Path object to determine file location in local storage
        '''
        raise NotImplementedError('Backend not implemented: upload_file')

    def download(self, file: File) -> bytes:
        raise NotImplementedError('Backend not implemented: download')

    def save_to_local_dir(self, key: str, target_path: str) -> None:
        raise NotImplementedError('Backend not implemented: save_to_local_dir')

    @staticmethod
    def get_extension(filename: str) -> str:
        return Path(filename).suffix.replace('.', '')

    def delete_file(self, key: str) -> None:
        raise NotImplementedError('Backend not implemented: delete_file')

    def move_file(self, key: str, target_key: str) -> None:
        raise NotImplementedError('Backend not implemented: archive_file')

    def create_dirs(self, path: str) -> None:
        raise NotImplementedError('Backend not implemented: create_dirs')


class SFTPBackend(BaseBackend):

    def __init__(self, host: str, user: str, password: Optional[str] = None,
                 key_path: Optional[str] = None, port: int = 22) -> None:
        self.ssh_client = paramiko.SSHClient()
        if not password and not key_path:
            raise ImproperlyConfigured('Must provide either password or key_path')

        self.connection_kwargs = {'hostname': host, 'username': user, 'port': port}
        if password:
            self.connection_kwargs['password'] = password
        if key_path:
            key = paramiko.RSAKey.from_private_key_file(key_path)
            self.connection_kwargs['pkey'] = key

    @contextmanager
    def connect(self) -> Generator:
        try:
            self.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self.ssh_client.connect(**self.connection_kwargs)
            yield self.ssh_client.open_sftp()
        finally:
            self.ssh_client.close()

    def list_files(self, path: str, extensions: Optional[Iterable[str]] = None) -> List[File]:
        sftp: paramiko.SFTPClient
        files: List[File] = []
        with self.connect() as sftp:
            entries = sftp.listdir_attr(path)
            for entry in entries:
                # check if its a file or directory
                if not S_ISREG(entry.st_mode):
                    continue
                extension = self.get_extension(entry.filename)
                if extensions and not (extension in extensions):
                    continue
                files.append(File(Path(path, entry.filename).as_posix(), entry.filename,
                                  extension, entry.st_size))
        return files

    def upload_file(self, target_path: str, source_file: Optional[IO] = None,  # type: ignore
                    source_file_path: Optional[Path] = None) -> File:
        if not source_file and not source_file_path:
            raise ValueError('File or filename must be provided')

        sftp: paramiko.SFTPClient
        with self.connect() as sftp:
            if source_file:
                file = sftp.putfo(source_file, target_path)
            elif source_file_path:
                file = sftp.put(source_file_path.absolute().as_posix(), target_path)
        return File(target_path, Path(target_path).name, self.get_extension(target_path), file.st_size)


class GoogleDriveBackend(BaseBackend):
    '''
        Args:
            service_account_file (str): path-like string contain location of json
                                        formatted google service account credential
            scopes (optional, List[str]): List of scopes used to authenticate to google drive api.

    Attributes:
        _path_id_mapping (Optional[dict]): cached path to id mapping data
        creds (Credential): google drive credential object
        client (Client): main google drive client

    Note in GoogleDriveBackend extension is in mimetype format see: https://mimetype.io
    '''
    _path_id_mapping: Optional[dict] = None

    def __init__(self, service_account_file: str, scopes: List[str] = ['https://www.googleapis.com/auth/drive']) -> None:
        self.creds = Credentials.from_service_account_file(service_account_file, scopes=scopes)
        self.client = build_google_api_client('drive', 'v3', credentials=self.creds)

    def __del__(self) -> None:
        self.client.close()

    def get_path_to_id_mapping(self, max_size: int = 100) -> dict:
        '''
            will return dict of path to it's id mapping, limited to 100 keys
            example: {
                'folder-one/folder-one-one': 'abcd11',
                'folder-one/folder-one-two': 'abcd12',
                'folder-two': 'abcd20'
            }
        '''
        if self._path_id_mapping:
            return self._path_id_mapping

        query = "trashed = false and mimeType = 'application/vnd.google-apps.folder'"
        results = self.client\
            .files()\
            .list(pageSize=max_size, fields="files(id, name, parents)", q=query)\
            .execute()
        folders = results.get('files', [])

        id_to_folder_data = {folder['id']: folder for folder in folders}

        path_to_id_mapping = {}
        for folder in id_to_folder_data.values():
            path = folder['name']
            if 'parents' not in folder or not folder['parents']:
                path_to_id_mapping[path] = folder['id']
                continue

            parent_id = folder['parents'][0]
            parent_data = id_to_folder_data[parent_id]

            path = Path(parent_data['name'], path).as_posix()
            while 'parents' in parent_data and parent_data['parents']:
                parent_id = parent_data['parents'][0]
                parent_data = id_to_folder_data[parent_id]
                path = Path(parent_data['name'], path).as_posix()
            path_to_id_mapping[path] = folder['id']

        self._path_id_mapping = path_to_id_mapping
        return path_to_id_mapping

    def list_files(self, path: str, extensions: Optional[Iterable[str]] = None,
                   page_size: int = 500) -> List[File]:
        # exclude trashed and folders
        query = "trashed = false and mimeType != 'application/vnd.google-apps.folder'"

        if path and path != '.':
            path_id_mapping = self.get_path_to_id_mapping()
            path_id = path_id_mapping.get(path)
            if not path_id:
                return []
            query += f" and '{path_id}' in parents"

        if extensions:
            for extension in extensions:
                mimetype = mimetypes.guess_type(f'file.{extension}')[0]
                if not mimetype:
                    raise ValueError(f'Unknown mimetype for extension: {extension}')
                query += f" and mimeType='{mimetype}'"

        response = self.client.files()\
            .list(q=query, fields='nextPageToken, files(id, name, size)', pageSize=page_size)\
            .execute()

        files = []
        for file_data in response.get('files', []):
            files.append(
                File(file_data['id'], file_data['name'],
                     self.get_extension(file_data['name']), file_data['size'])
            )
        return files

    def upload_file(self, target_path: str, source_file: Optional[IO] = None,  # type: ignore
                    source_file_path: Optional[str] = None) -> File:
        if not source_file and not source_file_path:
            raise ValueError('file and filename must be provided')

        path = Path(target_path)

        # try to guess mimetype from file name
        mimetype = mimetypes.guess_type(path.name)[0]
        if not mimetype:
            raise ValueError(f'Failed to guess mimetype for file {path.name}')

        file_metadata: dict = {'name': path.name}

        parent = path.parent.as_posix()
        if parent != '.':
            path_id_mapping = self.get_path_to_id_mapping()
            path_id = path_id_mapping.get(parent)
            if not path_id:
                raise ValueError(f'path {parent} not found')

            file_metadata['parents'] = [path_id]

        if source_file:
            media = MediaIoBaseUpload(source_file, mimetype=mimetype)
        else:
            media = MediaFileUpload(source_file_path, mimetype=mimetype)

        result = self.client.files()\
            .create(body=file_metadata, media_body=media, fields="id,name,size")\
            .execute()
        return File(result['id'], result['name'], extension=self.get_extension(target_path), size=result['size'])

    def delete_file(self, key: str) -> None:
        self.client.files().delete(fileId=key).execute()


class S3StorageBackend(BaseBackend):
    '''
    S3StorageBackend is backed by boto and compatible with S3, Backblaze, Cloudflare, Azure, and Google Cloud Storage.

    Args:
        access_key_id (str): Access ID from service account HMAC key
        session_token (str): base64 encoded secret from service account HMAC key

    For Google Cloud Storage, see https://cloud.google.com/storage/docs/authentication/managing-hmackeys
    '''
    def __init__(self, access_key_id: str, session_token: str, bucket_name: str, endpoint: str,
                 prefix: str = '') -> None:
        # https://gist.github.com/gleicon/2b8acb9f9c0f22753eaac227ff997b34
        session = Session(aws_access_key_id=access_key_id,
                          aws_secret_access_key=session_token)
        session.events.unregister('before-parameter-build.s3.ListObjects',  # type: ignore
                                  set_list_objects_encoding_type_url)
        s3 = session.resource('s3', endpoint_url=endpoint, config=Config(signature_version='s3v4'))
        if prefix and not prefix.endswith('/'):
            prefix += '/'
        self.prefix = prefix
        self.bucket = s3.Bucket(bucket_name)

    def list_files(self, path: str, extensions: Optional[Iterable[str]] = None) -> List[File]:
        object_summaries = self.bucket.objects.filter(Prefix=self.prefix + path)

        files = []
        for object_summary in object_summaries:
            file = File(key=object_summary.key, extension=BaseBackend.get_extension(object_summary.key),
                        name=Path(object_summary.key).name, size=object_summary.size)
            if extensions:
                is_invalid_extension = True
                for extension in extensions:
                    if file.extension == extension:
                        is_invalid_extension = False
                        break
                if is_invalid_extension:
                    continue

            files.append(file)
        return files

    def save_to_local_dir(self, key: str, target_path: str) -> None:
        s3_object = self.bucket.Object(key)
        s3_object.download_file(target_path)

    def upload_file(self, target_path: str, source_file: Optional[IO] = None,  # type: ignore
                    source_file_path: Optional[Path] = None, acl: Optional[str] = None) -> File:
        if not source_file and not source_file_path:
            raise ValueError('file or filename must be provided')

        s3_object = self.bucket.Object(target_path)

        if acl:
            extra_args = {
                'ACL': acl
            }
        else:
            extra_args = None

        if source_file:
            # StringIO can't be hashed, so we need to make it into as a BytesIO
            if isinstance(source_file, StringIO):
                bytes = BytesIO(source_file.getvalue().encode())
                s3_object.upload_fileobj(bytes, ExtraArgs=extra_args)
            else:
                s3_object.upload_fileobj(source_file, ExtraArgs=extra_args)
        else:
            s3_object.upload_file(os.path.abspath(source_file_path), ExtraArgs=extra_args)

        return File(key=s3_object.key, extension=BaseBackend.get_extension(s3_object.key),
                    name=Path(s3_object.key).name, size=s3_object.content_length)

    def delete_file(self, key: str) -> None:
        blob = self.bucket.Object(key)
        blob.delete()

    def move_file(self, key: str, target_key: str) -> None:
        s3_object = self.bucket.Object(key)
        s3_object.copy_from(CopySource={'Bucket': self.bucket.name, 'Key': key},
                            Key=target_key)
        s3_object.delete()


class LocalStorageBackend(BaseBackend):
    '''
    LocalStorageBackend is backed by the local filesystem.
    '''

    def list_files(self, path: str, extensions: Optional[Iterable[str]] = None) -> List[File]:
        py_path = Path(path)
        files: List[File] = []
        if not py_path.exists():
            return files
        for file_path in py_path.iterdir():
            if file_path.is_file():
                file = File(key=file_path.as_posix(), extension=self.get_extension(file_path.as_posix()),
                            name=file_path.name, size=file_path.stat().st_size)
                if extensions:
                    is_invalid_extension = True
                    for extension in extensions:
                        if file.extension == extension:
                            is_invalid_extension = False
                            break
                    if is_invalid_extension:
                        continue

                files.append(file)
        return files

    def save_to_local_dir(self, key: str, target_path: str) -> None:
        copyfile(key, target_path)

    def upload_file(self, target_path: str, source_file: Optional[IO] = None,  # type: ignore
                    source_file_path: Optional[Path] = None) -> File:
        if not source_file and not source_file_path:
            raise ValueError('file or filename must be provided')

        self.create_dirs(target_path)
        target_py_path = Path(target_path)

        if source_file and isinstance(source_file.read(0), bytes):
            with open(target_py_path.as_posix(), 'wb') as fb:
                copyfileobj(source_file, fb)
        elif source_file and isinstance(source_file.read(0), str):
            with open(target_py_path.as_posix(), 'w') as fs:
                copyfileobj(source_file, fs)
        elif source_file_path:
            copyfile(source_file_path.as_posix(), target_path)

        return File(key=target_py_path.as_posix(), extension=self.get_extension(target_py_path.as_posix()),
                    name=target_py_path.name, size=target_py_path.stat().st_size)

    def delete_file(self, key: str) -> None:
        file_path = Path(key)
        if file_path.exists():
            file_path.unlink()

    def move_file(self, key: str, target_key: str) -> None:
        # auto create dirs
        os.makedirs(os.path.dirname(target_key), exist_ok=True)

        file_path = Path(key)
        target_path = Path(target_key)
        if file_path.exists():
            file_path.rename(target_path)

    def create_dirs(self, path: str) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
