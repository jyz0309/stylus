from __future__ import annotations

import json
import os
import shlex
import subprocess
from dataclasses import asdict, dataclass
from typing import Protocol
from urllib.parse import urlparse

from openai import APIConnectionError, APIStatusError, OpenAI


DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_OPENAI_MODEL = "gpt-5.5"


@dataclass(frozen=True)
class AnalyzerInput:
    repo_id: str
    branch: str
    commit: str
    baseline_change_id: str
    baseline_diff: str
    user_diff: str
    current_preferences: str


@dataclass(frozen=True)
class PreferenceUpdate:
    topic: str
    instruction: str
    confidence: str
    evidence: str
    source_commit: str


@dataclass(frozen=True)
class AnalyzerOutput:
    preferences: list[PreferenceUpdate]
    obsolete_preferences: list[str]
    notes: list[str]


class AnalyzerProvider(Protocol):
    def analyze(self, request: AnalyzerInput) -> AnalyzerOutput:
        ...


class FakeAnalyzerProvider:
    def analyze(self, request: AnalyzerInput) -> AnalyzerOutput:
        return AnalyzerOutput(
            preferences=[
                PreferenceUpdate(
                    topic="reviewed user corrections",
                    instruction=(
                        "Review user edits against the prior agent diff before repeating "
                        "similar implementation choices."
                    ),
                    confidence="low",
                    evidence=(
                        f"Commit {request.commit} changed an agent baseline on "
                        f"branch {request.branch}; inspect stored diffs for specifics."
                    ),
                    source_commit=request.commit,
                )
            ],
            obsolete_preferences=[],
            notes=[],
        )


class CommandAnalyzerProvider:
    def __init__(self, command: list[str]) -> None:
        if not command:
            raise ValueError("analyzer command cannot be empty")
        self.command = command

    def analyze(self, request: AnalyzerInput) -> AnalyzerOutput:
        result = subprocess.run(
            self.command,
            input=json.dumps(asdict(request)),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if result.returncode != 0:
            message = result.stderr.strip() or f"analyzer command failed with exit code {result.returncode}"
            raise RuntimeError(message)
        return parse_analyzer_output(result.stdout)


_PREFERENCE_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "description": "A learned coding-style preference with supporting evidence.",
    "properties": {
        "topic": {
            "type": "string",
            "description": "Short topic label, e.g. 'tests' or 'abstractions'.",
        },
        "instruction": {
            "type": "string",
            "description": "Imperative instruction describing the preferred behavior.",
        },
        "confidence": {
            "type": "string",
            "enum": ["low", "medium", "high"],
            "description": "How strongly the evidence supports the preference.",
        },
        "evidence": {
            "type": "string",
            "description": "What in the user diff supported this preference.",
        },
        "source_commit": {
            "type": "string",
            "description": "Git commit sha the preference was learned from.",
        },
    },
    "required": ["topic", "instruction", "confidence", "evidence", "source_commit"],
}

_OBSOLETE_PREFERENCE_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "description": "A previously-recorded preference to retire.",
    "properties": {
        "instruction": {
            "type": "string",
            "description": "Exact instruction text to remove.",
        },
        "reason": {
            "type": "string",
            "description": "Why this preference no longer applies.",
        },
    },
    "required": ["instruction", "reason"],
}

# The JSON Schema describing the analyzer's structured output. It is shaped to
# satisfy Structured Outputs strict mode: every object sets
# `additionalProperties: false` and lists every property in `required`.
ANALYZER_OUTPUT_JSON_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "description": "Stylus analysis result comparing an agent diff with a later user commit.",
    "properties": {
        "preferences": {
            "type": "array",
            "description": "New or reinforced coding-style preferences.",
            "items": _PREFERENCE_SCHEMA,
        },
        "obsolete_preferences": {
            "type": "array",
            "description": "Preferences to retire because they no longer match the user's style.",
            "items": _OBSOLETE_PREFERENCE_SCHEMA,
        },
        "notes": {
            "type": "array",
            "description": "Free-form observations the analyzer wants to record.",
            "items": {"type": "string"},
        },
    },
    "required": ["preferences", "obsolete_preferences", "notes"],
}

