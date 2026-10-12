from typing import Any
from uuid import UUID

import boto3
from botocore.exceptions import ClientError
from fastapi import UploadFile

from auth import BUCKET, S3_ACCESS, S3_SECRET

s3 = boto3.client(
    "s3",
    endpoint_url="http://localhost:9000",
    aws_access_key_id=S3_ACCESS,
    aws_secret_access_key=S3_SECRET,
)


def init_bucket():
    try:
        s3.create_bucket(Bucket=BUCKET)
        print(f"bucket {BUCKET} succesfully created")
    except s3.exceptions.BucketAlreadyOwnedByYou:
        print(f"bucket {BUCKET} already created")


def download_file_obj(key: str) -> Any | None:
    try:
        response_file = s3.get_object(Bucket=BUCKET, Key=key)
        return response_file
    except ClientError:
        return None


def upload_file_obj(file: UploadFile, file_id: UUID):
    object_name = f"{file.filename}-{file_id}"
    s3.upload_fileobj(
        file.file, BUCKET, object_name, ExtraArgs={"ContentType": file.content_type}
    )
