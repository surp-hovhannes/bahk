"""Persistent capability flow for dedicated devotional-video uploads."""

import os
import re
import uuid
from datetime import timedelta
from xml.sax.saxutils import escape

from django.core import signing
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.utils import timezone

from .models import DevotionalVideoUpload, Video
from .utils import generate_unique_filename


MIN_FILE_SIZE = 1024 * 1024
MAX_FILE_SIZE = 500 * 1024 * 1024
SESSION_MAX_AGE = 60 * 60
TOKEN_MAX_AGE = 24 * 60 * 60
SESSION_SALT = "learning-resources.devotional-video-upload-session"
TOKEN_SALT = "learning-resources.devotional-video-upload-token"
DEDICATED_PREFIX = "videos/devotional-cli/"
ALLOWED_TYPES = {".mp4": "video/mp4", ".webm": "video/webm"}
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,199}$")


class UploadError(Exception):
    pass


def validate_file(file_name, file_size, content_type):
    if not isinstance(file_name, str) or not SAFE_NAME.fullmatch(file_name):
        raise UploadError("file_name must be a safe basename of at most 200 characters.")
    if os.path.basename(file_name) != file_name or any(ord(char) < 32 for char in file_name):
        raise UploadError("file_name must be a safe basename of at most 200 characters.")
    if isinstance(file_size, bool) or not isinstance(file_size, int):
        raise UploadError("file_size must be an integer.")
    if not MIN_FILE_SIZE <= file_size <= MAX_FILE_SIZE:
        raise UploadError("file_size must be between 1 MiB and 500 MiB.")
    if ALLOWED_TYPES.get(os.path.splitext(file_name)[1].lower()) != content_type:
        raise UploadError("Only exact .mp4 + video/mp4 and .webm + video/webm pairs are supported.")


def _signed_nonce(value, *, salt, max_age, user_id):
    try:
        payload = signing.loads(value, salt=salt, max_age=max_age)
    except signing.BadSignature as exc:
        raise UploadError("Upload capability is invalid or expired.") from exc
    if payload.get("user_id") != user_id or payload.get("purpose") != "video.video":
        raise UploadError("Upload capability does not belong to this user or field.")
    return payload.get("nonce")


def _get_upload(value, *, salt, max_age, user_id, lock=False):
    nonce = _signed_nonce(value, salt=salt, max_age=max_age, user_id=user_id)
    queryset = DevotionalVideoUpload.objects.select_for_update() if lock else DevotionalVideoUpload.objects
    try:
        upload = queryset.get(nonce=nonce, owner_id=user_id)
    except DevotionalVideoUpload.DoesNotExist as exc:
        raise UploadError("Upload capability is invalid, expired, or already used.") from exc
    if upload.expires_at <= timezone.now():
        raise UploadError("Upload capability is invalid or expired.")
    return upload


