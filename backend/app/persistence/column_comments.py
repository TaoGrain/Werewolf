"""数据库字段的中文注释定义。"""

from __future__ import annotations


COMMON_COLUMN_COMMENTS = {
    "game_id": "所属游戏ID",
    "player_id": "关联玩家ID",
    "agent_session_id": "关联智能体会话ID",
    "llm_call_id": "关联大模型调用ID",
    "day": "游戏天数",
    "phase": "游戏阶段",
    "status": "当前状态",
    "role": "角色标识",
    "sequence": "顺序编号",
    "name": "名称",
    "payload": "业务数据载荷",
    "validation_errors": "校验错误列表",
    "is_valid": "是否有效",
    "started_at": "开始时间（UTC）",
    "finished_at": "结束时间（UTC）",
    "created_at": "创建时间（UTC）",
    "updated_at": "更新时间（UTC）",
}


TABLE_COLUMN_COMMENTS = {
    "games": {
        "status": "游戏状态",
        "phase": "当前游戏阶段",
        "day": "当前游戏天数",
        "rules_version": "游戏规则版本",
        "random_seed": "随机数种子",
        "config": "游戏配置",
        "state_snapshot": "游戏状态快照",
        "version": "乐观锁版本号",
        "next_event_sequence": "下一个游戏事件序号",
    },
    "players": {
        "seat": "玩家座位号",
        "name": "玩家名称",
        "alive": "是否存活",
        "personality": "玩家性格配置",
    },
    "role_assignments": {
        "role": "分配的游戏角色",
        "private_state": "角色私有状态",
    },
    "agent_sessions": {
        "session_key": "智能体会话唯一键",
        "state": "智能体会话状态",
        "last_message_at": "最后消息时间（UTC）",
    },
    "llm_calls": {
        "request_id": "内部请求唯一标识",
        "provider_request_id": "大模型服务商请求标识",
        "player_id": "发起调用的玩家ID",
        "agent_session_id": "发起调用的智能体会话ID",
        "provider": "大模型服务商",
        "model": "大模型名称",
        "adapter_version": "模型适配器版本",
        "call_type": "模型调用类型",
        "attempt": "调用尝试次数",
        "request_snapshot": "模型请求快照",
        "response_snapshot": "模型响应快照",
        "final_text": "模型最终文本响应",
        "parsed_result": "解析后的结构化结果",
        "prompt_version": "提示词版本",
        "rules_version": "游戏规则版本",
        "schema_version": "响应结构版本",
        "usage": "Token用量信息",
        "reasoning_tokens": "推理Token数量",
        "latency_ms": "调用耗时（毫秒）",
        "status": "模型调用状态",
        "error_type": "调用错误类型",
        "error_message": "调用错误信息",
        "safety_flagged": "是否触发安全标记",
    },
    "llm_messages": {
        "ordinal": "消息顺序号",
        "role": "消息角色",
        "content": "消息内容",
        "name": "消息名称",
        "message_metadata": "消息元数据",
    },
    "game_events": {
        "sequence": "游戏内事件序号",
        "event_type": "游戏事件类型",
        "rules_version": "记录所属游戏的实际规则版本",
        "visibility": "事件可见范围",
        "actor_player_id": "事件发起玩家ID",
        "payload": "游戏事件数据",
    },
    "phase_commands": {
        "command_id": "客户端命令唯一标识",
        "command_type": "阶段命令类型",
        "status": "阶段命令状态",
        "request_payload": "命令请求数据",
        "result_payload": "命令处理结果数据",
        "error": "命令错误详情",
    },
    "phase_actions": {
        "phase_command_id": "来源阶段命令ID",
        "player_id": "执行行动的玩家ID",
        "action_type": "阶段行动类型",
        "rules_version": "记录所属游戏的实际规则版本",
        "payload": "阶段行动数据",
    },
    "votes": {
        "phase_action_id": "关联阶段行动ID",
        "voter_player_id": "投票玩家ID",
        "target_player_id": "被投票玩家ID，弃票时为空",
        "abstained": "是否弃票",
        "round": "投票轮次",
        "public_reason": "公开投票理由",
        "confidence": "投票置信度（0到1）",
    },
    "agent_memory_snapshots": {
        "sequence": "会话内记忆快照序号",
        "snapshot": "智能体记忆状态快照",
    },
    "alembic_version": {
        "version_num": "数据库结构迁移版本号",
    },
}


def get_column_comment(table_name: str, column_name: str) -> str:
    """返回字段注释；未定义时抛错，避免无注释字段静默进入数据库。"""

    table_comments = TABLE_COLUMN_COMMENTS.get(table_name, {})
    if column_name in table_comments:
        return table_comments[column_name]
    if column_name in COMMON_COLUMN_COMMENTS:
        return COMMON_COLUMN_COMMENTS[column_name]
    raise KeyError(f"字段缺少注释定义：{table_name}.{column_name}")
