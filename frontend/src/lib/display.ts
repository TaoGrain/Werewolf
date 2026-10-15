import type { GameEvent, GamePhase, KnownGamePhase, LlmCall, Player } from "./schemas";

export const phaseLabels: Record<KnownGamePhase, string> = {
  LOBBY: "等待开局",
  ROLE_ASSIGNED: "身份已分配",
  NIGHT_SEER: "预言家查验",
  NIGHT_WOLF_DISCUSSION: "狼人交流",
  NIGHT_WOLF: "狼人出刀",
  NIGHT_WITCH: "女巫行动",
  NIGHT_RESOLUTION: "夜间结算",
  DAY_ANNOUNCEMENT: "天亮公布",
  DAY_DISCUSSION: "白天发言",
  DAY_RUNOFF_DISCUSSION: "平票 PK 发言",
  DAY_VOTING: "放逐投票",
  DAY_RESOLUTION: "投票结算",
  HUNTER_REACTION: "猎人反应",
  FINISHED: "对局结束",
};
export function phaseLabel(phase: GamePhase) {
  return Object.prototype.hasOwnProperty.call(phaseLabels, phase) ? phaseLabels[phase as KnownGamePhase] : `未知阶段（${phase}）`;
}


export const roleLabels: Record<string, string> = {
  WEREWOLF: "狼人",
  WOLF: "狼人",
  SEER: "预言家",
  WITCH: "女巫",
  HUNTER: "猎人",
  VILLAGER: "平民",
};

export const visibilityLabels: Record<string, string> = {
  PUBLIC: "公开",
  PLAYER_PRIVATE: "玩家私密",
  WOLF_TEAM_PRIVATE: "狼人阵营私密",
  HOST_PRIVATE: "主持人私密",
  POST_GAME_PUBLIC: "赛后公开",
};

export const callTypeLabels: Record<string, string> = {
  speech: "公开发言",
  vote: "放逐投票",
  wolf_discussion: "狼人交流",
  wolf_kill: "狼人击杀",
  hunter_action: "猎人行动",
  seer_check: "预言家查验",
  witch_action: "女巫用药",
  last_words: "遗言",
  repair: "格式修正",
};

export const callStatusLabels: Record<string, string> = {
  completed: "已完成",
  succeeded: "成功",
  success: "成功",
  failed: "失败",
  running: "进行中",
  unknown: "未知",
};

export const alignmentLabels: Record<string, string> = { WOLF: "狼人阵营", GOOD: "好人阵营" };
export const deathCauseLabels: Record<string, string> = { WOLF_ATTACK: "狼人袭击", WITCH_POISON: "女巫毒杀", EXILE: "投票放逐", HUNTER_SHOT: "猎人枪杀" };

export function formatDate(value?: string | null) {
  if (!value) return "刚刚";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit",
  }).format(new Date(value));
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function playerName(id: unknown, players: Player[]) {
  if (!id) return "未知玩家";
  const player = players.find((item) => item.id === String(id));
  return player ? `${player.seat} 号 ${player.name}` : String(id);
}

