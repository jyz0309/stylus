import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

from stylus.analyzer import (
    AnalyzerInput,
    ANALYZER_CHAT_RESPONSE_FORMAT,
    ANALYZER_OUTPUT_SCHEMA,
    ANALYZER_RESPONSE_TEXT_FORMAT,
    CommandAnalyzerProvider,
    FakeAnalyzerProvider,
    OpenAIChatAnalyzerProvider,
    OpenAIResponsesAnalyzerProvider,
    _is_openai_official,
    parse_analyzer_output,
    provider_from_env,
    provider_name,
)


class _FakeResponses:
    def __init__(self, calls: list, content: str) -> None:
        self._calls = calls
        self._content = content

    def create(self, **kwargs):
        self._calls.append(kwargs)
        return type("Resp", (), {"output_text": self._content})()


class _FakeClient:
    def __init__(self, calls: list, content: str) -> None:
        self.responses = _FakeResponses(calls, content)


class _FakeChatCompletions:
    def __init__(self, calls: list, content: str) -> None:
        self._calls = calls
        self._content = content

    def create(self, **kwargs):
        self._calls.append(kwargs)
        message = type("Msg", (), {"content": self._content})()
        choice = type("Choice", (), {"message": message})()
        return type("Resp", (), {"choices": [choice]})()


class _FakeChatClient:
    def __init__(self, calls: list, content: str) -> None:
        self.chat = type("Chat", (), {"completions": _FakeChatCompletions(calls, content)})()


