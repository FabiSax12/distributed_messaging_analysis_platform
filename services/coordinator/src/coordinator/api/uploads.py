"""`POST /api/uploads`: presigned PUT URLs to upload files to the dataset bucket
from the dashboard, without passing the bytes through the coordinator."""

from __future__ import annotations

from pathlib import PurePosixPath

from fastapi import APIRouter, HTTPException
from ulid import ULID

from shared.api import UploadUrl, UploadUrlRequest, UploadUrlResponse
from shared.models import upload_key

from .deps import S3Dep, SettingsDep

router = APIRouter(tags=["uploads"])

_MAX_FILENAME_LENGTH = 255


@router.post("/uploads", response_model=UploadUrlResponse, status_code=201)
def create_upload_urls(
    payload: UploadUrlRequest,
    settings: SettingsDep,
    s3: S3Dep,
) -> UploadUrlResponse:
    upload_id = str(ULID())

    urls = [
        _presigned_upload(
            s3, settings.DATASET_BUCKET, settings.PRESIGN_EXPIRES_S, upload_id, name
        )
        for name in payload.filenames
    ]
    return UploadUrlResponse(upload_id=upload_id, urls=urls)


def _presigned_upload(
    s3, bucket: str, expires_in: int, upload_id: str, filename: str
) -> UploadUrl:
    safe_name = _sanitize_filename(filename)
    key = upload_key(upload_id, safe_name)
    url = s3.generate_presigned_url(
        "put_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=expires_in
    )
    return UploadUrl(filename=safe_name, input_key=key, url=url, expires_in=expires_in)


def _sanitize_filename(filename: str) -> str:
    """Basename only: rejects paths (`..`, `/`), empty or too-long names.

    `PurePosixPath(name).name` already drops any directory component
    (`a/../b` -> `b`, `/etc/passwd` -> `passwd`), but a name that is
    LITERALLY `..` or `.` survives `.name` unchanged (it is not a separator,
    it is "the name"), so it is rejected separately.
    """
    name = PurePosixPath(filename).name
    if not name or name in (".", "..") or len(name) > _MAX_FILENAME_LENGTH:
        raise HTTPException(
            status_code=422, detail=f"invalid file name: {filename!r}"
        )
    return name