export function eventText(event: GameEvent, players: Player[] = []) {
  const payload = event.payload;
  const type = event.type.toUpperCase();

  if (type === "VOTES_REVEALED") {
    const votes = Array.isArray(payload.votes) ? payload.votes.map(asRecord) : [];
    const counts = asRecord(payload.counts);
    const voteText = votes.map((vote) => {
      const choice = vote.abstain === true ? "弃票" : playerName(vote.target_id, players);
      return `${playerName(vote.actor_id, players)} → ${choice}`;
    }).join("；");
    const abstainCount = Number(payload.abstain_count) || 0;
    const countText = Object.entries(counts)
      .sort(([, left], [, right]) => Number(right) - Number(left))
      .map(([targetId, count]) => `${playerName(targetId, players)} ${Number(count)} 票`)
      .join("，");
    const summary = [countText, abstainCount ? `弃票 ${abstainCount} 票` : ""].filter(Boolean).join("，");
    return `第 ${Number(payload.round) || 1} 轮投票：${voteText || "暂无投票"}${summary ? `。票数统计：${summary}` : ""}`;
  }

  if (type === "HUNTER_ACTION_REQUESTED") {
    const hunter = playerName(payload.hunter_id ?? event.player_id, players);
    const targets = Array.isArray(payload.legal_target_ids) ? payload.legal_target_ids : [];
    return `${hunter}进入猎人反应，可选择开枪或放弃${targets.length ? `。合法目标：${targets.map((id) => playerName(id, players)).join("、")}` : ""}。`;
  }
  if (type === "HUNTER_ACTION_SUBMITTED") {
    const action = String(payload.action ?? payload.hunter_action ?? "pass").toLowerCase();
    if (action === "shoot") {
      return `猎人已提交开枪，目标为 ${playerName(payload.target_id, players)}${typeof payload.public_statement === "string" ? `。公开声明：${payload.public_statement}` : "。"}`;
    }
    return "猎人已提交放弃开枪。";
  }
  if (type === "HUNTER_SHOT") {
    return `${playerName(payload.hunter_id ?? payload.shooter_id ?? event.player_id, players)}开枪带走了 ${playerName(payload.target_id, players)}。`;
  }
  if (type === "HUNTER_PASSED") {
    return `${playerName(payload.hunter_id ?? event.player_id, players)}选择放弃开枪。`;
  }
  if (type === "NIGHT_ANNOUNCED") {
    const deaths = Array.isArray(payload.death_ids) ? payload.death_ids : [];
    return deaths.length ? `昨夜死亡：${deaths.map((id) => playerName(id, players)).join("、")}` : "昨夜是平安夜，无人死亡。";
  }
  if (type === "WOLF_DISCUSSION_MESSAGE") {
    return `${typeof payload.content === "string" ? payload.content : "提出了狼队计划。"}${payload.proposed_target_id ? ` 建议目标：${playerName(payload.proposed_target_id, players)}。` : ""}`;
  }
  if (type === "WOLF_ACTION_SUBMITTED") {
    return `选择 ${playerName(payload.target_id, players)} 作为今夜袭击目标${typeof payload.reason === "string" ? `。理由：${payload.reason}` : "。"}`;
  }
  if (type === "WOLF_TARGET_SELECTED") {
    return `狼人最终出刀目标：${playerName(payload.target_id, players)}。`;
  }
  if (type === "PLAYER_DIED") {
    const causes = Array.isArray(payload.causes) ? payload.causes : Array.isArray(payload.death_causes) ? payload.death_causes : [payload.cause];
    const causeText = causes.filter(Boolean).map((cause) => deathCauseLabels[String(cause)] ?? String(cause)).join("、");
    return `${playerName(payload.player_id, players)}死亡，原因：${causeText || "未知"}。`;
  }
  if (type === "RUNOFF_STARTED") {
    const candidates = Array.isArray(payload.candidate_ids) ? payload.candidate_ids : [];
    return `首轮出现平票，进入第 ${Number(payload.round) || 2} 轮投票。候选人：${candidates.map((id) => playerName(id, players)).join("、") || "未知"}。`;
  }
  if (type === "SEER_INSPECTION") {
    return `查验了 ${playerName(payload.target_id, players)}，结果为${alignmentLabels[String(payload.alignment)] ?? String(payload.alignment ?? "未知")}。`;
  }
  if (type === "WITCH_ACTION") {
    if (payload.rescue_target_id) return `使用解药救下 ${playerName(payload.rescue_target_id, players)}。`;
    if (payload.poison_target_id) return `使用毒药对付 ${playerName(payload.poison_target_id, players)}。`;
    return "本夜没有使用药剂。";
  }
  if (type === "PHASE_CHANGED") {
    const next = String(payload.to_phase ?? payload.to ?? payload.phase ?? "");
    return Object.prototype.hasOwnProperty.call(phaseLabels, next) ? `进入${phaseLabels[next as KnownGamePhase]}阶段。` : `进入未知阶段（${next || "未提供"}）。`;
  }
  if (type === "ROLES_ASSIGNED") {
    if (payload.role) {
      const teammates = Array.isArray(payload.wolf_teammate_ids) ? payload.wolf_teammate_ids : [];
      const teammateText = teammates.length ? `；狼人队友：${teammates.map((id) => playerName(id, players)).join("、")}` : "";
      return `${playerName(payload.player_id, players)} 的身份是 ${roleLabels[String(payload.role)] ?? String(payload.role)}${teammateText}。`;
    }
    return "所有玩家的身份已分配完毕。";
  }
  if (type === "GAME_CREATED") return "对局已创建，等待主持人开始游戏。";
  if (type === "NO_EXILE") return typeof payload.reason === "string" ? payload.reason : "本轮无人被放逐。";
  if (type === "GAME_FINISHED") return typeof payload.reason === "string" ? payload.reason : "对局胜负已定。";
  if (event.content) return event.content;
  if (event.message) return event.message;
  if (typeof payload.speech === "string") return payload.speech;
  if (typeof payload.reason === "string") return payload.reason;
  return "事件已记录";
}

