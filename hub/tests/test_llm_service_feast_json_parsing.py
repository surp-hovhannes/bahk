from django.test import TestCase

from hub.services.llm_service import _parse_feast_context_json


class FeastContextJsonParsingTests(TestCase):
    def test_parses_fenced_json(self):
        response_text = (
            "```json\n"
            '{ "short_text": "Hello.", "text": "Longer explanation." }\n'
            "```"
        )
        parsed = _parse_feast_context_json(response_text)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["short_text"], "Hello.")
        self.assertEqual(parsed["text"], "Longer explanation.")

    def test_parses_json_with_leading_trailing_text(self):
        response_text = (
            "Here you go:\n\n"
            '{ "short_text": "Hi.", "text": "Details." }\n'
            "\nThanks!"
        )
        parsed = _parse_feast_context_json(response_text)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["short_text"], "Hi.")
        self.assertEqual(parsed["text"], "Details.")

    def test_parses_json_with_literal_newlines_in_string_values(self):
        # This string contains a literal newline inside the quoted JSON value,
        # which would normally raise: Invalid control character...
        response_text = '{ "short_text": "One sentence.", "text": "Line 1\nLine 2" }'
        parsed = _parse_feast_context_json(response_text)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["short_text"], "One sentence.")
        self.assertEqual(parsed["text"], "Line 1\nLine 2")

    def test_returns_none_for_truncated_json(self):
        response_text = '{ "short_text": "Hi.", "text": "Details..." '
        parsed = _parse_feast_context_json(response_text)
        self.assertIsNone(parsed)


