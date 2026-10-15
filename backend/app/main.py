from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agents import AgentGateway
from app.agents.providers import (
    DeepSeekProvider,
    FakeProvider,
    MiniMaxProvider,
    OpenAICompatibleProvider,
    ProviderRegistry,
)
from app.api import router
from app.config import Settings, get_settings
from app.engine import GameEngine
from app.persistence import Database, upgrade_database
from app.services import DatabaseCallRecorder, GameApplicationService


def create_app(settings: Settings | None = None, database: Database | None = None) -> FastAPI:
    settings = settings or get_settings()
    database = database or Database(settings.database_url)
    upgrade_database(database)

    registry = ProviderRegistry()
    registry.register(FakeProvider())
    minimax = MiniMaxProvider(
        endpoint=settings.minimax_api_url,
        api_key=settings.read_minimax_api_key(),
        max_retries=settings.llm_provider_max_retries,
    )
    registry.register(minimax)
    deepseek = DeepSeekProvider(
        endpoint=settings.deepseek_api_url,
        api_key=settings.read_deepseek_api_key(),
        max_retries=settings.llm_provider_max_retries,
    )
    registry.register(deepseek)
    compatible_endpoint = settings.openai_compatible_api_url or settings.minimax_api_url
    compatible_key = (
        settings.openai_compatible_api_key.get_secret_value()
        if settings.openai_compatible_api_key
        else ""
    )
    registry.register(
        OpenAICompatibleProvider(
            endpoint=compatible_endpoint,
            api_key=compatible_key,
            max_retries=settings.llm_provider_max_retries,
        ),
        "openai_compatible",
    )
    provider = registry.get(settings.llm_provider)
    recorder = DatabaseCallRecorder(database)
    gateway = AgentGateway(
        provider,
        settings.llm_model,
        recorder,
        max_tokens=settings.llm_max_tokens,
        timeout_seconds=settings.llm_timeout_seconds,
        provider_concurrency_limit=settings.llm_provider_concurrency_limit,
        max_total_attempts=settings.llm_gateway_max_total_attempts,
        history_max_messages=settings.llm_history_max_messages,
        history_max_characters=settings.llm_history_max_characters,
    )
    model_gateways = {
        "minimaxM3": AgentGateway(
            minimax,
            settings.minimax_model,
            recorder,
            max_tokens=settings.llm_max_tokens,
            timeout_seconds=settings.llm_timeout_seconds,
            provider_concurrency_limit=settings.llm_provider_concurrency_limit,
            max_total_attempts=settings.llm_gateway_max_total_attempts,
            history_max_messages=settings.llm_history_max_messages,
            history_max_characters=settings.llm_history_max_characters,
        ),
        "deepseekv4pro": AgentGateway(
            deepseek,
            settings.deepseek_pro_model,
            recorder,
            max_tokens=settings.llm_max_tokens,
            timeout_seconds=settings.llm_timeout_seconds,
            provider_concurrency_limit=settings.llm_provider_concurrency_limit,
            max_total_attempts=settings.llm_gateway_max_total_attempts,
            history_max_messages=settings.llm_history_max_messages,
            history_max_characters=settings.llm_history_max_characters,
        ),
        "deepseekv4flash": AgentGateway(
            deepseek,
            settings.deepseek_flash_model,
            recorder,
            max_tokens=settings.llm_max_tokens,
            timeout_seconds=settings.llm_timeout_seconds,
            provider_concurrency_limit=settings.llm_provider_concurrency_limit,
            max_total_attempts=settings.llm_gateway_max_total_attempts,
            history_max_messages=settings.llm_history_max_messages,
            history_max_characters=settings.llm_history_max_characters,
        ),
    }

    # FakeProvider 是本地开发与自动验收的离线总开关。即使前端提交了
    # 某个生产模型键，也不能在该模式下意外访问外部供应商。
    if provider.name == "fake":
        offline_models = {
            "minimaxM3": settings.minimax_model,
            "deepseekv4pro": settings.deepseek_pro_model,
            "deepseekv4flash": settings.deepseek_flash_model,
        }
        model_gateways = {
            model_key: AgentGateway(
                provider,
                model_name,
                recorder,
                max_tokens=settings.llm_max_tokens,
                timeout_seconds=settings.llm_timeout_seconds,
                provider_concurrency_limit=settings.llm_provider_concurrency_limit,
                max_total_attempts=settings.llm_gateway_max_total_attempts,
                history_max_messages=settings.llm_history_max_messages,
                history_max_characters=settings.llm_history_max_characters,
            )
            for model_key, model_name in offline_models.items()
        }

    app = FastAPI(title=settings.app_name, version="0.1.0")
    app.state.database = database
    app.state.provider_registry = registry
    app.state.game_service = GameApplicationService(
        database,
        GameEngine(),
        gateway,
        gateways=model_gateways,
        phase_timeout_seconds=settings.llm_phase_timeout_seconds,
        phase_output_token_budget=settings.llm_phase_output_token_budget,
        speech_temperature=settings.llm_speech_temperature,
        vote_temperature=settings.llm_vote_temperature,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.frontend_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    return app


app = create_app()