export function eventTitle(event: GameEvent) {
  if (event.title) return event.title;
  const labels: Record<string, string> = {
    GAME_CREATED: "对局创建",
    GAME_STARTED: "身份分配完成",
    ROLES_ASSIGNED: "身份分配完成",
    PHASE_STARTED: "阶段开始",
    PHASE_COMPLETED: "阶段完成",
    PHASE_CHANGED: "阶段推进",
    WOLF_DISCUSSION_MESSAGE: "狼人队内交流",
    WOLF_ACTION_SUBMITTED: "狼人已提交行动",
    WOLF_TARGET_SELECTED: "狼人已选定目标",
    SEER_INSPECTION: "预言家查验",
    HUNTER_ACTION_REQUESTED: "等待猎人行动",
    HUNTER_ACTION_SUBMITTED: "猎人已作出选择",
    HUNTER_SHOT: "猎人开枪",
    HUNTER_PASSED: "猎人放弃开枪",
    WITCH_ACTION: "女巫行动",
    NIGHT_RESOLVED: "夜间结算完成",
    NIGHT_ANNOUNCED: "夜晚死亡公布",
    SPEECH: "公开发言",
    LAST_WORDS: "玩家遗言",
    VOTE: "投票公开",
    VOTE_SUBMITTED: "投票已提交",
    VOTES_REVEALED: "投票结果",
    RUNOFF_STARTED: "平票加赛",
    PLAYER_DIED: "玩家死亡",
    NO_EXILE: "无人放逐",
    GAME_FINISHED: "胜负已定",
    AGENT_CALL_STARTED: "Agent 正在思考",
    AGENT_CALL_COMPLETED: "Agent 决策完成",
    AGENT_CALL_FAILED: "Agent 调用失败",
  };
  return labels[event.type.toUpperCase()] ?? event.type.replaceAll("_", " ");
}

function structuredCallMarkdown(value: unknown): string | null {
  const result = asRecord(value);
  if (!Object.keys(result).length) return null;
  const lines: string[] = [];
  if (typeof result.speech === "string") lines.push(result.speech);
  if (typeof result.message === "string") lines.push(`**狼队发言：** ${result.message}`);
  if (typeof result.action === "string") {
    const action = { save: "使用解药", poison: "使用毒药", shoot: "开枪", pass: "放弃行动", vote: "投票", abstain: "弃票" }[result.action] ?? result.action;
    lines.push(`**行动：** ${action}`);
  }
  if (typeof result.target_id === "string") lines.push(`**目标编号：** ${result.target_id}`);
  if (typeof result.proposed_target_id === "string") lines.push(`**建议目标编号：** ${result.proposed_target_id}`);
  if (typeof result.public_reason === "string") lines.push(`**公开理由：** ${result.public_reason}`);
  if (typeof result.private_reason === "string" && result.private_reason) lines.push(`**私密理由：** ${result.private_reason}`);
  if (typeof result.private_strategy === "string" && result.private_strategy) lines.push(`**私有策略：** ${result.private_strategy}`);
  if (typeof result.confidence === "number") lines.push(`**信心值：** ${Math.round(result.confidence * 100)}%`);
  return lines.length ? lines.join("\n\n") : null;
}

export function callDisplayText(call: Pick<LlmCall, "parsed_result" | "response_text">) {
  const parsed = structuredCallMarkdown(call.parsed_result);
  if (parsed) return parsed;
  const cleaned = (call.response_text ?? "")
    .replace(/<(?:think|analysis)>[\s\S]*?<\/(?:think|analysis)>/gi, "")
    .trim();
  const start = cleaned.indexOf("{");
  const end = cleaned.lastIndexOf("}");
  if (start >= 0 && end > start) {
    try {
      const fallback = structuredCallMarkdown(JSON.parse(cleaned.slice(start, end + 1)));
      if (fallback) return fallback;
    } catch {
      // 不是结构化 JSON 时，继续按富文本展示清理后的原文。
    }
  }
  return cleaned || "此调用没有可展示的最终文本。";
}

export function callReasoningText(call: Pick<LlmCall, "reasoning_text" | "response_text">) {
  if (call.reasoning_text?.trim()) return call.reasoning_text.trim();
  const matches = [...(call.response_text ?? "").matchAll(/<(?:think|analysis)>([\s\S]*?)<\/(?:think|analysis)>/gi)];
  const text = matches.map((match) => match[1].trim()).filter(Boolean).join("\n\n");
  return text || null;
}
