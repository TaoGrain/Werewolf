from __future__ import annotations

from uuid import uuid4

from app.agents.providers import LLMMessage as ProviderMessage, MessageRole
from app.persistence import Database, Repository


class DatabaseCallRecorder:
    """将 AgentGateway 的脱敏快照落入审计表。"""

    def __init__(self, database: Database) -> None:
        self.database = database

    def record_llm_call(self, snapshot: dict) -> None:
        with self.database.session() as session:
            repo = Repository(session)
            game_id = snapshot["game_id"]
            player_id = snapshot["player_id"]
            agent_session = repo.get_or_create_agent_session(game_id, player_id)
            request_id = str(snapshot.get("request_id") or uuid4())
            # 厂商 request_id 可能在 FakeProvider 重建后重复；应用审计 ID 始终唯一。
            audit_request_id = f"{game_id}:{player_id}:{uuid4()}"
            call = repo.start_llm_call(
                request_id=audit_request_id,
                provider=snapshot["provider"],
                model=snapshot["model"],
                adapter_version=snapshot["adapter_version"],
                game_id=game_id,
                player_id=player_id,
                agent_session_id=agent_session.id,
                day=int(snapshot["day"]),
                phase=snapshot["phase"],
                call_type=snapshot["call_type"],
                request_snapshot=snapshot["request_snapshot"],
                prompt_version=str(snapshot.get("prompt_version") or "unknown"),
                rules_version=str(snapshot.get("ruleset_version") or "mvp-v1"),
                schema_version="mvp-v1",
            )
            history_messages = snapshot.get("history_messages")
            if history_messages is None:
                history_messages = [
                    *snapshot["request_snapshot"].get("messages", []),
                    {"role": "assistant", "content": snapshot.get("final_text", "")},
                ]
            for ordinal, message in enumerate(history_messages):
                repo.add_llm_message(
                    call.id,
                    ordinal=ordinal,
                    role=str(message.get("role", "user")),
                    content=str(message.get("content", "")),
                )
            usage = snapshot.get("usage") or {}
            repo.complete_llm_call(
                call,
                provider_request_id=request_id,
                final_text=snapshot.get("final_text", ""),
                parsed_result=snapshot.get("parsed_result"),
                response_snapshot=snapshot.get("response_snapshot"),
                validation_errors=[snapshot["validation_error"]] if snapshot.get("validation_error") else [],
                usage=usage,
                reasoning_tokens=int(usage.get("reasoning_tokens") or 0),
                latency_ms=round(float(snapshot.get("latency_ms") or 0)),
            )
            parsed = snapshot.get("parsed_result") or {}
            repo.add_memory_snapshot(
                game_id,
                player_id,
                agent_session.id,
                snapshot={
                    "private_strategy": parsed.get("private_strategy") or parsed.get("private_reason"),
                    "private_strategy_status": "SUBJECTIVE_AGENT_INFERENCE",
                    "last_call_type": snapshot.get("call_type"),
                    "attributed_public_statements": snapshot.get("attributed_public_statements") or [],
                    "public_vote_history": snapshot.get("public_vote_history") or [],
                },
                phase=snapshot["phase"],
                day=int(snapshot["day"]),
                llm_call_id=call.id,
            )

    def record_llm_failure(self, snapshot: dict) -> None:
        with self.database.session() as session:
            repo = Repository(session)
            game_id = snapshot["game_id"]
            player_id = snapshot["player_id"]
            agent_session = repo.get_or_create_agent_session(game_id, player_id)
            call = repo.start_llm_call(
                request_id=f"{game_id}:{player_id}:{uuid4()}",
                provider=snapshot["provider"],
                model=snapshot["model"],
                adapter_version=snapshot["adapter_version"],
                game_id=game_id,
                player_id=player_id,
                agent_session_id=agent_session.id,
                day=int(snapshot["day"]),
                phase=snapshot["phase"],
                call_type=snapshot["call_type"],
                request_snapshot=snapshot["request_snapshot"],
                prompt_version=str(snapshot.get("prompt_version") or "unknown"),
                rules_version=str(snapshot.get("ruleset_version") or "mvp-v1"),
                schema_version="mvp-v1",
            )
            for ordinal, message in enumerate(snapshot["request_snapshot"].get("messages", [])):
                repo.add_llm_message(
                    call.id,
                    ordinal=ordinal,
                    role=str(message.get("role", "user")),
                    content=str(message.get("content", "")),
                )
            if snapshot.get("final_text") is not None:
                repo.add_llm_message(
                    call.id,
                    ordinal=len(snapshot["request_snapshot"].get("messages", [])),
                    role="assistant",
                    content=str(snapshot["final_text"]),
                )
            repo.fail_llm_call(
                call,
                error_type=str(snapshot.get("error_type") or "provider_error"),
                error_message=str(snapshot.get("error_message") or "模型调用失败"),
                validation_errors=[snapshot["validation_error"]] if snapshot.get("validation_error") else [],
                latency_ms=round(float(snapshot.get("latency_ms") or 0)),
                response_snapshot=snapshot.get("response_snapshot"),
            )

    def load_session_history(self, session_id: str) -> list[ProviderMessage]:
        prefix = "game:"
        marker = ":player:"
        if not session_id.startswith(prefix) or marker not in session_id:
            return []
        game_id, player_id = session_id[len(prefix) :].split(marker, 1)
        with self.database.session() as session:
            repo = Repository(session)
            calls = repo.list_llm_calls(game_id, player_id=player_id)
            successful_call = next(
                (call for call in reversed(calls) if call.status == "SUCCEEDED"),
                None,
            )
            if successful_call is None:
                return []
            messages = repo.list_llm_messages(successful_call.id)
            recovered: list[ProviderMessage] = []
            for message in messages:
                if message.role == "system":
                    continue
                try:
                    role = MessageRole(message.role)
                except ValueError:
                    continue
                recovered.append(ProviderMessage(role=role, content=message.content))
            return recovered
