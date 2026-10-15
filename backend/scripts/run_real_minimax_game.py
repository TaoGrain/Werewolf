"""运行一局真实 MiniMax-M3 对局，仅输出脱敏统计 JSON。"""

from __future__ import annotations

import asyncio
import json
import statistics
import time
from collections import Counter
from pathlib import Path
from typing import Any

from app.agents import AgentGateway
from app.agents.providers import MiniMaxProvider
from app.config import Settings
from app.engine import GameEngine
from app.persistence import Base, Database, Repository
from app.services import DatabaseCallRecorder, GameApplicationService


def percentile(values: list[int], percent: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * percent)))
    return ordered[index]


def has_forbidden_key(value: Any) -> bool:
    forbidden = {
        "api_key",
        "apikey",
        "authorization",
        "reasoning_content",
        "thinking",
        "chain_of_thought",
    }
    if isinstance(value, dict):
        return any(str(key).lower() in forbidden or has_forbidden_key(item) for key, item in value.items())
    if isinstance(value, list):
        return any(has_forbidden_key(item) for item in value)
    return False


def request_without_thinking_control(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    """请求中的 thinking 是模式控制项，不应被误判为思维链泄露。"""
    return {
        key: value
        for key, value in (snapshot or {}).items()
        if str(key).lower() != "thinking"
    }


def inspect_thinking_mode(calls: list[Any]) -> dict[str, Any]:
    disabled_requests = 0
    invalid_requests = 0
    reasoning_response_count = 0
    tagged_reasoning_count = 0
    reasoning_tokens = 0
    for call in calls:
        request = call.request_snapshot or {}
        if (
            request.get("thinking") == {"type": "disabled"}
            or request.get("thinking_mode") == "disabled"
        ):
            disabled_requests += 1
        else:
            invalid_requests += 1
        response = call.response_snapshot or {}
        if str(response.get("reasoning_text") or "").strip():
            reasoning_response_count += 1
        final_text = str(call.final_text or "")
        if "<think>" in final_text.lower() or "<analysis>" in final_text.lower():
            tagged_reasoning_count += 1
        reasoning_tokens += int(call.reasoning_tokens or 0)
    return {
        "checked_calls": len(calls),
        "disabled_request_count": disabled_requests,
        "invalid_request_count": invalid_requests,
        "reasoning_response_count": reasoning_response_count,
        "tagged_reasoning_count": tagged_reasoning_count,
        "reasoning_tokens": reasoning_tokens,
        "passed": bool(calls)
        and invalid_requests == 0
        and reasoning_response_count == 0
        and tagged_reasoning_count == 0
        and reasoning_tokens == 0,
    }


def inspect_player_views(calls: list[Any]) -> dict[str, Any]:
    forbidden_view_keys = {
        "role_assignments",
        "role_states",
        "wolf_actions",
        "wolf_target_id",
        "seer_action",
        "witch_action",
        "processed_commands",
    }
    checked = 0
    invalid = 0
    sessions: set[str] = set()
    for call in calls:
        messages = (call.request_snapshot or {}).get("messages", [])
        found_view = False
        for message in messages:
            if message.get("role") != "user":
                continue
            content = str(message.get("content", ""))
            marker = "你的专属玩家视图（仅可使用这些信息）：\n"
            if marker not in content:
                continue
            try:
                view = json.loads(content.split(marker, 1)[1])
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(view, dict):
                continue
            found_view = True
            checked += 1
            sessions.add(str(view.get("session_id")))
            if forbidden_view_keys.intersection(view) or view.get("player_id") != call.player_id:
                invalid += 1
            break
        if not found_view:
            # 修复调用仍包含此前的初始 user 消息；若完全没有视图则记异常。
            invalid += 1
    return {
        "checked_calls": checked,
        "invalid_view_count": invalid,
        "distinct_sessions": len(sessions),
        "passed": invalid == 0,
    }


async def main() -> None:
    settings = Settings()
    database_path = settings.minimax_run_db
    if not database_path.is_absolute():
        database_path = (Path(__file__).resolve().parents[2] / database_path).resolve()
    api_key = settings.read_minimax_api_key()
    if not api_key:
        raise RuntimeError("MiniMax API Key 未配置")

    database = Database.sqlite(database_path)
    Base.metadata.create_all(database.engine)
    provider = MiniMaxProvider(
        endpoint=settings.minimax_api_url,
        api_key=api_key,
        max_retries=settings.minimax_provider_max_retries,
    )
    gateway = AgentGateway(
        provider,
        settings.minimax_model,
        DatabaseCallRecorder(database),
        max_tokens=settings.llm_max_tokens,
        timeout_seconds=settings.llm_timeout_seconds,
    )
    service = GameApplicationService(
        database,
        GameEngine(),
        gateway,
        gateways={"minimaxM3": gateway},
    )

    run_started = time.perf_counter()
    game = service.create_game(
        random_seed=20260720,
        god_view=True,
        allow_private_inspection=True,
        model_key="minimaxM3",
        thinking_enabled=False,
    )
    service.start_game(game.id, "real-minimax-start")
    phase_runs: list[dict[str, Any]] = []
    stopped_reason: str | None = None

    for step in range(settings.minimax_run_max_steps):
        current = service.load_game(game.id)
        if current.phase.value == "FINISHED":
            break
        completed = False
        for retry in range(1, settings.minimax_run_max_phase_retries + 1):
            command_id = f"real-minimax-{step:02d}-try-{retry}"
            before = service.load_game(game.id)
            started = time.perf_counter()
            result = await service.advance_now(game.id, command_id)
            after = service.load_game(game.id)
            record = {
                "step": step,
                "retry": retry,
                "phase_before": before.phase.value,
                "phase_after": after.phase.value,
                "status": result.status,
                "elapsed_ms": round((time.perf_counter() - started) * 1000),
            }
            if result.status == "FAILED":
                error = result.result if isinstance(result.result, dict) else {}
                record["error_type"] = error.get("type", "unknown")
            phase_runs.append(record)
            print(
                "PHASE"
                f" step={step} retry={retry} before={record['phase_before']}"
                f" after={record['phase_after']} status={record['status']}"
                f" elapsed_ms={record['elapsed_ms']}",
                flush=True,
            )
            if result.status == "SUCCEEDED":
                completed = True
                break
        if not completed:
            stopped_reason = (
                f"阶段 {current.phase.value} 连续 "
                f"{settings.minimax_run_max_phase_retries} 次失败"
            )
            break
    else:
        stopped_reason = f"超过最大步骤数 {settings.minimax_run_max_steps}"

    final_game = service.load_game(game.id)
    with database.session() as session:
        repo = Repository(session)
        calls = repo.list_llm_calls(game.id)
        events = repo.list_events(game.id)
        commands = repo.list_phase_commands(game.id)
        memories = repo.list_memory_snapshots(game.id)

    status_counts = Counter(call.status for call in calls)
    type_counts = Counter(call.call_type for call in calls)
    error_counts = Counter(call.error_type or "none" for call in calls if call.status == "FAILED")
    latencies = [int(call.latency_ms or 0) for call in calls]
    usage = Counter()
    forbidden_key_found = False
    key_material_found = False
    for call in calls:
        call_payload = {
            "request": request_without_thinking_control(call.request_snapshot),
            "response": call.response_snapshot,
            "final_text": call.final_text,
            "parsed": call.parsed_result,
        }
        forbidden_key_found = forbidden_key_found or has_forbidden_key(call_payload)
        serialized = json.dumps(call_payload, ensure_ascii=False, default=str)
        key_material_found = key_material_found or api_key in serialized
        for key, value in (call.usage or {}).items():
            if isinstance(value, int):
                usage[key] += value

    result = {
        "completed": final_game.phase.value == "FINISHED",
        "stopped_reason": stopped_reason,
        "game_id": game.id,
        "random_seed": game.config.random_seed,
        "final_phase": final_game.phase.value,
        "day": final_game.day,
        "winner": final_game.winner.winner.value if final_game.winner else None,
        "winner_reason": final_game.winner.reason if final_game.winner else None,
        "provider": provider.name,
        "model": settings.minimax_model,
        "endpoint": settings.minimax_api_url,
        "adapter_version": provider.adapter_version,
        "total_elapsed_ms": round((time.perf_counter() - run_started) * 1000),
        "phase_command_attempts": len(phase_runs),
        "phase_command_failures": sum(item["status"] == "FAILED" for item in phase_runs),
        "phase_runs": phase_runs,
        "event_count": len(events),
        "memory_snapshot_count": len(memories),
        "llm_calls": {
            "total": len(calls),
            "status_counts": dict(status_counts),
            "call_type_counts": dict(type_counts),
            "error_type_counts": dict(error_counts),
            "usage": dict(usage),
            "latency_ms": {
                "average": round(statistics.fmean(latencies), 2) if latencies else 0,
                "p95": percentile(latencies, 0.95),
                "max": max(latencies, default=0),
            },
        },
        "isolation_audit": inspect_player_views(calls),
        "thinking_mode_audit": inspect_thinking_mode(calls),
        "sensitive_data_audit": {
            "forbidden_key_found": forbidden_key_found,
            "api_key_material_found": key_material_found,
            "passed": not forbidden_key_found and not key_material_found,
        },
        "database_file": database_path.name,
    }
    print("MINIMAX_RESULT_JSON=" + json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
