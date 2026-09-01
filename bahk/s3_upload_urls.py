"""Staff-protected compatibility URLs for django-s3-file-field's admin widget."""

from django.contrib.admin.views.decorators import staff_member_required
from django.views.decorators.csrf import csrf_exempt
from django.core import signing
from django.http import HttpResponse
from django.urls import path, reverse
from rest_framework.decorators import api_view, parser_classes
from rest_framework.parsers import JSONParser
from rest_framework.response import Response
from s3_file_field import _registry, views
from s3_file_field.views import UploadCompletionRequestSerializer

app_name = "s3_file_field"


@staff_member_required
def upload_initialize(request, *args, **kwargs):
    return views.upload_initialize(request, *args, **kwargs)

@staff_member_required
@api_view(["POST"])
@parser_classes([JSONParser])
def upload_complete(request, *args, **kwargs):
    """Complete multipart uploads with server credentials, not a presigned URL."""
    serializer = UploadCompletionRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    transferred_parts = serializer.save()

    upload_signature = signing.loads(serializer.validated_data["upload_signature"])
    field = _registry.get_field(upload_signature["field_id"])
    storage = field.storage
    client = storage.connection.meta.client

    client.complete_multipart_upload(
        Bucket=storage.bucket_name,
        Key=transferred_parts.object_key,
        UploadId=transferred_parts.upload_id,
        MultipartUpload={
            "Parts": [
                {"PartNumber": part.part_number, "ETag": part.etag}
                for part in transferred_parts.parts
            ]
        },
    )

    return Response(
        {
            "complete_url": reverse("s3_file_field:completion-ack"),
            "body": "",
        }
    )


@csrf_exempt
@staff_member_required
def completion_ack(request, *args, **kwargs):
    return HttpResponse(status=204)


@staff_member_required
def finalize(request, *args, **kwargs):
    return views.finalize(request, *args, **kwargs)


urlpatterns = [
    path("upload-initialize/", upload_initialize, name="upload-initialize"),
    path("upload-complete/", upload_complete, name="upload-complete"),
    path("completion-ack/", completion_ack, name="completion-ack"),
    path("finalize/", finalize, name="finalize"),

]
