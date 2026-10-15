from __future__ import annotations

import asyncio
from typing import Any

import httpx

from .base import (
    LLMAuthenticationError,
    LLMError,
    LLMRateLimitError,
    LLMRequest,
    LLMResponse,
    LLMTimeoutError,
    LLMUsage,
    ModelCapabilities,
)


class OpenAICompatibleProvider:
    name = "openai-compatible"
    adapter_version = "openai-compatible-v1"

    def __init__(self, *, endpoint: str, api_key: str, max_retries: int = 2) -> None:
        self.endpoint = endpoint
        self._api_key = api_key
        self.max_retries = max_retries

    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(streaming=True, tool_calls=True, native_structured_output=False)

    def build_payload(self, request: LLMRequest) -> dict[str, Any]:
        return {
            "model": request.model,
            "messages": [message.model_dump(mode="json") for message in request.messages],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }

    async def generate(self, request: LLMRequest) -> LLMResponse:
        payload = self.build_payload(request)
        data = await self._post(payload, request.timeout_seconds)
        choices = data.get("choices") or []
        if not choices:
            raise LLMError("模型响应缺少 choices")
        message = choices[0].get("message") or {}
        usage = data.get("usage") or {}
        details = usage.get("completion_tokens_details") or {}
        return LLMResponse(
            text=str(message.get("content") or ""),
            reasoning_text=str(message.get("reasoning_content") or message.get("thinking") or "") or None,
            request_id=data.get("id") or data.get("request_id"),
            finish_reason=choices[0].get("finish_reason"),
            usage=LLMUsage(
                input_tokens=int(usage.get("prompt_tokens") or 0),
                output_tokens=int(usage.get("completion_tokens") or 0),
                reasoning_tokens=int(details.get("reasoning_tokens") or usage.get("reasoning_tokens") or 0),
                total_tokens=int(usage.get("total_tokens") or 0),
            ),
            provider_metadata={"base_resp": data.get("base_resp")},
        )

    async def _post(self, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        if not self._api_key:
            raise LLMAuthenticationError("未配置模型 API Key", status_code=401)
        headers = {"Authorization": f"Bearer {self._api_key}"}
        for attempt in range(self.max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
                    response = await client.post(self.endpoint, headers=headers, json=payload)
            except httpx.TimeoutException as exc:
                if attempt < self.max_retries:
                    await asyncio.sleep(0.2 * (2**attempt))
                    continue
                raise LLMTimeoutError("模型请求超时", retryable=True) from exc
            if response.status_code in {401, 403}:
                raise LLMAuthenticationError("模型鉴权失败", status_code=response.status_code)
            if response.status_code == 429:
                if attempt < self.max_retries:
                    await asyncio.sleep(0.25 * (2**attempt))
                    continue
                raise LLMRateLimitError("模型请求被限流", retryable=True, status_code=429)
            if response.status_code >= 500 and attempt < self.max_retries:
                await asyncio.sleep(0.25 * (2**attempt))
                continue
            if response.is_error:
                raise LLMError(f"模型请求失败（HTTP {response.status_code}）", status_code=response.status_code)
            try:
                return response.json()
            except ValueError as exc:
                raise LLMError("模型返回的不是有效 JSON") from exc
        raise LLMError("模型请求失败")
