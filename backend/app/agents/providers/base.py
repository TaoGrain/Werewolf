from __future__ import annotations

from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field


class MessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class LLMMessage(BaseModel):
    role: MessageRole
    content: str


class LLMUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0


class ModelCapabilities(BaseModel):
    streaming: bool = False
    tool_calls: bool = False
    native_structured_output: bool = False
    multimodal: bool = False


class LLMRequest(BaseModel):
    messages: list[LLMMessage]
    model: str
    temperature: float = Field(default=0.2, ge=0, le=2)
    max_tokens: int = Field(default=8192, gt=0)
    timeout_seconds: float = Field(default=60, gt=0)
    thinking_enabled: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    def safe_snapshot(self) -> dict[str, Any]:
        """可持久化快照；类型中根本不含凭据。"""
        return self.model_dump(mode="json")


class LLMResponse(BaseModel):
    text: str
    reasoning_text: str | None = None
    request_id: str | None = None
    finish_reason: str | None = None
    safety_status: str | None = None
    usage: LLMUsage = Field(default_factory=LLMUsage)
    provider_metadata: dict[str, Any] = Field(default_factory=dict)


class LLMError(Exception):
    error_type = "provider_error"

    def __init__(self, message: str, *, retryable: bool = False, status_code: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


class LLMAuthenticationError(LLMError):
    error_type = "authentication_error"


class LLMRateLimitError(LLMError):
    error_type = "rate_limit_error"


class LLMTimeoutError(LLMError):
    error_type = "timeout_error"


class LLMContentSafetyError(LLMError):
    error_type = "content_safety_error"


@runtime_checkable
class LLMProvider(Protocol):
    name: str
    adapter_version: str

    async def generate(self, request: LLMRequest) -> LLMResponse: ...

    def capabilities(self) -> ModelCapabilities: ...