class MultipartUploadAdapter:
    """Small mockable boundary over the django-storages S3 client contract."""

    def __init__(self):
        field = Video._meta.get_field("video")
        self.field = field
        self.storage = field.storage
        try:
            self.client = self.storage.connection.meta.client
            self.bucket_name = self.storage.bucket_name
        except AttributeError as exc:
            raise ImproperlyConfigured("Devotional direct upload requires S3-backed storage.") from exc

    def initialize(self, *, file_name, file_size, content_type):
        key = DEDICATED_PREFIX + generate_unique_filename(None, file_name)
        response = self.client.create_multipart_upload(
            Bucket=self.bucket_name, Key=key, ContentType=content_type
        )
        upload_id = response["UploadId"]
        try:
            url = self.client.generate_presigned_url(
                "upload_part",
                Params={"Bucket": self.bucket_name, "Key": key, "UploadId": upload_id,
                        "PartNumber": 1, "ContentLength": file_size},
                ExpiresIn=SESSION_MAX_AGE,
            )
        except Exception:
            self.abort(key=key, upload_id=upload_id)
            raise
        return {"key": key, "upload_id": upload_id,
                "parts": [{"part_number": 1, "size": file_size, "url": url}]}

    def completion_request(self, *, key, upload_id, parts):
        aws_parts = [{"PartNumber": p["part_number"], "ETag": p["etag"]} for p in parts]
        url = self.client.generate_presigned_url(
            "complete_multipart_upload",
            Params={"Bucket": self.bucket_name, "Key": key, "UploadId": upload_id},
            ExpiresIn=SESSION_MAX_AGE,
        )
        body = '<?xml version="1.0" encoding="UTF-8"?>'
        body += '<CompleteMultipartUpload xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
        body += "".join(
            f"<Part><PartNumber>{p['PartNumber']}</PartNumber><ETag>{escape(p['ETag'])}</ETag></Part>"
            for p in aws_parts
        ) + "</CompleteMultipartUpload>"
        return {"url": url, "body": body}

    def abort(self, *, key, upload_id):
        try:
            self.client.abort_multipart_upload(
                Bucket=self.bucket_name, Key=key, UploadId=upload_id
            )
        except Exception:
            pass

    def abort_for_cleanup(self, *, key, upload_id):
        self.client.abort_multipart_upload(Bucket=self.bucket_name, Key=key, UploadId=upload_id)

    def delete_object(self, *, key):
        self.client.delete_object(Bucket=self.bucket_name, Key=key)

    def object_size(self, *, key):
        return self.client.head_object(Bucket=self.bucket_name, Key=key)["ContentLength"]


def initialize_upload(*, user_id, file_name, file_size, content_type, adapter=None):
    validate_file(file_name, file_size, content_type)
    upload_adapter = adapter or MultipartUploadAdapter()
    try:
        result = upload_adapter.initialize(
            file_name=file_name, file_size=file_size, content_type=content_type
        )
    except Exception as exc:
        raise UploadError("Upload could not be initialized; try a fresh upload.") from exc
    if not result["key"].startswith(DEDICATED_PREFIX):
        upload_adapter.abort(key=result["key"], upload_id=result["upload_id"])
        raise UploadError("Storage generated an invalid devotional video key.")
    nonce = uuid.uuid4().hex
    try:
        DevotionalVideoUpload.objects.create(
            nonce=nonce, owner_id=user_id, storage_key=result["key"], file_name=file_name,
            expected_size=file_size, content_type=content_type, upload_id=result["upload_id"],
            expires_at=timezone.now() + timedelta(seconds=SESSION_MAX_AGE),
        )
    except Exception:
        upload_adapter.abort(key=result["key"], upload_id=result["upload_id"])
        raise
    session = signing.dumps(
        {"nonce": nonce, "user_id": user_id, "purpose": "video.video"}, salt=SESSION_SALT
    )
    return {"upload_session": session, "upload_id": result["upload_id"], "parts": result["parts"]}


def complete_upload(*, user_id, upload_session, upload_id, parts, adapter=None):
    preparation_error = None
    aborted_upload = None
    with transaction.atomic():
        upload = _get_upload(upload_session, salt=SESSION_SALT, max_age=SESSION_MAX_AGE,
                             user_id=user_id, lock=True)
        if (upload.state != DevotionalVideoUpload.State.INITIALIZED
                or upload.completion_requested_at is not None):
            raise UploadError("Upload completion has already been requested.")
        if upload_id != upload.upload_id:
            raise UploadError("upload_id does not match this upload session.")
        if not isinstance(parts, list) or not parts:
            raise UploadError("parts must be a non-empty list.")
        total = 0
        for expected, part in enumerate(parts, start=1):
            if (not isinstance(part, dict) or set(part) != {"part_number", "size", "etag"}
                    or part["part_number"] != expected):
                raise UploadError("parts must be ordered, contiguous, and contain only part_number, size, and etag.")
            if isinstance(part["size"], bool) or not isinstance(part["size"], int) or part["size"] < 1:
                raise UploadError("Each part size must be a positive integer.")
            if not isinstance(part["etag"], str) or not part["etag"]:
                raise UploadError("Each part must have an etag.")
            total += part["size"]
        if total != upload.expected_size:
            raise UploadError("Part sizes do not match the initiated file size.")
        upload_adapter = adapter or MultipartUploadAdapter()
        try:
            result = upload_adapter.completion_request(
                key=upload.storage_key, upload_id=upload.upload_id, parts=parts
            )
        except Exception as exc:
            # Commit the terminal state before the irreversible abort. If the
            # process stops after this commit, the expired cleanup path can
            # still reconcile the abandoned multipart upload later.
            upload.state = DevotionalVideoUpload.State.CLEANED
            upload.cleaned_at = None
            upload.save(update_fields=["state", "cleaned_at"])
            preparation_error = exc
            aborted_upload = (upload.storage_key, upload.upload_id)
        else:
            upload.completion_requested_at = timezone.now()
            upload.save(update_fields=["completion_requested_at"])
    if preparation_error is not None:
        upload_adapter.abort(key=aborted_upload[0], upload_id=aborted_upload[1])
        raise UploadError(
            "Upload completion could not be prepared; start a fresh upload."
        ) from preparation_error
    return result


