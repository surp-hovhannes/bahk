import os
import uuid
import warnings

from django.core.exceptions import ImproperlyConfigured
from PIL import Image, UnidentifiedImageError
from rest_framework import generics, permissions, status
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.response import Response
from rest_framework.views import APIView
from django.db.models import Prefetch
from ..serializers import ProfileSerializer, ProfileImageSerializer
from ..models import Profile, Fast


PROFILE_IMAGE_PENDING_PREFIX = "profile_images/pending/"
PROFILE_IMAGE_PREFIX = "profile_images/originals/"
PROFILE_IMAGE_MAX_SIZE = 10 * 1024 * 1024
PROFILE_IMAGE_UPLOAD_EXPIRY = 5 * 60
PROFILE_IMAGE_TYPES = {
    ".gif": ("image/gif", "GIF"),
    ".jpeg": ("image/jpeg", "JPEG"),
    ".jpg": ("image/jpeg", "JPEG"),
    ".png": ("image/png", "PNG"),
    ".webp": ("image/webp", "WEBP"),
}


class DirectUploadUnavailable(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "Direct image uploads are unavailable."




class ProfileImageUploadStorage:
    """Mockable boundary over the profile image field's S3 storage client."""

    def __init__(self):
        self.storage = Profile._meta.get_field("profile_image").storage
        try:
            self.client = self.storage.connection.meta.client
            self.bucket_name = self.storage.bucket_name
        except AttributeError as exc:
            raise ImproperlyConfigured(
                "Direct profile image uploads require S3-backed storage."
            ) from exc

    def presign_put(self, *, key, content_type, file_size):
        return self.client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": self.bucket_name,
                "Key": key,
                "ContentType": content_type,
                "ContentLength": file_size,
            },
            ExpiresIn=PROFILE_IMAGE_UPLOAD_EXPIRY,
            HttpMethod="PUT",
        )

    def head_object(self, key):
        return self.client.head_object(Bucket=self.bucket_name, Key=key)

    def open(self, key):
        return self.storage.open(key, "rb")

    def delete(self, key):
        self.client.delete_object(Bucket=self.bucket_name, Key=key)

    def copy(self, *, source_key, destination_key, content_type):
        self.client.copy_object(
            Bucket=self.bucket_name,
            CopySource={"Bucket": self.bucket_name, "Key": source_key},
            Key=destination_key,
            ContentType=content_type,
            MetadataDirective="REPLACE",
        )


def _profile_image_details(data):
    file_name = data.get("file_name")
    content_type = data.get("content_type")
    file_size = data.get("file_size")
    if not isinstance(file_name, str) or os.path.basename(file_name) != file_name:
        raise ValidationError({"file_name": "Provide a safe image filename."})
    extension = os.path.splitext(file_name)[1].lower()
    expected = PROFILE_IMAGE_TYPES.get(extension)
    if not expected or content_type != expected[0]:
        raise ValidationError(
            {"content_type": "File extension and content type must be a supported image pair."}
        )
    if isinstance(file_size, bool) or not isinstance(file_size, int):
        raise ValidationError({"file_size": "Provide the image size in bytes."})
    if not 0 < file_size <= PROFILE_IMAGE_MAX_SIZE:
        raise ValidationError(
            {"file_size": f"Image size must be between 1 and {PROFILE_IMAGE_MAX_SIZE} bytes."}
        )
    return extension, content_type, file_size


def _validate_uploaded_image(storage, key, expected_type):
    try:
        metadata = storage.head_object(key)
    except Exception as exc:
        raise ValidationError({"key": "Uploaded image was not found."}) from exc
    if metadata.get("ContentLength", 0) > PROFILE_IMAGE_MAX_SIZE:
        storage.delete(key)
        raise ValidationError({"key": "Uploaded image exceeds the maximum size."})
    if metadata.get("ContentType") != expected_type[0]:
        storage.delete(key)
        raise ValidationError({"key": "Uploaded image content type is invalid."})
    try:
        with storage.open(key) as image_file:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                image = Image.open(image_file)
                image.load()
        if image.format != expected_type[1]:
            raise UnidentifiedImageError
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        OSError,
        SyntaxError,
        UnidentifiedImageError,
    ):
        storage.delete(key)
        raise ValidationError({"key": "Uploaded file is not a valid image."})