# Complete `text.format` value for `client.responses.create`, matching the
# OpenAI SDK `ResponseFormatTextJSONSchemaConfigParam` shape:
#   {"type": "json_schema", "name": ..., "strict": ..., "schema": ...}
ANALYZER_RESPONSE_TEXT_FORMAT: dict[str, object] = {
    "type": "json_schema",
    "name": "stylus_analysis",
    "description": "Stylus coding-style preference analysis.",
    "strict": True,
    "schema": ANALYZER_OUTPUT_JSON_SCHEMA,
}

# Complete `response_format` value for `client.chat.completions.create`, matching
# the OpenAI SDK `ResponseFormatJSONSchema` shape. This is exported for custom
# analyzers that still use Chat Completions.
ANALYZER_CHAT_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "stylus_analysis",
        "strict": True,
        "schema": ANALYZER_OUTPUT_JSON_SCHEMA,
    },
}

# Backward-compatible alias for callers that imported the old schema constant.
ANALYZER_OUTPUT_SCHEMA = ANALYZER_RESPONSE_TEXT_FORMAT


class OpenAIResponsesAnalyzerProvider:
    """Analyzer backed by OpenAI's Responses API via the official `openai` SDK."""

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_OPENAI_MODEL,
        base_url: str = DEFAULT_OPENAI_BASE_URL,
        timeout: float = 60.0,
        client: OpenAI | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("OpenAI API key is required")
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.timeout = timeout
        self._client = client or self._build_client()

    @property
    def url(self) -> str:
        return self.base_url.rstrip("/") + "/responses"

    def _build_client(self) -> OpenAI:
        return OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout,
        )

    def analyze(self, request: AnalyzerInput) -> AnalyzerOutput:
        request_json = json.dumps(asdict(request), ensure_ascii=False)
        try:
            response = self._client.responses.create(
                input=request_json,
                instructions=_developer_prompt(),
                model=self.model,
                stream=False,
                text={"format": ANALYZER_RESPONSE_TEXT_FORMAT},
            )
        except APIConnectionError as exc:
            raise RuntimeError(_url_error_message(exc)) from exc
        except APIStatusError as exc:
            body = _safe_response_text(exc)
            raise RuntimeError(
                f"OpenAI API request failed: HTTP {exc.status_code}: {body}"
            ) from exc
        return parse_analyzer_output(_extract_response_text(response))


class OpenAIChatAnalyzerProvider:
    """Analyzer backed by OpenAI's Chat Completions API.

    Used automatically when `base_url` is not the official OpenAI endpoint, since
    most third-party/OpenAI-compatible providers (e.g. DeepSeek, OpenRouter)
    implement `/chat/completions` but not the newer `/responses` endpoint.

    Uses `response_format={"type": "json_object"}` for broad compatibility and
    embeds the full JSON schema plus an example in the system prompt so the model
    knows the expected shape (DeepSeek's json_object mode requires "json" in the
    prompt and a format sample).
    """

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_OPENAI_MODEL,
        base_url: str = DEFAULT_OPENAI_BASE_URL,
        timeout: float = 60.0,
        client: OpenAI | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("OpenAI API key is required")
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.timeout = timeout
        self._client = client or self._build_client()

    @property
    def url(self) -> str:
        return self.base_url.rstrip("/") + "/chat/completions"

    def _build_client(self) -> OpenAI:
        return OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout,
        )

    def analyze(self, request: AnalyzerInput) -> AnalyzerOutput:
        request_json = json.dumps(asdict(request), ensure_ascii=False)
        messages = [
            {"role": "system", "content": _chat_developer_prompt()},
            {"role": "user", "content": request_json},
        ]
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                stream=False,
                response_format={"type": "json_object"},
            )
        except APIConnectionError as exc:
            raise RuntimeError(_url_error_message(exc)) from exc
        except APIStatusError as exc:
            body = _safe_response_text(exc)
            raise RuntimeError(
                f"OpenAI API request failed: HTTP {exc.status_code}: {body}"
            ) from exc
        return parse_analyzer_output(_extract_chat_content(response))


