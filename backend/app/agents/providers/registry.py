from __future__ import annotations

from .base import LLMProvider


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, LLMProvider] = {}

    def register(self, provider: LLMProvider, *aliases: str) -> None:
        for name in {provider.name, *aliases}:
            self._providers[name.lower()] = provider

    def get(self, name: str) -> LLMProvider:
        try:
            return self._providers[name.lower()]
        except KeyError as exc:
            available = ", ".join(sorted(self._providers)) or "无"
            raise KeyError(f"未知 LLM Provider：{name}；可用项：{available}") from exc

