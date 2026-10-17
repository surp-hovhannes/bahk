"""FEAST_ICON_MATCH_ENGINE chooses who decides a new feast's icon."""

import io
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase, override_settings

from hub.models import Church, Feast
from hub.services.icon_clef_judge import IMAGE_MAX_BYTES, thumbnail_image_loader
from hub.services.icon_match_service import IconMatchOutcome
from hub.tasks.icon_tasks import feast_icon_engine, match_icon_to_feast_task
from icons.models import Icon


def outcome(icon_id=None, *, assignable=True, status="complete"):
    matches = []
    if icon_id is not None:
        matches = [
            {
                "id": icon_id,
                "relation": "exact_event",
                "match_tier": "exact_event",
                "confidence": "high",
                "auto_assignable": assignable,
                "provenance": "clef_judge",
            }
        ]
    return IconMatchOutcome(matches=matches, status=status)


class FeastIconEngineTests(TestCase):
    def setUp(self):
        self.church = Church.objects.create(name="Engine fixture church")
        self.palm = Icon.objects.create(title="Palm Sunday Entry into Jerusalem", church=self.church, image="palm.jpg")
        self.other = Icon.objects.create(title="Palm Sunday 2", church=self.church, image="palm2.jpg")
        # save() caches its own thumbnail URL; set the ones production would hold.
        Icon.objects.filter(pk=self.palm.pk).update(cached_thumbnail_url="https://example.com/palm.jpg")
        Icon.objects.filter(pk=self.other.pk).update(cached_thumbnail_url=None)
        with patch("hub.signals.match_icon_to_feast_task.delay"):
            self.feast = Feast.objects.create(name="Palm Sunday", church=self.church)

    def run_task(self, *, llm=None, clef=None):
        with (
            patch("hub.tasks.icon_tasks.match_icons", return_value=llm) as llm_mock,
            patch("hub.tasks.icon_tasks.judge_icons", side_effect=clef) as clef_mock,
            patch("hub.tasks.icon_tasks.logger") as logger,
        ):
            match_icon_to_feast_task(self.feast.pk)
        self.feast.refresh_from_db()
        return llm_mock, clef_mock, logger

    def test_the_llm_matcher_decides_by_default(self):
        self.assertEqual(feast_icon_engine(), "llm")
        llm, clef, _ = self.run_task(llm=outcome(self.palm.id))
        clef.assert_not_called()
        llm.assert_called_once()
        self.assertEqual(self.feast.icon_id, self.palm.id)

    @override_settings(FEAST_ICON_MATCH_ENGINE="clef")
    def test_clef_decides_and_sees_the_cached_thumbnails(self):
        with patch("hub.tasks.icon_tasks.thumbnail_image_loader") as loader:
            llm, clef, _ = self.run_task(clef=lambda *a, **k: outcome(self.palm.id))
        llm.assert_not_called()
        self.assertEqual(self.feast.icon_id, self.palm.id)
        loader.assert_called_once_with({self.palm.id: "https://example.com/palm.jpg", self.other.id: None})
        self.assertIs(clef.call_args.kwargs["image_for"], loader.return_value)

    @override_settings(FEAST_ICON_MATCH_ENGINE="clef")
    def test_clef_writes_only_a_complete_assignable_first_match(self):
        for result in (outcome(self.palm.id, assignable=False), outcome(self.palm.id, status="partial"), outcome()):
            self.run_task(clef=lambda *a, result=result, **k: result)
            self.assertIsNone(self.feast.icon_id)

    @override_settings(FEAST_ICON_MATCH_ENGINE="clef")
    def test_unavailable_clef_writes_nothing(self):
        self.run_task(clef=lambda *a, **k: IconMatchOutcome(diagnostics=["clef_feast_question_failed"]))
        self.assertIsNone(self.feast.icon_id)

    @override_settings(FEAST_ICON_MATCH_ENGINE="clef_shadow")
    def test_shadow_writes_the_llm_choice_and_logs_clefs(self):
        written_before_shadow = []

        def clef(*args, **kwargs):
            written_before_shadow.append(Feast.objects.get(pk=self.feast.pk).icon_id)
            return outcome(self.other.id)

        _, clef_mock, logger = self.run_task(llm=outcome(self.palm.id), clef=clef)
        clef_mock.assert_called_once()
        self.assertEqual(self.feast.icon_id, self.palm.id)
        # The shadow runs after the LLM's write, so its latency never delays it.
        self.assertEqual(written_before_shadow, [self.palm.id])
        shadow = [c for c in logger.info.call_args_list if c.args[0].startswith("Clef shadow")]
        self.assertEqual(len(shadow), 1)
        self.assertEqual(shadow[0].args[3:5], (self.palm.id, self.other.id))
        self.assertEqual(shadow[0].args[-1], " [differs]")

    @override_settings(FEAST_ICON_MATCH_ENGINE="clef_shadow")
    def test_a_failing_shadow_does_not_stop_the_llm_write(self):
        def boom(*args, **kwargs):
            raise RuntimeError("Clef down")

        _, _, logger = self.run_task(llm=outcome(self.palm.id), clef=boom)
        self.assertEqual(self.feast.icon_id, self.palm.id)
        logger.warning.assert_called_once()

    @override_settings(FEAST_ICON_MATCH_ENGINE="gpt")
    def test_an_unknown_engine_falls_back_to_the_llm_matcher(self):
        with patch("hub.tasks.icon_tasks.logger") as logger:
            self.assertEqual(feast_icon_engine(), "llm")
        logger.warning.assert_called_once()

    def fake_clef(self, state, questions, model="clef", images=None):
        if "names_event" in questions:
            return {"names_event": {"noul": 0.9}}
        depicts = "Entry into Jerusalem" in state
        return {
            "depicts_event": {"noul": 0.96 if depicts else 0.3},
            "all_figures": {"noul": 0.8},
            "related": {"noul": 0.97 if depicts else 0.6},
        }

    def run_real_judge(self, opener):
        with (
            patch("hub.services.icon_clef_judge.run_clef", self.fake_clef),
            patch("hub.services.icon_clef_judge._THUMBNAIL_OPENER.open", side_effect=opener),
        ):
            match_icon_to_feast_task(self.feast.pk)
        self.feast.refresh_from_db()

    @override_settings(FEAST_ICON_MATCH_ENGINE="clef")
    def test_end_to_end_with_the_real_judge(self):
        Icon.objects.filter(pk=self.other.pk).update(cached_thumbnail_url="https://example.com/palm2.jpg")
        self.run_real_judge(lambda url, timeout: fake_response())
        self.assertEqual(self.feast.icon_id, self.palm.id)

    @override_settings(FEAST_ICON_MATCH_ENGINE="clef")
    def test_without_every_image_clef_only_suggests(self):
        # The thresholds were calibrated with images: a failed download, or an icon with no
        # thumbnail URL (here, Palm Sunday 2), must not lead to a write.
        for opener in (lambda url, timeout: fake_response(), OSError("S3 down")):
            with self.subTest(opener=opener):
                self.run_real_judge(opener)
                self.assertIsNone(self.feast.icon_id)


