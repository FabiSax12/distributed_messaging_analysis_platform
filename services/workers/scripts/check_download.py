"""Worker download/CAS trust-boundary checks without external storage."""

import hashlib
import io
import tempfile
from pathlib import Path
from types import SimpleNamespace

from botocore.exceptions import ClientError

from shared.messages import ErrorCode
from workers.ops.base import OpError
from workers.runner.process import _download


class Storage:
    def __init__(self, data):
        self.data = data
        self.objects = {}

    def get_object(self, **kwargs):
        return {"ContentLength": len(self.data), "Body": io.BytesIO(self.data)}

    def put_object(self, **kwargs):
        assert kwargs["IfNoneMatch"] == "*"
        key = kwargs["Key"]
        if key in self.objects:
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
        self.objects[key] = kwargs["Body"].read()


def main():
    data = b"synthetic evidence" * 1000
    storage = Storage(data)
    context = SimpleNamespace(s3=storage, bucket="synthetic")
    digest = hashlib.sha256(data).hexdigest()
    with tempfile.TemporaryDirectory() as directory:
        out = Path(directory) / "source.bin"
        _download(context, "uploads/source.bin", out, digest)
        assert out.read_bytes() == data and storage.objects["content/" + digest] == data
        _download(context, "uploads/duplicate.bin", out, digest)
        assert len(storage.objects) == 1
        try:
            _download(context, "uploads/source.bin", out, "0" * 64)
        except OpError as exc:
            assert exc.code == ErrorCode.CORRUPT_INPUT
        else:
            raise AssertionError("Incorrect source hash was accepted")
    print("Worker chunk download, source integrity and conditional CAS checks passed")


if __name__ == "__main__":
    main()
