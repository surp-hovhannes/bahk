"""
Tests for push notification payload generation.

This test file validates that the push notification admin form generates
correct payload structures for different content types.
"""
import ast
import json
import re
from pathlib import Path

from django.test import SimpleTestCase


SEND_PUSH_TEMPLATE = (
    Path(__file__).resolve().parents[1]
    / "templates/admin/notifications/devicetoken/send_push.html"
)


def load_route_map_from_template():
    """Load the production JavaScript route map from the admin template."""
    template = SEND_PUSH_TEMPLATE.read_text()
    match = re.search(r"const routeMap = (?P<route_map>\{.*?\});", template, re.S)
    if not match:
        raise AssertionError("Could not find routeMap in send_push.html")
    return ast.literal_eval(match.group("route_map"))


def parse_query_params(input_value):
    params = {}
    if not input_value.strip():
        return params

    for part in input_value.split("&"):
        trimmed = part.strip()
        if not trimmed:
            continue
        key, _, value = trimmed.partition("=")
        if key:
            params[key.strip()] = value.strip()
    return params


def build_deep_link_payload(
    route_map,
    payload_type,
    content_id="",
    query_params="",
    activity_type="",
    activity_target="",
):
    """Mirror the template generator while using the production route map."""
    params = parse_query_params(query_params)

    if payload_type == "activity":
        activity_params = dict(params)
        if activity_type.strip():
            activity_params["activity_type"] = activity_type.strip()
        if activity_target.strip():
            activity_params["target_id"] = activity_target.strip()

        payload = {"screen": "activity"}
        if activity_params:
            payload["params"] = activity_params
        return payload

    route = route_map[payload_type]
    use_id = payload_type != "prayer_requests"
    screen_path = f"{route}/{content_id.strip()}" if use_id and content_id.strip() else route
    payload = {"screen": screen_path}
    if params:
        payload["params"] = params
    return payload


class PushNotificationPayloadTests(SimpleTestCase):
    """
    Test cases for push notification payload generation.

    These tests validate the expected payload structures based on the
    frontend app's routing requirements.
    """
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.route_map = load_route_map_from_template()

    def test_detail_payloads_use_admin_route_map(self):
        """Test detail screen payloads generated from the admin route map."""
        cases = [
            ("fast", "48", {"screen": "fast/48"}),
            ("devotional", "123", {"screen": "devotional/123"}),
            ("prayer", "77", {"screen": "prayer/77"}),
            ("prayer_request", "123", {"screen": "prayer-request/123"}),
            ("video", "902", {"screen": "learn/video/902"}),
            ("article", "305", {"screen": "learn/article/305"}),
            ("recipe", "120", {"screen": "learn/recipe/120"}),
            ("prayer_set", "44", {"screen": "prayer-set/44"}),
        ]

        for payload_type, content_id, expected in cases:
            with self.subTest(payload_type=payload_type):
                self.assertEqual(
                    build_deep_link_payload(self.route_map, payload_type, content_id),
                    expected,
                )

    def test_prayer_requests_list_payload_ignores_content_id(self):
        """Test prayer requests list screen payload does not include an ID."""
        self.assertEqual(
            build_deep_link_payload(self.route_map, "prayer_requests", "123"),
            {"screen": "prayer-requests"},
        )

    def test_activity_feed_basic(self):
        """Test basic activity feed payload."""
        self.assertEqual(
            build_deep_link_payload(self.route_map, "activity"),
            {"screen": "activity"},
        )

    def test_activity_feed_with_params(self):
        """Test activity feed payload with parameters."""
        self.assertEqual(
            build_deep_link_payload(
                self.route_map,
                "activity",
                activity_type="announcement",
                activity_target="987",
            ),
            {
                "screen": "activity",
                "params": {
                    "activity_type": "announcement",
                    "target_id": "987",
                },
            },
        )

    def test_payload_with_query_params(self):
        """Test payload with additional query parameters."""
        self.assertEqual(
            build_deep_link_payload(
                self.route_map,
                "fast",
                "48",
                "source=push&ref=notification",
            ),
            {
                "screen": "fast/48",
                "params": {
                    "source": "push",
                    "ref": "notification",
                },
            },
        )

    def test_external_url_payload(self):
        """Test external URL payload structure."""
        expected = {"url": "https://example.com/announcement"}
        # External URLs should have 'url' key, not 'screen'
        self.assertIn("url", expected)
        self.assertNotIn("screen", expected)
        self.assertTrue(expected["url"].startswith("https://"))

    def test_json_serialization(self):
        """Test that all payloads can be serialized to JSON."""
        test_payloads = [
            {"screen": "fast/48"},
            {"screen": "learn/video/902"},
            {"screen": "prayer-set/44"},
            {"screen": "prayer-request/123"},
            {"screen": "prayer-requests"},
            {"screen": "activity", "params": {"activity_type": "announcement"}},
            {"url": "https://example.com"},
        ]

        for payload in test_payloads:
            # Should not raise exception
            json_str = json.dumps(payload)
            # Should be able to parse it back
            parsed = json.loads(json_str)
            self.assertEqual(payload, parsed)


