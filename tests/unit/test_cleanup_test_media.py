"""Tests for the standalone test media cleanup script."""

import os
import tempfile
from pathlib import Path
from unittest import TestCase, skipIf

from django.test import override_settings

from cleanup_test_media import cleanup_test_media
from tests.test_utils import cleanup_test_media as cleanup_settings_test_media


class CleanupTestMediaTests(TestCase):
    """Validate cleanup behavior for safe and unsafe test_media paths."""

    def test_cleanup_removes_project_test_media_contents(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                media_dir = Path("test_media")
                nested_dir = media_dir / "nested"
                nested_dir.mkdir(parents=True)
                media_file = media_dir / "image.jpg"
                nested_file = nested_dir / "thumb.jpg"
                media_file.write_text("image")
                nested_file.write_text("thumb")

                cleaned = cleanup_test_media()

                self.assertTrue(cleaned)
                self.assertTrue(media_dir.exists())
                self.assertFalse(media_file.exists())
                self.assertFalse(nested_dir.exists())
                self.assertFalse(nested_file.exists())
            finally:
                os.chdir(original_cwd)

    @skipIf(not hasattr(os, "symlink"), "symlink is unavailable on this platform")
    def test_cleanup_refuses_symlinked_test_media(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_cwd = os.getcwd()
            external_dir = Path(tmpdir) / "external"
            project_dir = Path(tmpdir) / "project"
            external_dir.mkdir()
            project_dir.mkdir()
            external_file = external_dir / "keep.txt"
            external_file.write_text("do not delete")

            try:
                os.chdir(project_dir)
                os.symlink(external_dir, "test_media")

                cleaned = cleanup_test_media()

                self.assertFalse(cleaned)
                self.assertTrue(external_file.exists())
                self.assertEqual(external_file.read_text(), "do not delete")
            finally:
                os.chdir(original_cwd)

    @skipIf(not hasattr(os, "symlink"), "symlink is unavailable on this platform")
    def test_test_utils_cleanup_refuses_symlinked_media_root(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            external_dir = Path(tmpdir) / "external"
            project_dir = Path(tmpdir) / "project"
            external_dir.mkdir()
            project_dir.mkdir()
            external_file = external_dir / "keep.txt"
            external_file.write_text("do not delete")
            media_root = project_dir / "test_media"
            os.symlink(external_dir, media_root)

            with override_settings(MEDIA_ROOT=str(media_root)):
                cleaned = cleanup_settings_test_media()

            self.assertFalse(cleaned)
            self.assertTrue(external_file.exists())
            self.assertEqual(external_file.read_text(), "do not delete")
