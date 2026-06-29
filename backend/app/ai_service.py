from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from functools import lru_cache
from textwrap import dedent
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .settings import settings

LOGGER = logging.getLogger(__name__)

ProviderName = Literal["nvidia"]
LanguageCode = Literal["fa", "en"]

DEFAULT_NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_NVIDIA_MODEL = "openai/gpt-oss-120b"
DEFAULT_NVIDIA_MODELS = (
    "openai/gpt-oss-120b",
    "nvidia/nemotron-3-super-120b-a12b",
    "z-ai/glm-5.1",
    "qwen/qwen3-next-80b-a3b-instruct",
    "qwen/qwen3.5-397b-a17b",
    "moonshotai/kimi-k2.6",
    "minimaxai/minimax-m2.7",
    "deepseek-ai/deepseek-v4-flash",
    "google/gemma-4-31b-it",
)

NVIDIA_MODEL_OPTIONS: dict[str, dict[str, object]] = {
    "openai/gpt-oss-120b": {
        "temperature": 1,
        "top_p": 1,
        "max_tokens": 4096,
    },
    "nvidia/nemotron-3-super-120b-a12b": {
        "temperature": 1,
        "top_p": 0.95,
        "max_tokens": 16384,
        "extra_body": {
            "chat_template_kwargs": {"enable_thinking": True},
            "reasoning_budget": 16384,
        },
    },
    "z-ai/glm-5.1": {
        "temperature": 1,
        "top_p": 1,
        "max_tokens": 16384,
    },
    "qwen/qwen3-next-80b-a3b-instruct": {
        "temperature": 0.6,
        "top_p": 0.7,
        "max_tokens": 4096,
    },
    "qwen/qwen3.5-397b-a17b": {
        "temperature": 0.6,
        "top_p": 0.95,
        "max_tokens": 16384,
        "presence_penalty": 0,
        "extra_body": {
            "top_k": 20,
            "repetition_penalty": 1,
        },
    },
    "moonshotai/kimi-k2.6": {
        "temperature": 1,
        "top_p": 1,
        "max_tokens": 16384,
    },
    "minimaxai/minimax-m2.7": {
        "temperature": 1,
        "top_p": 0.95,
        "max_tokens": 8192,
    },
    "deepseek-ai/deepseek-v4-flash": {
        "temperature": 1,
        "top_p": 0.95,
        "max_tokens": 16384,
        "extra_body": {
            "chat_template_kwargs": {
                "thinking": True,
                "reasoning_effort": "high",
            },
        },
    },
    "google/gemma-4-31b-it": {
        "temperature": 1,
        "top_p": 0.95,
        "max_tokens": 16384,
        "extra_body": {
            "chat_template_kwargs": {"enable_thinking": True},
        },
    },
}