class ProfileImagePresignView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        extension, content_type, file_size = _profile_image_details(request.data)
        key = f"{PROFILE_IMAGE_PENDING_PREFIX}{request.user.pk}/{uuid.uuid4().hex}{extension}"
        try:
            upload_url = ProfileImageUploadStorage().presign_put(
                key=key, content_type=content_type, file_size=file_size
            )
        except ImproperlyConfigured as exc:
            raise DirectUploadUnavailable() from exc
        except Exception as exc:
            raise DirectUploadUnavailable("Could not initialize image upload.") from exc
        return Response(
            {
                "upload_url": upload_url,
                "key": key,
                "expires_in": PROFILE_IMAGE_UPLOAD_EXPIRY,
                "headers": {"Content-Type": content_type},
            },
            status=status.HTTP_200_OK,
        )


class ProfileImageConfirmView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        key = request.data.get("key")
        prefix = f"{PROFILE_IMAGE_PENDING_PREFIX}{request.user.pk}/"
        extension = os.path.splitext(key)[1].lower() if isinstance(key, str) else ""
        expected_type = PROFILE_IMAGE_TYPES.get(extension)
        if not isinstance(key, str) or not expected_type or not key.startswith(prefix):
            raise ValidationError({"key": "Provide a valid upload key for this user."})
        try:
            storage = ProfileImageUploadStorage()
            _validate_uploaded_image(storage, key, expected_type)
        except ImproperlyConfigured as exc:
            raise DirectUploadUnavailable() from exc
        destination_key = f"{PROFILE_IMAGE_PREFIX}{request.user.pk}/{uuid.uuid4().hex}{extension}"
        try:
            storage.copy(
                source_key=key,
                destination_key=destination_key,
                content_type=expected_type[0],
            )
        except Exception as exc:
            raise DirectUploadUnavailable("Could not finalize image upload.") from exc
        profile = request.user.profile
        profile.profile_image.name = destination_key
        try:
            profile.save(update_fields=["profile_image"])
        except Exception:
            storage.delete(destination_key)
            raise
        storage.delete(key)
        return Response(ProfileImageSerializer(profile).data, status=status.HTTP_200_OK)

class ProfileDetailView(generics.RetrieveUpdateAPIView):
    """
    API view to retrieve and update the authenticated user's profile.

    This view allows an authenticated user to retrieve their own profile information and update it as needed.
    The user's profile is identified based on the authenticated user making the request.

    Returns:
        - The profile data of the authenticated user.
    """
    serializer_class = ProfileSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        # Optimized: Use select_related and prefetch_related for better performance
        # This prevents N+1 queries when accessing user and church data
        return Profile.objects.select_related('user', 'church').prefetch_related(
            Prefetch('fasts', queryset=Fast.objects.select_related('church'))
        ).get(id=self.request.user.profile.id)


class ProfileImageUploadView(generics.UpdateAPIView):
    """
    API view to handle profile image uploads for the authenticated user.

    This view allows an authenticated user to upload or update their profile image. The image is parsed
    and processed using MultiPartParser and FormParser to handle file uploads.

    Inherits:
        - UpdateAPIView: A view that provides PUT/PATCH functionality for updating a model instance.

    Permissions:
        - IsAuthenticated: Only authenticated users can access this view.

    Parsers:
        - MultiPartParser: Parses multipart HTML form content, typically used for file uploads.
        - FormParser: Parses HTML form content.

    Returns:
        - The updated profile data with the new profile image.
    """
    serializer_class = ProfileImageSerializer
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def get_object(self):
        return self.request.user.profile
