"""Tests for the admin surface of ``Feast.observance_id``.

The re-key made ``(church, observance_id)`` the identity of the model and then left the field out
of ``list_display``, ``list_filter``, ``search_fields`` and every fieldset -- so the thing the row
is keyed by could not be read, searched or filtered, and an unkeyed row could not be spotted at
all. These pin it visible and never editable.
"""

from unittest.mock import patch

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.test import TestCase

from hub.admin import FeastAdmin
from hub.models import Church, Feast


class FeastAdminObservanceIdTests(TestCase):
    """``observance_id`` is half the uniqueness key, so it is visible but never editable."""

    def setUp(self):
        self.feast_admin = FeastAdmin(Feast, AdminSite())

    def test_observance_id_is_listed_and_searchable(self):
        self.assertIn("observance_id", self.feast_admin.list_display)
        self.assertIn("observance_id", self.feast_admin.search_fields)

    def test_observance_id_is_read_only(self):
        self.assertIn("observance_id", self.feast_admin.readonly_fields)

    def test_observance_id_appears_in_a_fieldset(self):
        shown = {
            field
            for _, options in self.feast_admin.fieldsets
            for field in options["fields"]
        }
        self.assertIn("observance_id", shown)

    def test_empty_field_filters_cover_both_pipeline_gaps(self):
        filtered = {
            entry[0] for entry in self.feast_admin.list_filter if isinstance(entry, tuple)
        }
        self.assertEqual(filtered, {"designation", "observance_id"})


class FeastAdminRendersTests(TestCase):
    """The list and change views have to survive the new filters and the read-only field."""

    def setUp(self):
        self.admin_user = get_user_model().objects.create_superuser(
            username="admin2", email="admin2@example.com", password="password",
        )
        self.client.force_login(self.admin_user)
        church = Church.objects.get(pk=Church.get_default_pk())
        with patch("hub.signals.match_icon_to_feast_task.delay"), \
                patch("hub.signals.determine_feast_designation_task.delay"):
            self.feast = Feast.objects.create(
                church=church, name="St. Theodore the Tyron",
                observance_id="theodore_the_tyron",
            )

    def test_changelist_renders_with_the_empty_field_filters(self):
        response = self.client.get("/admin/hub/feast/")
        self.assertEqual(response.status_code, 200)

    def test_changelist_filters_on_missing_observance_id(self):
        response = self.client.get("/admin/hub/feast/?observance_id__isempty=1")
        self.assertEqual(response.status_code, 200)

    def test_change_form_shows_observance_id_without_offering_to_edit_it(self):
        response = self.client.get(f"/admin/hub/feast/{self.feast.pk}/change/")
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("theodore_the_tyron", body)
        self.assertNotIn('name="observance_id"', body)

    def test_searching_by_observance_id_finds_the_row(self):
        response = self.client.get("/admin/hub/feast/?q=theodore_the_tyron")
        self.assertEqual(response.status_code, 200)
        self.assertIn("St. Theodore the Tyron", response.content.decode())