def provider_from_env() -> AnalyzerProvider:
    command = os.environ.get("STYLUS_ANALYZER_CMD", "").strip()
    if command:
        return CommandAnalyzerProvider(shlex.split(command))
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if api_key:
        model = (
            os.environ.get("STYLUS_OPENAI_MODEL", DEFAULT_OPENAI_MODEL).strip()
            or DEFAULT_OPENAI_MODEL
        )
        base_url = (
            os.environ.get("STYLUS_OPENAI_BASE_URL", DEFAULT_OPENAI_BASE_URL).strip()
            or DEFAULT_OPENAI_BASE_URL
        )
        if _is_openai_official(base_url):
            return OpenAIResponsesAnalyzerProvider(api_key=api_key, model=model, base_url=base_url)
        return OpenAIChatAnalyzerProvider(api_key=api_key, model=model, base_url=base_url)
    return FakeAnalyzerProvider()


def provider_name(provider: AnalyzerProvider) -> str:
    """Return a human-readable label identifying the analyzer provider.

    Used by `stylus analyze --debug` to show which backend handled the analysis.
    """
    if isinstance(provider, CommandAnalyzerProvider):
        return f"command ({shlex.join(provider.command)})"
    if isinstance(provider, OpenAIResponsesAnalyzerProvider):
        return f"openai-responses (model={provider.model}, base_url={provider.base_url})"
    if isinstance(provider, OpenAIChatAnalyzerProvider):
        return f"openai-chat (model={provider.model}, base_url={provider.base_url})"
    if isinstance(provider, FakeAnalyzerProvider):
        return "fake (no OPENAI_API_KEY or STYLUS_ANALYZER_CMD set)"
    return type(provider).__name__


_PREVIEW_LIMIT = 200  # characters of analyzer output shown in error messages
_CONFIDENCE_VALUES = {"low", "medium", "high"}
_REQUIRED_PREFERENCE_FIELDS = ("topic", "instruction", "confidence", "evidence", "source_commit")


def _preview(text: str) -> str:
    """Return a short, single-line preview of `text` for error messages."""
    one_line = " ".join(text.split())  # collapse whitespace/newlines
    if len(one_line) <= _PREVIEW_LIMIT:
        return one_line
    return one_line[:_PREVIEW_LIMIT] + "…"


def _parse_preference(item: object) -> PreferenceUpdate:
    """Validate a single preference object, raising on structural problems."""
    if not isinstance(item, dict):
        raise TypeError(f"each preference must be an object, got {type(item).__name__}")
    missing = [k for k in _REQUIRED_PREFERENCE_FIELDS if k not in item]
    if missing:
        raise KeyError(f"missing required fields: {', '.join(missing)}")
    confidence = item["confidence"]
    if confidence not in _CONFIDENCE_VALUES:
        raise ValueError(
            f"confidence must be one of {sorted(_CONFIDENCE_VALUES)}, got {confidence!r}"
        )
    return PreferenceUpdate(
        topic=str(item["topic"]),
        instruction=str(item["instruction"]),
        confidence=str(confidence),
        evidence=str(item["evidence"]),
        source_commit=str(item["source_commit"]),
    )


