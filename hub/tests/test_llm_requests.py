from unittest.mock import Mock

from django.test import SimpleTestCase

from hub.services.llm_requests import anthropic_message, openai_chat_completion


class LLMRequestTests(SimpleTestCase):
    def test_gpt5_uses_completion_tokens_without_sampling_parameters(self):
        client = Mock()

        openai_chat_completion(
            client,
            model="gpt-5-mini",
            messages=[{"role": "user", "content": "hello"}],
            max_tokens=100,
            temperature=0.2,
            top_p=1,
        )

        client.chat.completions.create.assert_called_once_with(
            model="gpt-5-mini",
            messages=[{"role": "user", "content": "hello"}],
            max_completion_tokens=100,
        )

    def test_standard_openai_model_keeps_sampling_parameters(self):
        client = Mock()

        openai_chat_completion(
            client,
            model="gpt-4o-mini",
            messages=[],
            max_tokens=100,
            temperature=0.2,
            top_p=1,
        )

        client.chat.completions.create.assert_called_once_with(
            model="gpt-4o-mini",
            messages=[],
            max_tokens=100,
            temperature=0.2,
            top_p=1,
        )

    def test_anthropic_request_omits_temperature(self):
        client = Mock()

        anthropic_message(
            client,
            model="claude-sonnet-4-6",
            messages=[{"role": "user", "content": "hello"}],
            max_tokens=100,
            system="Be concise.",
        )

        client.messages.create.assert_called_once_with(
            model="claude-sonnet-4-6",
            messages=[{"role": "user", "content": "hello"}],
            max_tokens=100,
            system="Be concise.",
        )