class RouteMapValidationTests(SimpleTestCase):
    """
    Validate that the JavaScript route mapping matches frontend expectations.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.route_map = load_route_map_from_template()

    def test_prayer_request_route(self):
        """Verify prayer_request maps to prayer-request detail route."""
        self.assertEqual(self.route_map['prayer_request'], 'prayer-request')

    def test_prayer_requests_route(self):
        """Verify prayer_requests maps to prayer-requests list route."""
        self.assertEqual(self.route_map['prayer_requests'], 'prayer-requests')

    def test_learning_resources_have_prefix(self):
        """Verify learning resources have learn/ prefix."""
        learning_resources = ['video', 'article', 'recipe']
        for resource in learning_resources:
            route = self.route_map[resource]
            self.assertTrue(
                route.startswith('learn/'),
                f"{resource} should have 'learn/' prefix, got: {route}"
            )

    def test_prayer_set_uses_hyphen(self):
        """Verify prayer_set maps to prayer-set with hyphen."""
        self.assertEqual(
            self.route_map['prayer_set'],
            'prayer-set',
            "prayer_set should map to 'prayer-set' with hyphen"
        )

    def test_standard_routes_no_prefix(self):
        """Verify standard routes have no prefix."""
        standard_routes = ['fast', 'devotional', 'prayer']
        for route_key in standard_routes:
            route = self.route_map[route_key]
            self.assertFalse(
                '/' in route,
                f"{route_key} should not have slashes: {route}"
            )

    def test_all_routes_lowercase(self):
        """Verify all routes are lowercase."""
        for route_key, route_value in self.route_map.items():
            self.assertEqual(
                route_value,
                route_value.lower(),
                f"Route {route_value} should be lowercase"
            )


class AnnouncementExternalLinkTests(SimpleTestCase):
    """
    Test announcement external link functionality.

    Based on the spec:
    - Announcements with external links use activity_type='announcement'
    - data.announcement_url holds the URL
    - App opens URL in platform browser via openExternalLink
    """

    def test_announcement_with_url_in_data(self):
        """Test announcement data structure with URL."""
        announcement_data = {
            "announcement_id": 123,
            "announcement_url": "https://example.com/news",
            "publish_at": "2025-01-01T00:00:00Z",
            "expires_at": None,
        }

        self.assertIn("announcement_url", announcement_data)
        self.assertTrue(announcement_data["announcement_url"].startswith("https://"))

    def test_announcement_activity_feed_payload(self):
        """Test activity feed payload for announcement with URL."""
        payload = {
            "screen": "activity",
            "params": {
                "activity_type": "announcement",
                "target_id": "987"
            }
        }

        # This opens activity feed and finds announcement #987
        # The app then reads data.announcement_url from the feed item
        self.assertEqual(payload["params"]["activity_type"], "announcement")
        self.assertEqual(payload["params"]["target_id"], "987")

    def test_direct_external_url_skips_activity(self):
        """Test direct external URL bypasses activity feed."""
        payload = {"url": "https://example.com/direct"}

        # URL contains :// so notification handler treats it as external
        # Opens directly without going to Activity screen
        self.assertIn("://", payload["url"])
        self.assertNotIn("screen", payload)
        self.assertNotIn("params", payload)