def finalize_upload(*, user_id, upload_session, adapter=None):
    size_mismatch = False
    aborted_upload = None
    with transaction.atomic():
        upload = _get_upload(upload_session, salt=SESSION_SALT, max_age=SESSION_MAX_AGE,
                             user_id=user_id, lock=True)
        if upload.state != DevotionalVideoUpload.State.INITIALIZED or not upload.completion_requested_at:
            raise UploadError("Upload must be completed before it can be finalized.")
        upload_adapter = adapter or MultipartUploadAdapter()
        try:
            size = upload_adapter.object_size(key=upload.storage_key)
        except Exception as exc:
            raise UploadError("The completed object is not available.") from exc
        if size != upload.expected_size:
            # Persist a terminal, recoverable cleanup lease before touching S3.
            # A crash before or during abort leaves this row eligible for the
            # cleanup command, which also removes a completed object.
            upload.state = DevotionalVideoUpload.State.CLEANED
            upload.cleaned_at = None
            upload.save(update_fields=["state", "cleaned_at"])
            size_mismatch = True
            aborted_upload = (upload.storage_key, upload.upload_id)
        else:
            upload.state = DevotionalVideoUpload.State.READY
            upload.expires_at = timezone.now() + timedelta(seconds=TOKEN_MAX_AGE)
            upload.save(update_fields=["state", "expires_at"])
            token = signing.dumps(
                {"nonce": upload.nonce, "user_id": user_id, "purpose": "video.video"},
                salt=TOKEN_SALT,
            )
            result = {"upload_token": token, "file_name": upload.file_name, "file_size": size}
    if size_mismatch:
        upload_adapter.abort(key=aborted_upload[0], upload_id=aborted_upload[1])
        raise UploadError(
            "Completed object size does not match the initiated file size; start a fresh upload."
        )
    return result


def attach_upload_token(token, *, user_id, save):
    """Lock a ready upload, persist its Video, then atomically mark it attached."""
    with transaction.atomic():
        upload = _get_upload(token, salt=TOKEN_SALT, max_age=TOKEN_MAX_AGE,
                             user_id=user_id, lock=True)
        if upload.state != DevotionalVideoUpload.State.READY:
            raise UploadError("Upload token has already been used.")
        if not upload.storage_key.startswith(DEDICATED_PREFIX):
            raise UploadError("Upload token contains an invalid video key.")
        result = save(upload.storage_key)
        upload.state = DevotionalVideoUpload.State.ATTACHED
        upload.attached_at = timezone.now()
        upload.save(update_fields=["state", "attached_at"])
        return result


def consume_upload_token(token, *, user_id):
    """Compatibility validator; new persistence must use attach_upload_token."""
    upload = _get_upload(token, salt=TOKEN_SALT, max_age=TOKEN_MAX_AGE, user_id=user_id)
    if upload.state != DevotionalVideoUpload.State.READY:
        raise UploadError("Upload token has already been used.")
    return upload.storage_key
