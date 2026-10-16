"""Regression tests guarding Django migration state.

The ``LLMPrompt.model`` choices drift (issue #545) showed that model and
migration state can silently diverge: anyone running ``makemigrations``
for unrelated work would regenerate the drift migration and could
accidentally commit it.  This module fails as soon as that happens again.
"""

from django.core.management import call_command
from django.test import TestCase

from learning_resources.models import Video


class HubMigrationStateTests(TestCase):
    """``makemigrations --check`` must be a no-op for the hub app.

    ``TestCase`` (not ``SimpleTestCase``) because ``makemigrations``
    reads applied-migration history through the migration recorder.

    Scoped to ``hub`` on purpose, with a storage workaround:

    Under test settings ``learning_resources.apps`` swaps ``Video.video``
    storage for ``OfflineS3Storage`` — an inner class that cannot be
    serialized.  ``makemigrations`` deconstructs every app's fields while
    building autodetector state, so it crashes before reporting anything.
    Restoring the field default for the duration of the check avoids the
    crash without touching the offline-upload behavior of other tests.
    Widen the scope to the whole project once that storage class is
    serializable.
    """

    def setUp(self):
        super().setUp()
        self.video_field = Video._meta.get_field("video")
        self.swapped_storage = self.video_field.storage
        from django.core.files.storage import default_storage

        self.video_field.storage = default_storage
        self.addCleanup(setattr, self.video_field, "storage", self.swapped_storage)

    def test_no_pending_hub_model_changes(self):
        """Hub models and committed hub migrations must agree.

        Fails (via SystemExit from ``--check``) when the autodetector can
        generate any hub migration, so choice drift or uncommitted schema
        changes cannot land unnoticed.
        """
        call_command("makemigrations", "hub", "--check", "--dry-run", "--noinput", verbosity=0)
