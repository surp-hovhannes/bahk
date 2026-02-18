from django.test import SimpleTestCase
from s3_file_field.widgets import S3FileInput

from hub.forms import CombinedDevotionalForm, SUPPORTED_LANGUAGES
from learning_resources.models import Video


class CombinedDevotionalFormTest(SimpleTestCase):
    def test_video_fields_use_s3_widget_with_model_field_id(self):
        form = CombinedDevotionalForm()
        expected_field_id = Video._meta.get_field("video").id

        for code, _, _ in SUPPORTED_LANGUAGES:
            field = form.fields[f"video_file_{code}"]
            self.assertIsInstance(field.widget, S3FileInput)
            self.assertEqual(field.widget.attrs["data-field-id"], expected_field_id)
            self.assertEqual(field.widget.attrs["accept"], "video/*")