def fake_response(body=b"jpeg-bytes", content_type="image/jpeg"):
    response = MagicMock()
    response.__enter__.return_value = response
    response.headers.get_content_type.return_value = content_type
    stream = io.BytesIO(body)
    response.read.side_effect = lambda size=-1: stream.read(size)
    return response


class ThumbnailImageLoaderTests(SimpleTestCase):
    OPEN = "hub.services.icon_clef_judge._THUMBNAIL_OPENER.open"

    def test_returns_a_cached_data_url(self):
        with patch(self.OPEN, return_value=fake_response()) as opener:
            image_for = thumbnail_image_loader({1: "https://example.com/a.jpg"})
            self.assertEqual(image_for(1), "data:image/jpeg;base64,anBlZy1ieXRlcw==")
            image_for(1)
        opener.assert_called_once()

    def test_skips_missing_non_web_non_image_and_oversized(self):
        with patch(self.OPEN) as opener:
            image_for = thumbnail_image_loader({1: None, 2: "file:///etc/passwd"})
            self.assertIsNone(image_for(1))
            self.assertIsNone(image_for(2))
            opener.assert_not_called()
            opener.return_value = fake_response(content_type="text/html")
            self.assertIsNone(thumbnail_image_loader({3: "https://example.com/page"})(3))
            opener.return_value = fake_response(body=b"x" * (IMAGE_MAX_BYTES + 1))
            self.assertIsNone(thumbnail_image_loader({4: "https://example.com/big.jpg"})(4))

    def test_a_slow_download_is_cut_off_by_the_total_deadline(self):
        clock = iter(range(0, 1000, 5))
        with (
            patch(self.OPEN, return_value=fake_response(body=b"x" * (512 * 1024))),
            patch("hub.services.icon_clef_judge.time.monotonic", side_effect=lambda: next(clock)),
            self.assertRaises(TimeoutError),
        ):
            thumbnail_image_loader({1: "https://example.com/slow.jpg"}, timeout=12)(1)

    def test_redirects_are_refused(self):
        class Redirect(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", "ftp://127.0.0.1/x.png")
                self.end_headers()

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Redirect)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_port}/thumb.png"
        with patch.dict("os.environ", {"NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}):
            with self.assertRaises(urllib.error.HTTPError) as refused:
                thumbnail_image_loader({1: url}, timeout=5)(1)
        self.assertEqual(refused.exception.code, 302)
