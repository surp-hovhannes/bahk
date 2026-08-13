"""Staff-protected compatibility URLs for django-s3-file-field's admin widget."""

from django.contrib.admin.views.decorators import staff_member_required
from django.urls import path
from s3_file_field import views

app_name = "s3_file_field"


@staff_member_required
def upload_initialize(request, *args, **kwargs):
    return views.upload_initialize(request, *args, **kwargs)


@staff_member_required
def upload_complete(request, *args, **kwargs):
    return views.upload_complete(request, *args, **kwargs)


@staff_member_required
def finalize(request, *args, **kwargs):
    return views.finalize(request, *args, **kwargs)


urlpatterns = [
    path("upload-initialize/", upload_initialize, name="upload-initialize"),
    path("upload-complete/", upload_complete, name="upload-complete"),
    path("finalize/", finalize, name="finalize"),
]
