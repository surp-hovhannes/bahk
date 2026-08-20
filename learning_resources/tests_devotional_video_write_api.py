from unittest.mock import Mock, patch
from datetime import timedelta
from io import StringIO
import uuid

from django.contrib.auth import get_user_model
from django.core import signing
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import connection
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from learning_resources.devotional_video_uploads import (
    MultipartUploadAdapter, UploadError, attach_upload_token, complete_upload,
    consume_upload_token, finalize_upload, initialize_upload, validate_file,
)
from learning_resources.models import DevotionalVideoUpload, Video


class FakeMultipartAdapter:
    def __init__(self, size=1024 * 1024):
        self.size = size
        self.aborted = []

    def initialize(self, **kwargs):
        return {"key": "videos/devotional-cli/server-name.mp4", "upload_id": "aws-id",
                "parts": [{"part_number": 1, "size": kwargs["file_size"],
                           "url": "https://s3.invalid/part"}]}

    def completion_request(self, **kwargs):
        return {"url": "https://s3.invalid/complete", "body": "<xml/>"}

    def object_size(self, **kwargs):
        return self.size

    def abort(self, **kwargs):
        self.aborted.append(kwargs)


class MultipartUploadAdapterTests(SimpleTestCase):
    def test_constructor_uses_django_storages_s3_resource_contract(self):
        field = Mock()
        expected_client = field.storage.connection.meta.client
        field.storage.bucket_name = "video-bucket"
        with patch.object(Video._meta, "get_field", return_value=field):
            adapter = MultipartUploadAdapter()

        self.assertIs(adapter.client, expected_client)
        self.assertEqual(adapter.bucket_name, "video-bucket")

    def setUp(self):
        self.client = Mock()
        self.client.create_multipart_upload.return_value = {"UploadId": "aws-id"}
        self.client.generate_presigned_url.return_value = "https://s3.invalid/presigned"
        self.adapter = MultipartUploadAdapter.__new__(MultipartUploadAdapter)
        self.adapter.client = self.client
        self.adapter.bucket_name = "video-bucket"
        self.adapter.field = Mock()
        self.adapter.field.generate_filename.return_value = "videos/server-name.mp4"

    def test_part_presign_is_bound_to_initiated_size(self):
        with patch("learning_resources.devotional_video_uploads.generate_unique_filename",
                   return_value="server-name.mp4"):
            result = self.adapter.initialize(
                file_name="day.mp4", file_size=7 * 1024 * 1024, content_type="video/mp4"
            )

        self.assertEqual(result["parts"][0]["size"], 7 * 1024 * 1024)
        self.client.generate_presigned_url.assert_called_once_with(
            "upload_part",
            Params={
                "Bucket": "video-bucket", "Key": "videos/devotional-cli/server-name.mp4",
                "UploadId": "aws-id", "PartNumber": 1, "ContentLength": 7 * 1024 * 1024,
            },
            ExpiresIn=60 * 60,
        )

    def test_completion_presign_excludes_payload_and_returns_upstream_xml(self):
        result = self.adapter.completion_request(
            key="videos/server-name.mp4", upload_id="aws-id",
            parts=[{"part_number": 1, "size": 123, "etag": '"normal-etag"'}],
        )

        self.client.generate_presigned_url.assert_called_once_with(
            "complete_multipart_upload",
            Params={"Bucket": "video-bucket", "Key": "videos/server-name.mp4",
                    "UploadId": "aws-id"},
            ExpiresIn=60 * 60,
        )
        self.assertEqual(
            result["body"],
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<CompleteMultipartUpload xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
            '<Part><PartNumber>1</PartNumber><ETag>"normal-etag"</ETag></Part>'
            '</CompleteMultipartUpload>',
        )

    def test_abort_uses_s3_client_contract_and_swallows_cleanup_errors(self):
        self.client.abort_multipart_upload.side_effect = RuntimeError("private S3 detail")
        self.adapter.abort(key="videos/server-name.mp4", upload_id="aws-id")
        self.client.abort_multipart_upload.assert_called_once_with(
            Bucket="video-bucket", Key="videos/server-name.mp4", UploadId="aws-id"
        )


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class UploadCapabilityTests(TestCase):
    size = 1024 * 1024

    def setUp(self):
        cache.clear()
        self.owner = get_user_model().objects.create_user("capability-owner")

    def test_file_policy_accepts_only_exact_pairs_and_safe_sizes(self):
        validate_file("day-1.mp4", self.size, "video/mp4")
        validate_file("day-1.webm", self.size, "video/webm")
        validate_file("largest.mp4", 500 * 1024 * 1024, "video/mp4")
        for args in [
            ("../bad.mp4", self.size, "video/mp4"),
            ("bad.mp4", self.size - 1, "video/mp4"),
            ("bad.mp4", 500 * 1024 * 1024 + 1, "video/mp4"),
            ("bad.mp4", self.size, "video/webm"),
            ("bad.mov", self.size, "video/quicktime"),
        ]:
            with self.assertRaises(UploadError):
                validate_file(*args)

    def _initialized(self, user_id=None):
        user_id = user_id or self.owner.pk
        return initialize_upload(user_id=user_id, file_name="day.mp4", file_size=self.size,
                                 content_type="video/mp4", adapter=FakeMultipartAdapter())

    def test_protocol_order_user_binding_size_and_one_time_token(self):
        user_id = self.owner.pk
        initiated = self._initialized(user_id)
        self.assertEqual(initiated["parts"][0]["size"], self.size)
        with self.assertRaises(UploadError):
            finalize_upload(user_id=user_id, upload_session=initiated["upload_session"], adapter=FakeMultipartAdapter())
        with self.assertRaises(UploadError):
            complete_upload(user_id=user_id + 1, upload_session=initiated["upload_session"],
                            upload_id="aws-id", parts=[] , adapter=FakeMultipartAdapter())
        completed = complete_upload(
            user_id=user_id, upload_session=initiated["upload_session"], upload_id="aws-id",
            parts=[{"part_number": 1, "size": self.size, "etag": '"etag"'}],
            adapter=FakeMultipartAdapter(),
        )
        self.assertEqual(set(completed), {"url", "body"})
        wrong_size_adapter = FakeMultipartAdapter(self.size + 1)
        with self.assertRaises(UploadError):
            finalize_upload(user_id=user_id, upload_session=initiated["upload_session"],
                            adapter=wrong_size_adapter)
        self.assertEqual(wrong_size_adapter.aborted, [
            {"key": "videos/devotional-cli/server-name.mp4", "upload_id": "aws-id"}
        ])

        # A terminal size mismatch invalidates that capability; retry from a
        # fresh upload rather than reusing a potentially inconsistent object.
        initiated = self._initialized(user_id)
        complete_upload(
            user_id=user_id, upload_session=initiated["upload_session"], upload_id="aws-id",
            parts=[{"part_number": 1, "size": self.size, "etag": '"etag"'}],
            adapter=FakeMultipartAdapter(),
        )
        finalized = finalize_upload(user_id=user_id, upload_session=initiated["upload_session"],
                                    adapter=FakeMultipartAdapter())
        self.assertNotIn("key", finalized)
        with self.assertRaises(UploadError):
            consume_upload_token(finalized["upload_token"], user_id=user_id + 1)
        self.assertEqual(consume_upload_token(finalized["upload_token"], user_id=user_id),
                         "videos/devotional-cli/server-name.mp4")
        self.assertEqual(
            attach_upload_token(finalized["upload_token"], user_id=user_id, save=lambda key: key),
            "videos/devotional-cli/server-name.mp4",
        )
        with self.assertRaises(UploadError):
            consume_upload_token(finalized["upload_token"], user_id=user_id)

    def test_expired_capability_is_rejected(self):
        initiated = self._initialized()
        with patch("learning_resources.devotional_video_uploads.signing.loads",
                   side_effect=signing.SignatureExpired):
            with self.assertRaises(UploadError):
                complete_upload(user_id=1, upload_session=initiated["upload_session"],
                                upload_id="aws-id", parts=[], adapter=FakeMultipartAdapter())

    def test_completion_preparation_failure_aborts_and_invalidates_session(self):
        initiated = self._initialized()
        adapter = FakeMultipartAdapter()
        adapter.completion_request = Mock(side_effect=RuntimeError("S3 detail"))

        with self.assertRaisesRegex(UploadError, "start a fresh upload"):
            complete_upload(
                user_id=self.owner.pk, upload_session=initiated["upload_session"], upload_id="aws-id",
                parts=[{"part_number": 1, "size": self.size, "etag": '"etag"'}],
                adapter=adapter,
            )
        self.assertEqual(adapter.aborted, [
            {"key": "videos/devotional-cli/server-name.mp4", "upload_id": "aws-id"}
        ])
        upload = DevotionalVideoUpload.objects.get(owner=self.owner)
        self.assertEqual(upload.state, DevotionalVideoUpload.State.CLEANED)
        self.assertIsNone(upload.cleaned_at)
        with self.assertRaises(UploadError):
            complete_upload(
                user_id=self.owner.pk, upload_session=initiated["upload_session"], upload_id="aws-id",
                parts=[{"part_number": 1, "size": self.size, "etag": '"etag"'}],
                adapter=adapter,
            )
        with self.assertRaises(UploadError):
            finalize_upload(
                user_id=self.owner.pk,
                upload_session=initiated["upload_session"],
                adapter=adapter,
            )
        self.assertEqual(adapter.completion_request.call_count, 1)


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}})
class FinalizeUploadFailureTests(TransactionTestCase):
    def test_size_mismatch_is_terminal_before_abort_runs_outside_transaction(self):
        size = 1024 * 1024
        owner = get_user_model().objects.create_user("finalize-failure-owner")
        initiated = initialize_upload(
            user_id=owner.pk,
            file_name="day.mp4",
            file_size=size,
            content_type="video/mp4",
            adapter=FakeMultipartAdapter(),
        )
        complete_upload(
            user_id=owner.pk,
            upload_session=initiated["upload_session"],
            upload_id="aws-id",
            parts=[{"part_number": 1, "size": size, "etag": '"etag"'}],
            adapter=FakeMultipartAdapter(),
        )
        upload = DevotionalVideoUpload.objects.get(owner=owner)
        adapter = FakeMultipartAdapter(size + 1)

        def assert_terminal_ledger(**kwargs):
            self.assertFalse(connection.in_atomic_block)
            upload.refresh_from_db()
            self.assertEqual(upload.state, DevotionalVideoUpload.State.CLEANED)
            self.assertIsNone(upload.cleaned_at)
            adapter.aborted.append(kwargs)

        adapter.abort = Mock(side_effect=assert_terminal_ledger)
        with self.assertRaisesRegex(UploadError, "start a fresh upload"):
            finalize_upload(
                user_id=owner.pk,
                upload_session=initiated["upload_session"],
                adapter=adapter,
            )

        adapter.abort.assert_called_once_with(
            key="videos/devotional-cli/server-name.mp4", upload_id="aws-id"
        )
        with self.assertRaisesRegex(UploadError, "completed before it can be finalized"):
            finalize_upload(
                user_id=owner.pk,
                upload_session=initiated["upload_session"],
                adapter=FakeMultipartAdapter(),
            )