class AIServiceError(Exception):
    def __init__(self, message: str, provider: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.provider = provider


class AIConfigurationError(AIServiceError):
    pass


class AIValidationError(AIServiceError):
    pass


class AIRemoteError(AIServiceError):
    pass


class AIForbiddenError(AIRemoteError):
    pass


class AIRateLimitError(AIRemoteError):
    pass


class AITimeoutError(AIRemoteError):
    pass


class AIProviderError(AIRemoteError):
    pass


class AIChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)

    @field_validator("content")
    @classmethod
    def normalize_content(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("content cannot be empty")
        return cleaned


class AIChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language: LanguageCode = "fa"
    provider: ProviderName = "nvidia"
    model: str | None = Field(default=None, min_length=1, max_length=160)
    level: str = Field(default="station", min_length=1, max_length=96)
    index: str = Field(default="spi3", min_length=1, max_length=64)
    date: str = Field(default="2020-01", min_length=7, max_length=7)
    region_id: str | None = Field(default=None, max_length=128)
    question: str = Field(min_length=1, max_length=2000)
    history: list[AIChatMessage] = Field(default_factory=list, max_length=10)

    @field_validator("level", "index", "date", "region_id", "model", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, str):
            cleaned = value.strip()
            return cleaned or None
        return value

    @field_validator("question")
    @classmethod
    def normalize_question(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("question cannot be empty")
        return cleaned


class AIChatResponse(BaseModel):
    status: Literal["success"] = "success"
    provider: ProviderName
    model: str
    answer: str


@dataclass(frozen=True)
class NvidiaChatClient:
    api_key: str
    model: str
    base_url: str = DEFAULT_NVIDIA_BASE_URL
    timeout_seconds: int = 60
    temperature: float = 0.2
    top_p: float = 0.7
    max_tokens: int = 2048

    @property
    def provider_name(self) -> str:
        return "nvidia"

    def _payload(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "max_tokens": self.max_tokens,
            "stream": False,
        }
        model_options = NVIDIA_MODEL_OPTIONS.get(self.model, {})
        for key in ("temperature", "top_p", "max_tokens", "presence_penalty"):
            if key in model_options:
                payload[key] = model_options[key]
        extra_body = model_options.get("extra_body")
        if isinstance(extra_body, dict):
            payload.update(extra_body)
        return payload

    @staticmethod
    def _extract_content(payload: dict[str, Any]) -> str:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise AIProviderError("NVIDIA returned no completion choices.", "nvidia")
        first = choices[0]
        message = first.get("message") if isinstance(first, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise AIProviderError("NVIDIA returned an empty completion.", "nvidia")
        return content.strip()

    @staticmethod
    def _forbidden_message(message: str) -> str:
        normalized = (message or "").strip()
        lowered = normalized.lower()
        if (
            not normalized
            or normalized == "Forbidden"
            or "<html" in lowered
            or "<h1>403 forbidden</h1>" in lowered
        ):
            return (
                "NVIDIA rejected access with HTTP 403. Check the API key, "
                "selected model, account permissions, or current network location."
            )
        return normalized

    def complete(self, messages: list[dict[str, str]]) -> str:
        url = f"{self.base_url.rstrip('/')}/chat/completions"
        body = json.dumps(self._payload(messages), ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                response_body = response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            message = error.read().decode("utf-8", errors="replace") or str(error)
            if error.code == 403:
                raise AIForbiddenError(self._forbidden_message(message), "nvidia") from error
            if error.code == 429:
                raise AIRateLimitError(message, "nvidia") from error
            raise AIProviderError(message, "nvidia") from error
        except TimeoutError as error:
            raise AITimeoutError("NVIDIA request timed out.", "nvidia") from error
        except urllib.error.URLError as error:
            raise AIProviderError(str(error), "nvidia") from error

        try:
            parsed = json.loads(response_body)
        except json.JSONDecodeError as error:
            raise AIProviderError("NVIDIA returned invalid JSON.", "nvidia") from error
        if not isinstance(parsed, dict):
            raise AIProviderError("NVIDIA returned an invalid response.", "nvidia")
        return self._extract_content(parsed)


CHAT_SYSTEM_PROMPT = dedent(
    """
    You are a conversational drought monitoring and hydroclimate data assistant.

    Answer questions about the selected dashboard state, region or station, drought
    class, SPI/SPEI/SSI or other hydroclimate index values, time-series behavior,
    trend diagnostics, prediction summaries, and map-wide drought distribution.

    Rules:
    - Treat the supplied dashboard context as the authoritative source.
    - Never invent measurements, feature names, dates, model results, or causal claims.
    - If the context is insufficient, say exactly what is missing.
    - Distinguish statistical trend, correlation, prediction, and causation.
    - Explain drought classes D0-D4 when useful.
    - Keep answers concise and practical for a dashboard user.
    - Return valid JSON only, with exactly one property named "answer".
    """
).strip()


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _extract_json_object(text: str) -> dict[str, Any]:
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = candidate.strip("`")
        if candidate.startswith("json"):
            candidate = candidate[4:].strip()
    first = candidate.find("{")
    last = candidate.rfind("}")
    if first >= 0 and last > first:
        candidate = candidate[first:last + 1]
    parsed = json.loads(candidate)
    if not isinstance(parsed, dict):
        raise ValueError("LLM output was not a JSON object.")
    return parsed


def _first_nonempty_string(payload: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _allowed_nvidia_models() -> tuple[str, ...]:
    configured = tuple(
        model.strip()
        for model in settings.nvidia_models.split(",")
        if model.strip()
    )
    default_model = settings.nvidia_model.strip() or DEFAULT_NVIDIA_MODEL
    return tuple(dict.fromkeys((default_model, *(configured or DEFAULT_NVIDIA_MODELS))))


def _model_label(model: str) -> str:
    return model.split("/", 1)[1] if "/" in model else model


def get_ai_options() -> dict[str, Any]:
    models = [
        {
            "id": model,
            "label": _model_label(model),
            "free": True,
            "usage_hint": "NVIDIA API Catalog",
        }
        for model in _allowed_nvidia_models()
    ]
    default_model = settings.nvidia_model.strip() or DEFAULT_NVIDIA_MODEL
    return {
        "default_provider": "nvidia",
        "providers": [
            {
                "id": "nvidia",
                "label": "NVIDIA",
                "enabled": bool(settings.nvidia_api_key),
                "default_model": default_model,
                "models": models,
            }
        ],
    }


class DroughtAIService:
    def __init__(self) -> None:
        self.api_key = settings.nvidia_api_key
        self.base_url = settings.nvidia_base_url or DEFAULT_NVIDIA_BASE_URL
        self.timeout_seconds = settings.ai_timeout_seconds

    def _client_for_request(self, request: AIChatRequest) -> NvidiaChatClient:
        if not self.api_key:
            raise AIConfigurationError("NVIDIA_API_KEY is missing.", "nvidia")
        model = request.model or settings.nvidia_model or DEFAULT_NVIDIA_MODEL
        if model not in _allowed_nvidia_models():
            raise AIValidationError(f"Model '{model}' is not enabled for NVIDIA.", "nvidia")
        return NvidiaChatClient(
            api_key=self.api_key,
            model=model,
            base_url=self.base_url,
            timeout_seconds=self.timeout_seconds,
        )

    @staticmethod
    def _messages(request: AIChatRequest, context: dict[str, Any]) -> list[dict[str, str]]:
        system_prompt = CHAT_SYSTEM_PROMPT
        system_prompt += "\n\nRespond in English." if request.language == "en" else "\n\nRespond in Persian (Farsi)."
        return [
            {"role": "system", "content": system_prompt},
            *[
                {"role": message.role, "content": message.content}
                for message in request.history
            ],
            {
                "role": "user",
                "content": dedent(
                    f"""
                    Dashboard context:
                    {_json_dumps(context)}

                    Current user question:
                    {request.question}

                    Return exactly:
                    {{"answer": "Your answer in the requested language"}}
                    """
                ).strip(),
            },
        ]

    def chat(self, request: AIChatRequest, context: dict[str, Any]) -> AIChatResponse:
        client = self._client_for_request(request)
        messages = self._messages(request, context)
        try:
            raw_content = client.complete(messages)
        except AIServiceError:
            raise
        except Exception as error:
            LOGGER.exception("Unexpected NVIDIA chat failure")
            raise AIProviderError("AI provider request failed unexpectedly.", "nvidia") from error

        try:
            parsed = _extract_json_object(raw_content)
            answer = _first_nonempty_string(parsed, ("answer", "response", "analysis", "پاسخ"))
            if not answer:
                raise ValueError("No answer field")
        except Exception:
            repair_messages = [
                *messages,
                {"role": "assistant", "content": raw_content},
                {
                    "role": "user",
                    "content": 'Return one valid JSON object only: {"answer": "..."} with a non-empty answer.',
                },
            ]
            try:
                repaired = _extract_json_object(client.complete(repair_messages))
                answer = _first_nonempty_string(repaired, ("answer", "response", "analysis", "پاسخ"))
                if not answer:
                    raise ValueError("No answer field")
            except AIServiceError:
                raise
            except Exception as error:
                raise AIProviderError("NVIDIA returned an invalid chat response.", "nvidia") from error

        return AIChatResponse(provider="nvidia", model=client.model, answer=answer)


@lru_cache(maxsize=1)
def get_ai_service() -> DroughtAIService:
    return DroughtAIService()