class AnalyzerTests(unittest.TestCase):
    def test_parse_valid_analyzer_output(self):
        raw = json.dumps({
            "preferences": [{
                "topic": "tests",
                "instruction": "Prefer focused package tests before broad suites.",
                "confidence": "high",
                "evidence": "User replaced broad test command with package-level test.",
                "source_commit": "abc123",
            }],
            "obsolete_preferences": [{"instruction": "Run every test always", "reason": "too broad"}],
            "notes": ["kept concise"],
        })

        output = parse_analyzer_output(raw)

        self.assertEqual(output.preferences[0].topic, "tests")
        self.assertEqual(output.obsolete_preferences, ["Run every test always"])
        self.assertEqual(output.notes, ["kept concise"])

    def test_parse_rejects_non_json(self):
        with self.assertRaises(RuntimeError) as ctx:
            parse_analyzer_output("not json at all")
        self.assertIn("valid JSON", str(ctx.exception))
        self.assertIn("Output preview", str(ctx.exception))

    def test_parse_rejects_json_array(self):
        with self.assertRaises(RuntimeError) as ctx:
            parse_analyzer_output("[1, 2, 3]")
        self.assertIn("must be an object", str(ctx.exception))

    def test_parse_rejects_preference_missing_field(self):
        raw = json.dumps({"preferences": [{
            "topic": "tests",
            "instruction": "Prefer focused tests.",
            "confidence": "high",
            "source_commit": "abc123",
            # evidence missing
        }], "obsolete_preferences": [], "notes": []})
        with self.assertRaises(RuntimeError) as ctx:
            parse_analyzer_output(raw)
        self.assertIn("malformed", str(ctx.exception))
        self.assertIn("missing", str(ctx.exception))

    def test_parse_rejects_invalid_confidence(self):
        raw = json.dumps({"preferences": [{
            "topic": "tests", "instruction": "Prefer focused tests.",
            "confidence": "definitely", "evidence": "x", "source_commit": "abc123",
        }], "obsolete_preferences": [], "notes": []})
        with self.assertRaises(RuntimeError) as ctx:
            parse_analyzer_output(raw)
        self.assertIn("confidence must be one of", str(ctx.exception))

    def test_parse_rejects_preference_not_object(self):
        raw = json.dumps({"preferences": ["a plain string"], "obsolete_preferences": [], "notes": []})
        with self.assertRaises(RuntimeError) as ctx:
            parse_analyzer_output(raw)
        self.assertIn("must be an object", str(ctx.exception))

    def test_parse_error_includes_output_preview(self):
        raw = json.dumps({"preferences": "not-an-array"}, ensure_ascii=False)
        with self.assertRaises(RuntimeError) as ctx:
            parse_analyzer_output(raw)
        # The preview of the raw output is embedded so the failure is debuggable.
        self.assertIn("Output preview:", str(ctx.exception))
        self.assertIn("preferences", str(ctx.exception))

    def test_parse_tolerates_obsolete_as_strings_or_objects(self):
        raw = json.dumps({
            "preferences": [],
            "obsolete_preferences": ["Run every test always.", {"instruction": "Other rule", "reason": "x"}],
            "notes": [],
        })
        output = parse_analyzer_output(raw)
        self.assertEqual(output.obsolete_preferences, ["Run every test always.", "Other rule"])

    def test_fake_provider_returns_preference_from_commit(self):
        provider = FakeAnalyzerProvider()
        output = provider.analyze(AnalyzerInput(
            repo_id="/tmp/repo",
            branch="main",
            commit="abc123",
            baseline_change_id="baseline1",
            baseline_diff="-old\n+new\n",
            user_diff="-new\n+newer\n",
            current_preferences="",
        ))

        self.assertEqual(output.preferences[0].source_commit, "abc123")
        self.assertIn("Review user edits", output.preferences[0].instruction)

    def test_command_provider_sends_request_and_parses_response(self):
        script = (
            "import json, sys; "
            "request = json.load(sys.stdin); "
            "print(json.dumps({'preferences': [{'topic': 'scope', "
            "'instruction': 'Prefer small diffs after agent baselines.', "
            "'confidence': 'high', "
            "'evidence': request['commit'], "
            "'source_commit': request['commit']}], "
            "'obsolete_preferences': [], 'notes': []}))"
        )
        provider = CommandAnalyzerProvider([sys.executable, "-c", script])

        output = provider.analyze(AnalyzerInput(
            repo_id="/tmp/repo",
            branch="main",
            commit="abc123",
            baseline_change_id="baseline1",
            baseline_diff="-old\n+new\n",
            user_diff="-new\n+newer\n",
            current_preferences="",
        ))

        self.assertEqual(output.preferences[0].instruction, "Prefer small diffs after agent baselines.")
        self.assertEqual(output.preferences[0].evidence, "abc123")

    def test_openai_provider_sends_structured_responses_request(self):
        calls: list = []
        body = json.dumps({
            "preferences": [{
                "topic": "abstractions",
                "instruction": "Prefer existing helpers before adding local wrappers.",
                "confidence": "high",
                "evidence": "User commit replaced a new wrapper with an existing helper.",
                "source_commit": "abc123",
            }],
            "obsolete_preferences": [],
            "notes": ["clear signal"],
        })
        client = _FakeClient(calls, body)

        provider = OpenAIResponsesAnalyzerProvider(
            api_key="test-key",
            model="gpt-test",
            base_url="https://api.openai.com/v1",
            client=client,
        )

        output = provider.analyze(AnalyzerInput(
            repo_id="/tmp/repo",
            branch="main",
            commit="abc123",
            baseline_change_id="baseline1",
            baseline_diff="-old\n+new\n",
            user_diff="-new\n+helper\n",
            current_preferences="",
        ))

        payload = calls[0]
        self.assertEqual(payload["model"], "gpt-test")
        self.assertFalse(payload["stream"])
        text_format = payload["text"]["format"]
        self.assertEqual(text_format["type"], "json_schema")
        self.assertEqual(text_format["name"], "stylus_analysis")
        self.assertTrue(text_format["strict"])
        self.assertEqual(text_format["schema"]["type"], "object")
        self.assertIn("baseline_diff", payload["input"])
        self.assertIn("learns a user's coding style", payload["instructions"])
        self.assertEqual(output.preferences[0].instruction, "Prefer existing helpers before adding local wrappers.")

    def test_analyzer_schema_exports_sdk_compatible_formats(self):
        self.assertIs(ANALYZER_OUTPUT_SCHEMA, ANALYZER_RESPONSE_TEXT_FORMAT)
        self.assertEqual(ANALYZER_RESPONSE_TEXT_FORMAT["type"], "json_schema")
        self.assertEqual(ANALYZER_RESPONSE_TEXT_FORMAT["schema"]["type"], "object")
        self.assertEqual(ANALYZER_CHAT_RESPONSE_FORMAT["type"], "json_schema")
        self.assertIs(
            ANALYZER_CHAT_RESPONSE_FORMAT["json_schema"]["schema"],
            ANALYZER_RESPONSE_TEXT_FORMAT["schema"],
        )

    def test_openai_provider_url_derived_from_base_url(self):
        provider = OpenAIResponsesAnalyzerProvider(
            api_key="test-key",
            base_url="https://openrouter.ai/api/v1/",
            client=_FakeClient([], "{}"),
        )

        self.assertEqual(provider.url, "https://openrouter.ai/api/v1/responses")

    def test_provider_from_env_uses_openai_when_api_key_is_set(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True):
            provider = provider_from_env()

        self.assertIsInstance(provider, OpenAIResponsesAnalyzerProvider)
        self.assertEqual(provider.base_url, "https://api.openai.com/v1")

    def test_provider_from_env_routes_non_openai_base_url_to_chat(self):
        with patch.dict(os.environ, {
            "OPENAI_API_KEY": "test-key",
            "STYLUS_OPENAI_BASE_URL": "https://openrouter.ai/api/v1/",
        }, clear=True):
            provider = provider_from_env()

        self.assertIsInstance(provider, OpenAIChatAnalyzerProvider)
        self.assertEqual(provider.url, "https://openrouter.ai/api/v1/chat/completions")

    def test_provider_from_env_accepts_openai_model(self):
        with patch.dict(os.environ, {
            "OPENAI_API_KEY": "test-key",
            "STYLUS_OPENAI_MODEL": "gpt-custom",
        }, clear=True):
            provider = provider_from_env()

        self.assertIsInstance(provider, OpenAIResponsesAnalyzerProvider)
        self.assertEqual(provider.model, "gpt-custom")

    def test_provider_from_env_falls_back_to_fake_without_api_key(self):
        with patch.dict(os.environ, {}, clear=True):
            provider = provider_from_env()

        self.assertIsInstance(provider, FakeAnalyzerProvider)

    def test_chat_provider_sends_chat_completions_request(self):
        calls: list = []
        body = json.dumps({
            "preferences": [{
                "topic": "abstractions",
                "instruction": "Prefer existing helpers before adding local wrappers.",
                "confidence": "high",
                "evidence": "User commit replaced a new wrapper with an existing helper.",
                "source_commit": "abc123",
            }],
            "obsolete_preferences": [],
            "notes": ["clear signal"],
        })
        client = _FakeChatClient(calls, body)

        provider = OpenAIChatAnalyzerProvider(
            api_key="test-key",
            model="gpt-test",
            base_url="https://other.example.com/v1",
            client=client,
        )

        output = provider.analyze(AnalyzerInput(
            repo_id="/tmp/repo",
            branch="main",
            commit="abc123",
            baseline_change_id="baseline1",
            baseline_diff="-old\n+new\n",
            user_diff="-new\n+helper\n",
            current_preferences="",
        ))

        payload = calls[0]
        self.assertEqual(payload["model"], "gpt-test")
        self.assertFalse(payload["stream"])
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        messages = payload["messages"]
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("learns a user's coding style", messages[0]["content"])
        # json_object mode requires the prompt to mention "json" and include a
        # schema/example so the model knows the expected shape.
        self.assertIn("json", messages[0]["content"].lower())
        self.assertIn("preferences", messages[0]["content"])
        self.assertEqual(messages[1]["role"], "user")
        self.assertIn("baseline_diff", messages[1]["content"])
        self.assertEqual(output.preferences[0].instruction, "Prefer existing helpers before adding local wrappers.")

    def test_chat_provider_url_derived_from_base_url(self):
        provider = OpenAIChatAnalyzerProvider(
            api_key="test-key",
            base_url="https://openrouter.ai/api/v1/",
            client=_FakeChatClient([], "{}"),
        )

        self.assertEqual(provider.url, "https://openrouter.ai/api/v1/chat/completions")

    def test_chat_prompt_embeds_json_schema_and_example(self):
        # DeepSeek's json_object mode requires "json" in the prompt and a format
        # sample. The system prompt must carry the schema and an example.
        from stylus.analyzer import _chat_developer_prompt

        prompt = _chat_developer_prompt()
        self.assertIn("json", prompt.lower())
        # Schema fields present.
        for field in ("preferences", "obsolete_preferences", "notes", "topic", "instruction", "confidence"):
            self.assertIn(field, prompt)
        # An example output is embedded.
        self.assertIn("Example JSON output", prompt)
        self.assertIn("source_commit", prompt)

    def test_chat_provider_raises_when_no_content(self):
        # Simulate a refusal / empty content response.
        client = _FakeChatClient([], "")
        provider = OpenAIChatAnalyzerProvider(
            api_key="test-key",
            base_url="https://other.example.com/v1",
            client=client,
        )

        with self.assertRaises(RuntimeError) as ctx:
            provider.analyze(AnalyzerInput(
                repo_id="/tmp/repo",
                branch="main",
                commit="abc123",
                baseline_change_id="baseline1",
                baseline_diff="-old\n+new\n",
                user_diff="-new\n+helper\n",
                current_preferences="",
            ))
        self.assertIn("did not contain message content", str(ctx.exception))

    def test_provider_from_env_uses_responses_for_openai_official(self):
        for base_url in (
            "https://api.openai.com/v1",
            "https://api.openai.com/v1/",
        ):
            with patch.dict(os.environ, {
                "OPENAI_API_KEY": "test-key",
                "STYLUS_OPENAI_BASE_URL": base_url,
            }, clear=True):
                provider = provider_from_env()
            self.assertIsInstance(provider, OpenAIResponsesAnalyzerProvider, base_url)

    def test_is_openai_official_classifies_urls(self):
        self.assertTrue(_is_openai_official("https://api.openai.com/v1"))
        self.assertTrue(_is_openai_official("https://api.openai.com/v1/"))
        self.assertFalse(_is_openai_official("https://openrouter.ai/api/v1"))
        self.assertFalse(_is_openai_official("https://api.deepseek.com"))
        self.assertFalse(_is_openai_official(""))

    def test_provider_name_labels_chat_provider(self):
        provider = OpenAIChatAnalyzerProvider(
            api_key="test-key",
            model="gpt-test",
            base_url="https://other.example.com/v1",
            client=_FakeChatClient([], "{}"),
        )
        name = provider_name(provider)
        self.assertIn("openai-chat", name)
        self.assertIn("gpt-test", name)
        self.assertIn("other.example.com", name)


if __name__ == "__main__":
    unittest.main()
