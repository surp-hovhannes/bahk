from django.core.exceptions import ImproperlyConfigured
from rest_framework import status
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from .devotional_video_uploads import (
    UploadError, complete_upload, finalize_upload, initialize_upload,
)


class StaffUploadView(APIView):
    permission_classes = [IsAdminUser]

    def run(self, request):
        raise NotImplementedError

    def post(self, request):
        try:
            return Response(self.run(request), status=status.HTTP_200_OK)
        except (UploadError, ImproperlyConfigured) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)


class InitializeUploadView(StaffUploadView):
    def run(self, request):
        allowed = {"file_name", "file_size", "content_type"}
        if set(request.data) != allowed:
            raise UploadError("Exactly file_name, file_size, and content_type are required.")
        return initialize_upload(user_id=request.user.pk, **{key: request.data[key] for key in allowed})


class CompleteUploadView(StaffUploadView):
    def run(self, request):
        allowed = {"upload_session", "upload_id", "parts"}
        if set(request.data) != allowed:
            raise UploadError("Exactly upload_session, upload_id, and parts are required.")
        return complete_upload(user_id=request.user.pk, **{key: request.data[key] for key in allowed})


class FinalizeUploadView(StaffUploadView):
    def run(self, request):
        if set(request.data) != {"upload_session"}:
            raise UploadError("Exactly upload_session is required.")
        return finalize_upload(user_id=request.user.pk, upload_session=request.data["upload_session"])