def parse_analyzer_output(raw: str) -> AnalyzerOutput:
    """Parse and validate the analyzer's JSON response.

    Wraps all structural failures into a RuntimeError with a clear message and
    a short preview of the offending output, so the caller never sees a bare
    JSONDecodeError/KeyError/TypeError traceback.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Analyzer did not return valid JSON ({exc.msg} at line {exc.lineno} col {exc.colno}). "
            f"Output preview: {_preview(raw)}"
        ) from exc

    if not isinstance(data, dict):
        raise RuntimeError(
            f"Analyzer JSON must be an object, got {type(data).__name__}. "
            f"Output preview: {_preview(raw)}"
        )

    try:
        preferences = [
            _parse_preference(item) for item in data.get("preferences", [])
        ]
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(
            f"Analyzer 'preferences' field is malformed: {exc}. "
            f"Output preview: {_preview(raw)}"
        ) from exc

    try:
        obsolete = [
            item["instruction"] if isinstance(item, dict) else str(item)
            for item in data.get("obsolete_preferences", [])
        ]
    except (KeyError, TypeError) as exc:
        raise RuntimeError(
            f"Analyzer 'obsolete_preferences' field is malformed: {exc}. "
            f"Output preview: {_preview(raw)}"
        ) from exc

    notes = [str(item) for item in data.get("notes", [])]
    return AnalyzerOutput(preferences=preferences, obsolete_preferences=obsolete, notes=notes)


def _developer_prompt() -> str:
    return (
        "You are Stylus, an analyzer that learns a user's coding style by comparing "
        "a prior agent-generated diff with a later user commit diff. Extract durable, "
        "general coding preferences only. Prefer concrete imperative instructions. "
        "Do not record project-specific facts unless they reveal a reusable preference. "
        "Mark confidence low when evidence is thin. Return only JSON matching the schema."
    )


# A concrete example of the expected JSON output, embedded into the Chat
# Completions system prompt so endpoints using json_object mode (e.g. DeepSeek)
# have a format sample to imitate.
_ANALYZER_OUTPUT_EXAMPLE: dict[str, object] = {
    "preferences": [
        {
            "topic": "change scope",
            "instruction": "Prefer small, localized changes when correcting output.",
            "confidence": "medium",
            "evidence": "User commit narrowed the previous diff.",
            "source_commit": "abc123",
        }
    ],
    "obsolete_preferences": [],
    "notes": [],
}


def _chat_developer_prompt() -> str:
    """System prompt for the Chat Completions provider.

    Unlike the Responses provider (which enforces the schema via
    `response_format` strict mode), the Chat provider uses `json_object` mode
    for broad compatibility. That mode requires the prompt to mention "json"
    and include a format sample, so we embed the full JSON schema and an example.
    """
    schema_json = json.dumps(ANALYZER_OUTPUT_JSON_SCHEMA, ensure_ascii=False)
    example_json = json.dumps(_ANALYZER_OUTPUT_EXAMPLE, ensure_ascii=False, indent=2)
    return (
        f"{_developer_prompt()}\n\n"
        "Respond with a single JSON object matching this JSON schema:\n\n"
        f"{schema_json}\n\n"
        "Example JSON output:\n\n"
        f"{example_json}"
    )


def _extract_response_text(response: object) -> str:
    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str):
        return output_text

    if isinstance(response, dict):
        output_text = response.get("output_text")
        if isinstance(output_text, str):
            return output_text
        output = response.get("output")
    else:
        output = getattr(response, "output", None)

    if isinstance(output, list):
        for item in output:
            content = item.get("content") if isinstance(item, dict) else getattr(item, "content", None)
            if not isinstance(content, list):
                continue
            for part in content:
                part_type = part.get("type") if isinstance(part, dict) else getattr(part, "type", None)
                text = part.get("text") if isinstance(part, dict) else getattr(part, "text", None)
                if part_type == "output_text" and isinstance(text, str):
                    return text

    raise RuntimeError("OpenAI response did not contain output text")


def _extract_chat_content(response: object) -> str:
    """Pull the assistant message text out of a Chat Completions response."""
    choices = getattr(response, "choices", None)
    if choices is None and isinstance(response, dict):
        choices = response.get("choices")
    if not choices:
        raise RuntimeError("OpenAI chat response did not contain any choices")

    first = choices[0]
    message = first.get("message") if isinstance(first, dict) else getattr(first, "message", None)
    content = message.get("content") if isinstance(message, dict) else getattr(message, "content", None)
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("OpenAI chat response did not contain message content")
    return content


def _is_openai_official(base_url: str) -> bool:
    """Return True when `base_url` points at the official OpenAI API.

    Non-official endpoints are routed to the Chat Completions provider because
    most third-party/OpenAI-compatible providers do not implement the newer
    `/responses` endpoint.
    """
    host = urlparse(base_url).hostname or ""
    return host == "api.openai.com"


def _url_error_message(exc: BaseException) -> str:
    return f"OpenAI API request failed: {exc}"


def _safe_response_text(exc: APIStatusError) -> str:
    response = getattr(exc, "response", None)
    if response is None:
        return str(exc)
    try:
        return response.text
    except Exception:
        return str(exc)
