from io import BytesIO, StringIO
from pathlib import Path
from typing import Optional, Union

from django.conf import settings
from django.utils.text import slugify

from .backends import S3StorageBackend


def upload_to_s3(name: str, buffer: Optional[Union[str, bytes, StringIO, BytesIO]] = None) -> str:
    s3_uploader = S3StorageBackend(
        access_key_id=settings.S3_CREDENTIALS['AWS_ACCESS_KEY_ID'],
        session_token=settings.S3_CREDENTIALS['AWS_SECRET_ACCESS_KEY'],
        bucket_name=settings.S3_CREDENTIALS['MEDIA_BUCKET_NAME'],
    )

    name_path = Path(name)
    file_name = slugify(name_path.stem) + name_path.suffix
    s3_file = s3_uploader.upload_file(file_name, source_file=buffer, acl='public-read')  # type: ignore
    return s3_file.name
