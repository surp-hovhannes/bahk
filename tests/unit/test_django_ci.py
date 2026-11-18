import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from scripts.django_ci import classify, completion_summary, documentation_only


CLASSIFIER = Path(__file__).resolve().parents[2] / "scripts" / "django_ci.py"


class PullRequestClassificationTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.repo = Path(directory.name)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "CI tests")
        self.git("config", "user.email", "ci@example.test")
        self.git("config", "commit.gpgsign", "false")
        self.write("README.md", "Documentation\n")
        self.write("app.py", "VERSION = 1\n")
        self.base = self.commit()

    def git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.repo, check=True, capture_output=True, text=True,
        ).stdout.strip()

    def write(self, path, content):
        file = self.repo / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content, encoding="utf-8")

    def commit(self):
        self.git("add", "--all")
        self.git("commit", "-qm", "Fixture revision")
        return self.git("rev-parse", "HEAD")

    def classify(self, head, base=None):
        output = self.repo / ".git" / "ci-output"
        result = subprocess.run(
            [sys.executable, str(CLASSIFIER), "classify"],
            cwd=self.repo,
            env={
                **os.environ,
                "GITHUB_EVENT_NAME": "pull_request",
                "PR_BASE": self.base if base is None else base,
                "PR_HEAD": head,
                "GITHUB_OUTPUT": str(output),
                "GITHUB_STEP_SUMMARY": str(self.repo / ".git" / "ci-summary"),
            },
            capture_output=True,
            text=True,
        )
        return result, output.read_text(encoding="utf-8") if output.exists() else ""

    def assert_classification(self, head, mode, *, base=None):
        result, output = self.classify(head, base)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(output, f"mode={mode}\n")

    def test_complete_pr_diff_against_merge_base(self):
        self.write("README.md", "Updated text\n")
        self.write("docs/admin-branding.md", "Branding guide\n")
        head = self.commit()
        self.git("switch", "--detach", self.base)
        self.write("app.py", "VERSION = 2\n")
        advanced_base = self.commit()
        self.assert_classification(head, "documentation", base=advanced_base)

    def test_mixed_change_includes_executable_widget_example(self):
        self.write("README.md", "Updated text\n")
        self.write("docs/examples/widgets-plain.html", "<script>run()</script>\n")
        self.assert_classification(self.commit(), "application")

    def test_unknown_markdown_is_application_relevant(self):
        self.write("docs/new-guide.md", "New guide\n")
        self.assert_classification(self.commit(), "application")

    def test_documentation_deletion_is_application_relevant(self):
        (self.repo / "README.md").unlink()
        self.assert_classification(self.commit(), "application")

    def test_rename_between_allowlisted_paths_is_application_relevant(self):
        (self.repo / "docs").mkdir()
        self.git("mv", "README.md", "docs/admin-branding.md")
        self.assert_classification(self.commit(), "application")

    def test_executable_documentation_path_is_application_relevant(self):
        self.git("update-index", "--chmod=+x", "README.md")
        self.git("commit", "-qm", "Change the executable bit")
        self.assert_classification(self.git("rev-parse", "HEAD"), "application")

    def test_symlink_at_allowlisted_path_is_application_relevant(self):
        readme = self.repo / "README.md"
        readme.unlink()
        readme.symlink_to("app.py")
        self.assert_classification(self.commit(), "application")

    def test_unexpected_empty_diff_is_application_relevant(self):
        self.assert_classification(self.base, "application")

    def test_unavailable_base_or_incomplete_history_cannot_skip(self):
        self.write("README.md", "Updated text\n")
        head = self.commit()
        for base in ("", "f" * 40):
            with self.subTest(base=base):
                result, output = self.classify(head, base)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(output, "")
        (self.repo / ".git" / "shallow").write_text(head + "\n", encoding="utf-8")
        result, output = self.classify(head)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(output, "")

    def test_failed_diff_command_cannot_set_a_classification(self):
        self.write("README.md", "Updated text\n")
        head = self.commit()
        tree = self.git("rev-parse", "HEAD^{tree}")
        (self.repo / ".git" / "objects" / tree[:2] / tree[2:]).unlink()
        result, output = self.classify(head)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(output, "")

    def test_malformed_diff_cannot_skip(self):
        metadata = b":100644 100644 " + b"a" * 40 + b" " + b"b" * 40 + b" M\0"
        doc = metadata + b"README.md\0"
        self.assertTrue(documentation_only(doc))
        for diff in (doc[:-1], doc + metadata, doc + b"invalid\0README.md\0", b""):
            with self.subTest(diff=diff):
                self.assertFalse(documentation_only(diff))


class CompletionTests(unittest.TestCase):
    def test_main_push_always_runs_application_validation(self):
        self.assertEqual(classify("push", "", ""), "application")

    def test_only_proven_documentation_skips_or_passed_application_jobs_succeed(self):
        self.assertIn("skipped", completion_summary("success", "documentation", "skipped", "skipped"))
        self.assertIn("passed", completion_summary("success", "application", "success", "success"))

        for args in (
            ("failure", "documentation", "skipped", "skipped"),
            ("cancelled", "documentation", "skipped", "skipped"),
            ("success", "", "skipped", "skipped"),
            ("success", "documentation", "success", "skipped"),
            ("success", "application", "skipped", "success"),
            ("success", "application", "success", "failure"),
            ("success", "application", "cancelled", "success"),
            ("success", "application", "success", "timed_out"),
        ):
            with self.subTest(args=args), self.assertRaises(RuntimeError):
                completion_summary(*args)
