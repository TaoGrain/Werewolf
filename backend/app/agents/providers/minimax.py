from __future__ import annotations

from .base import LLMRequest, LLMResponse, LLMUsage, ModelCapabilities
from .openai_compatible import OpenAICompatibleProvider


class MiniMaxProvider(OpenAICompatibleProvider):
    name = "minimax"
    adapter_version = "minimax-m3-v2"

    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(streaming=True, native_structured_output=False)

    def build_payload(self, request: LLMRequest) -> dict[str, object]:
        payload = super().build_payload(request)
        payload["thinking"] = {
            "type": "adaptive" if request.thinking_enabled else "disabled"
        }
        # 该参数只负责把思考内容与最终回答分开，不负责开启或关闭思考。
        payload["reasoning_split"] = True
        return payload

    async def generate(self, request: LLMRequest) -> LLMResponse:
        data = await self._post(
            self.build_payload(request),
            request.timeout_seconds,
        )
        choices = data.get("choices") or []
        if not choices:
            from .base import LLMError

            raise LLMError("MiniMax 响应缺少 choices")
        message = choices[0].get("message") or {}
        usage = data.get("usage") or {}
        reasoning_tokens = int(
            (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
            or usage.get("reasoning_tokens")
            or 0
        )
        return LLMResponse(
            text=str(message.get("content") or ""),
            reasoning_text=str(message.get("reasoning_content") or message.get("thinking") or "") or None,
            request_id=data.get("id") or data.get("request_id"),
            finish_reason=choices[0].get("finish_reason"),
            safety_status=(data.get("base_resp") or {}).get("status_msg"),
            usage=LLMUsage(
                input_tokens=int(usage.get("prompt_tokens") or 0),
                output_tokens=int(usage.get("completion_tokens") or 0),
                reasoning_tokens=reasoning_tokens,
                total_tokens=int(usage.get("total_tokens") or 0),
            ),
            provider_metadata={
                "base_resp": data.get("base_resp"),
                "input_sensitive": data.get("input_sensitive"),
                "output_sensitive": data.get("output_sensitive"),
            },
        )