@override_settings(
    MODELTRANS_AVAILABLE_LANGUAGES=["en", "hy"],
    CACHES={"default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}},
)
class DevotionalVideoWriteAPITests(APITestCase):
    def setUp(self):
        users = get_user_model()
        self.staff = users.objects.create_user("video-staff", is_staff=True)
        self.user = users.objects.create_user("video-user")
        self.video = Video.objects.create(title="Before", description="Before",
                                          category="devotional", language_code="en")
        self.payload = {"title": "New", "description": "Description",
                        "language_code": "en", "upload_token": "opaque"}

    def test_public_reads_remain_available_and_shaped_as_before(self):
        listing = self.client.get("/api/learning-resources/videos/?category=devotional")
        detail = self.client.get(f"/api/learning-resources/videos/{self.video.pk}/")
        self.assertEqual(listing.status_code, status.HTTP_200_OK)
        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        self.assertEqual(set(detail.data), {"id", "title", "description", "category", "thumbnail",
                                            "thumbnail_small_url", "video", "created_at", "updated_at",
                                            "is_bookmarked"})

    def test_writes_require_staff(self):
        for user, expected_status in (
            (None, status.HTTP_401_UNAUTHORIZED),
            (self.user, status.HTTP_403_FORBIDDEN),
        ):
            self.client.force_authenticate(user=user)
            response = self.client.post("/api/learning-resources/videos/", self.payload, format="json")
            self.assertEqual(response.status_code, expected_status)
        self.client.force_authenticate(user=None)

    @patch(
        "learning_resources.devotional_video_uploads.attach_upload_token",
        side_effect=lambda _token, *, user_id, save: save(
            "videos/devotional-cli/server.mp4"
        ),
    )
    def test_staff_create_forces_category_and_returns_public_shape(self, _consume):
        self.client.force_authenticate(user=self.staff)
        response = self.client.post("/api/learning-resources/videos/", self.payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        created = Video.objects.get(pk=response.data["id"])
        self.assertEqual(created.category, "devotional")
        self.assertEqual(created.video.name, "videos/devotional-cli/server.mp4")
        self.assertNotIn("upload_token", response.data)

    def test_create_rejects_forbidden_unknown_and_invalid_fields(self):
        self.client.force_authenticate(user=self.staff)
        for field, value in [("category", "general"), ("video", "videos/raw.mp4"),
                             ("id", 9), ("created_at", "now"), ("unknown", True)]:
            response = self.client.post("/api/learning-resources/videos/",
                                        {**self.payload, field: value}, format="json")
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, field)
        for changes in [{"title": "x" * 201}, {"description": ""}, {"language_code": "zz"}]:
            response = self.client.post("/api/learning-resources/videos/",
                                        {**self.payload, **changes}, format="json")
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_patch_metadata_clear_thumbnail_and_method_restrictions(self):
        self.client.force_authenticate(user=self.staff)
        response = self.client.patch(f"/api/learning-resources/videos/{self.video.pk}/",
                                     {"title": "After", "clear_thumbnail": True}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.video.refresh_from_db()
        self.assertEqual(self.video.title, "After")
        self.assertEqual(self.video.category, "devotional")
        self.assertEqual(self.client.patch(f"/api/learning-resources/videos/{self.video.pk}/", {}, format="json").status_code,
                         status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self.client.put(f"/api/learning-resources/videos/{self.video.pk}/", self.payload, format="json").status_code,
                         status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertEqual(self.client.delete(f"/api/learning-resources/videos/{self.video.pk}/").status_code,
                         status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_thumbnail_rules_and_non_devotional_patch(self):
        self.client.force_authenticate(user=self.staff)
        thumbnail = SimpleUploadedFile(
            "thumbnail.gif",
            (
                b"\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x00\x00\x00\x21\xf9\x04"
                b"\x01\x0a\x00\x01\x00\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02"
                b"\x02\x4c\x01\x00\x3b"
            ),
            content_type="image/gif",
        )
        both = self.client.patch(f"/api/learning-resources/videos/{self.video.pk}/",
                                 {"thumbnail": thumbnail, "clear_thumbnail": True},
                                 format="multipart")
        self.assertEqual(both.status_code, status.HTTP_400_BAD_REQUEST)
        general = Video.objects.create(title="G", description="G", category="general")
        self.assertEqual(self.client.patch(f"/api/learning-resources/videos/{general.pk}/",
                                          {"title": "No"}, format="json").status_code,
                         status.HTTP_404_NOT_FOUND)

    @patch("learning_resources.upload_views.finalize_upload", return_value={"upload_token": "t", "file_name": "day.mp4", "file_size": 1})
    @patch("learning_resources.upload_views.complete_upload", return_value={"url": "https://s3.invalid", "body": "<xml/>"})
    @patch("learning_resources.upload_views.initialize_upload")
    def test_upload_endpoints_are_staff_only_strict_and_post_only(self, initialize, _complete, _finalize):
        initialize.return_value = {"upload_session": "opaque", "upload_id": "id", "parts": []}
        endpoints = [
            ("initialize", {"file_name": "day.mp4", "file_size": 1024 * 1024, "content_type": "video/mp4"}),
            ("complete", {"upload_session": "opaque", "upload_id": "id", "parts": []}),
            ("finalize", {"upload_session": "opaque"}),
        ]
        for endpoint, payload in endpoints:
            url = f"/api/learning-resources/devotional-videos/uploads/{endpoint}/"
            self.assertIn(self.client.post(url, payload, format="json").status_code, {401, 403})
        self.client.force_authenticate(user=self.user)
        for endpoint, payload in endpoints:
            url = f"/api/learning-resources/devotional-videos/uploads/{endpoint}/"
            self.assertEqual(self.client.post(url, payload, format="json").status_code, 403)
        self.client.force_authenticate(user=self.staff)
        for endpoint, payload in endpoints:
            url = f"/api/learning-resources/devotional-videos/uploads/{endpoint}/"
            self.assertEqual(self.client.post(url, payload, format="json").status_code, 200)
            self.assertEqual(self.client.post(url, {**payload, "key": "raw"}, format="json").status_code, 400)
            self.assertEqual(self.client.get(url).status_code, 405)

    def _ready_upload_token(self, owner=None, key="videos/devotional-cli/ready.mp4"):
        owner = owner or self.staff
        upload = DevotionalVideoUpload.objects.create(
            nonce=f"nonce-{DevotionalVideoUpload.objects.count()}",
            owner=owner,
            storage_key=key,
            file_name="ready.mp4",
            expected_size=1024 * 1024,
            content_type="video/mp4",
            upload_id="aws-ready",
            state=DevotionalVideoUpload.State.READY,
            expires_at=timezone.now() + timedelta(hours=1),
        )
        token = signing.dumps(
            {"nonce": upload.nonce, "user_id": owner.pk, "purpose": "video.video"},
            salt="learning-resources.devotional-video-upload-token",
        )
        return upload, token

    def test_real_create_and_patch_attachment_mark_ledger_attached(self):
        self.client.force_authenticate(user=self.staff)
        created_upload, create_token = self._ready_upload_token()
        response = self.client.post(
            "/api/learning-resources/videos/",
            {**self.payload, "upload_token": create_token},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        created_upload.refresh_from_db()
        self.assertEqual(created_upload.state, DevotionalVideoUpload.State.ATTACHED)

        patch_upload, patch_token = self._ready_upload_token(
            key="videos/devotional-cli/replacement.mp4"
        )
        response = self.client.patch(
            f"/api/learning-resources/videos/{self.video.pk}/",
            {"upload_token": patch_token},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        patch_upload.refresh_from_db()
        self.assertEqual(patch_upload.state, DevotionalVideoUpload.State.ATTACHED)

    def test_persistence_failure_leaves_ready_ledger_for_cleanup(self):
        upload, token = self._ready_upload_token()
        with self.assertRaises(RuntimeError):
            attach_upload_token(
                token,
                user_id=self.staff.pk,
                save=lambda _key: (_ for _ in ()).throw(RuntimeError("save failed")),
            )
        upload.refresh_from_db()
        self.assertEqual(upload.state, DevotionalVideoUpload.State.READY)


class CleanupDevotionalVideoUploadsTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user("cleanup-owner")

    def _upload(self, *, key, state, expired=True):
        return DevotionalVideoUpload.objects.create(
            nonce=uuid.uuid4().hex,
            owner=self.owner,
            storage_key=key,
            file_name="orphan.mp4",
            expected_size=1024 * 1024,
            content_type="video/mp4",
            upload_id="upload-id",
            state=state,
            expires_at=timezone.now() + timedelta(hours=-1 if expired else 1),
        )

    @patch("learning_resources.management.commands.cleanup_devotional_video_uploads.MultipartUploadAdapter")
    def test_dry_run_execute_safety_and_idempotency(self, adapter_class):
        adapter = adapter_class.return_value
        ready = self._upload(
            key="videos/devotional-cli/orphan.mp4",
            state=DevotionalVideoUpload.State.READY,
        )
        attached = self._upload(
            key="videos/devotional-cli/attached.mp4",
            state=DevotionalVideoUpload.State.ATTACHED,
        )
        legacy = self._upload(
            key="videos/legacy.mp4",
            state=DevotionalVideoUpload.State.READY,
        )
        future = self._upload(
            key="videos/devotional-cli/future.mp4",
            state=DevotionalVideoUpload.State.READY,
            expired=False,
        )
        output = StringIO()
        call_command("cleanup_devotional_video_uploads", stdout=output)
        self.assertIn("WOULD CLEAN", output.getvalue())
        adapter_class.assert_not_called()

        call_command("cleanup_devotional_video_uploads", "--execute")
        adapter.abort_for_cleanup.assert_called_once_with(
            key=ready.storage_key, upload_id=ready.upload_id
        )
        adapter.delete_object.assert_called_once_with(key=ready.storage_key)
        ready.refresh_from_db()
        self.assertEqual(ready.state, DevotionalVideoUpload.State.CLEANED)
        for untouched in (attached, legacy, future):
            untouched.refresh_from_db()
            self.assertNotEqual(untouched.state, DevotionalVideoUpload.State.CLEANED)
        call_command("cleanup_devotional_video_uploads", "--execute")
        adapter.delete_object.assert_called_once()

    @patch("learning_resources.management.commands.cleanup_devotional_video_uploads.MultipartUploadAdapter")
    def test_missing_object_is_successful_and_repeat_is_noop(self, adapter_class):
        missing = RuntimeError("missing")
        missing.response = {"Error": {"Code": "NoSuchKey"}}
        adapter = adapter_class.return_value
        adapter.abort_for_cleanup.side_effect = missing
        adapter.delete_object.side_effect = missing
        upload = self._upload(
            key="videos/devotional-cli/already-gone.mp4",
            state=DevotionalVideoUpload.State.READY,
        )

        call_command("cleanup_devotional_video_uploads", "--execute")
        upload.refresh_from_db()
        self.assertEqual(upload.state, DevotionalVideoUpload.State.CLEANED)
        self.assertIsNotNone(upload.cleaned_at)

        call_command("cleanup_devotional_video_uploads", "--execute")
        self.assertEqual(adapter.delete_object.call_count, 1)

    @patch("learning_resources.management.commands.cleanup_devotional_video_uploads._finish_cleanup")
    @patch("learning_resources.management.commands.cleanup_devotional_video_uploads.MultipartUploadAdapter")
    def test_delete_success_then_db_failure_is_reconciled_on_retry(
        self, adapter_class, finish_cleanup
    ):
        upload = self._upload(
            key="videos/devotional-cli/reconcile.mp4",
            state=DevotionalVideoUpload.State.READY,
        )

        def finish(upload_id):
            if finish_cleanup.call_count == 1:
                raise RuntimeError("database unavailable")
            DevotionalVideoUpload.objects.filter(pk=upload_id).update(
                cleaned_at=timezone.now()
            )

        finish_cleanup.side_effect = finish

        call_command("cleanup_devotional_video_uploads", "--execute")
        upload.refresh_from_db()
        self.assertEqual(upload.state, DevotionalVideoUpload.State.CLEANED)
        self.assertIsNone(upload.cleaned_at)

        call_command("cleanup_devotional_video_uploads", "--execute")
        self.assertEqual(adapter_class.return_value.delete_object.call_count, 2)
        self.assertEqual(finish_cleanup.call_count, 2)
        upload.refresh_from_db()
        self.assertIsNotNone(upload.cleaned_at)

    @patch("learning_resources.management.commands.cleanup_devotional_video_uploads.MultipartUploadAdapter")
    def test_storage_failure_releases_claim_for_retry(self, adapter_class):
        adapter = adapter_class.return_value
        adapter.delete_object.side_effect = [RuntimeError("storage unavailable"), None]
        upload = self._upload(
            key="videos/devotional-cli/retry.mp4",
            state=DevotionalVideoUpload.State.READY,
        )

        call_command("cleanup_devotional_video_uploads", "--execute")
        upload.refresh_from_db()
        self.assertEqual(upload.state, DevotionalVideoUpload.State.READY)

        call_command("cleanup_devotional_video_uploads", "--execute")
        upload.refresh_from_db()
        self.assertEqual(upload.state, DevotionalVideoUpload.State.CLEANED)
        self.assertIsNotNone(upload.cleaned_at)
