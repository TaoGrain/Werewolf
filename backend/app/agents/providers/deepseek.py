from __future__ import annotations

from .base import LLMRequest, ModelCapabilities
from .openai_compatible import OpenAICompatibleProvider


class DeepSeekProvider(OpenAICompatibleProvider):
    """DeepSeek Chat Completions 适配器，始终请求原生 JSON 输出。"""

    name = "deepseek"
    adapter_version = "deepseek-json-v1"

    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            streaming=True,
            tool_calls=True,
            native_structured_output=True,
        )

    def build_payload(self, request: LLMRequest) -> dict[str, object]:
        payload = super().build_payload(request)
        payload["response_format"] = {"type": "json_object"}
        payload["thinking"] = {
            "type": "enabled" if request.thinking_enabled else "disabled"
        }
        return payload
