"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import {
  ArrowLeft, Bot, BrainCircuit, Braces, ChevronDown, ChevronLeft, ChevronRight,
  Clock3, EyeOff, FileText, FlaskConical, Gauge, HeartPulse, LockKeyhole,
  MessageSquareText, SearchCheck, ShieldAlert, Sparkles, Target,
} from "lucide-react";
import { api } from "@/lib/api";
import {
  alignmentLabels, callDisplayText, callReasoningText, callStatusLabels, callTypeLabels, phaseLabels, roleLabels,
} from "@/lib/display";
import { useUiStore } from "@/store/ui-store";
import { ErrorState, JsonBlock, LoadingState, Pill } from "./ui";
import { RichText } from "./rich-text";

const tabs = [
  ["timeline", "调用时间线", Clock3],
  ["conversation", "可读对话", MessageSquareText],
  ["raw", "原始 JSON", Braces],
  ["memory", "私有记忆", BrainCircuit],
  ["metrics", "用量与错误", Gauge],
] as const;

export function AgentInspector({ gameId, playerId }: { gameId: string; playerId: string }) {
  const godMode = useSearchParams().get("god_mode") === "true";
  const { inspectorTab, setInspectorTab } = useUiStore();
  const game = useQuery({ queryKey: ["games", gameId, godMode], queryFn: () => api.getGame(gameId, godMode) });
  const privateAccess = godMode || game.data?.phase === "FINISHED";
  const calls = useQuery({
    queryKey: ["games", gameId, "players", playerId, "calls", godMode],
    queryFn: () => api.getCalls(gameId, playerId, godMode),
    enabled: privateAccess,
  });
  const memory = useQuery({
    queryKey: ["games", gameId, "players", playerId, "memory", godMode],
    queryFn: () => api.getMemory(gameId, playerId, godMode),
    enabled: inspectorTab === "memory" && privateAccess,
  });

  if (game.isLoading || (privateAccess && calls.isLoading)) return <main className="page"><LoadingState label="正在打开 Agent 审计记录…" /></main>;
  if (game.isError || !game.data) return <main className="page"><ErrorState message={game.error?.message ?? "找不到对局"} /></main>;

  const roster = game.data.players ?? [];
  const player = roster.find((item) => item.id === playerId);
  if (!player) return <main className="page"><ErrorState message="这个 Agent 不在当前对局中。" /></main>;
  const index = roster.findIndex((item) => item.id === playerId);
  const previous = roster.at(index - 1) ?? roster.at(-1);
  const next = roster.at((index + 1) % roster.length);
  const callData = calls.data ?? [];
  const totalTokens = callData.reduce((sum, call) => sum + call.input_tokens + call.output_tokens + call.reasoning_tokens, 0);
  const averageLatency = callData.length ? Math.round(callData.reduce((sum, call) => sum + call.latency_ms, 0) / callData.length) : 0;
  const failed = callData.filter((call) => call.status.toLowerCase().includes("fail")).length;

  return (
    <main className="inspector-page page">
      <div className="inspector-nav">
        <Link href={`/games/${gameId}${godMode ? "?god_mode=true" : ""}`} className="back-link"><ArrowLeft size={17} />返回游戏桌</Link>
        <div className="agent-pagination">
          {previous && <Link aria-label="上一名 Agent" href={`/games/${gameId}/agents/${previous.id}${godMode ? "?god_mode=true" : ""}`}><ChevronLeft /></Link>}
          <span>{index + 1} / {roster.length}</span>
          {next && <Link aria-label="下一名 Agent" href={`/games/${gameId}/agents/${next.id}${godMode ? "?god_mode=true" : ""}`}><ChevronRight /></Link>}
        </div>
      </div>

      <header className="agent-profile panel">
        <div className="profile-avatar"><Bot /></div>
        <div className="profile-copy"><span className="eyebrow">智能体检查器 · {player.seat || index + 1} 号座位</span><h1>{player.name}</h1><p>{player.is_alive ? "当前存活" : "已离场"} · 独立会话 <code>game:{gameId.slice(0, 6)}…:player:{player.id.slice(0, 6)}…</code></p></div>
        <div className="profile-badges"><Pill tone={player.is_alive ? "good" : "neutral"}>{player.is_alive ? "存活" : "已淘汰"}</Pill>{privateAccess && player.role ? <Pill tone="danger">{roleLabels[player.role] ?? player.role}</Pill> : <Pill><EyeOff size={12} />身份隐藏</Pill>}</div>
      </header>

      <div className={`privacy-banner ${godMode ? "danger" : ""}`}>
        {godMode ? <ShieldAlert /> : <LockKeyhole />}
        <div><strong>{godMode ? "当前为私密调试视角" : "安全观战过滤已启用"}</strong><p>{godMode ? "以下内容可能包含角色身份、私有策略与夜间信息，请勿用于未结束对局的公平观战。" : "服务端仅返回允许公开的数据。开启上帝视角前不会请求私有记忆。"}</p></div>
      </div>

      {privateAccess && player.role === "SEER" && <RoleHistory player={player} roster={roster} type="seer" />}
      {privateAccess && player.role === "WITCH" && <RoleHistory player={player} roster={roster} type="witch" />}
      {privateAccess && player.role === "HUNTER" && <RoleHistory player={player} roster={roster} type="hunter" />}

      <nav className="tab-list" aria-label="Agent 检查器视图">
        {tabs.map(([id, label, Icon]) => <button key={id} className={inspectorTab === id ? "active" : ""} onClick={() => setInspectorTab(id)}><Icon size={16} />{label}</button>)}
      </nav>

      <section className="inspector-content">
        {calls.isError && <ErrorState message={calls.error.message} action={<button onClick={() => calls.refetch()}>重试</button>} />}
        {!calls.isError && inspectorTab === "timeline" && <div className="call-timeline">
          {callData.length ? callData.map((call, callIndex) => <article className="call-row" key={call.id}>
            <span className={`call-status ${call.status}`} />
            <div className="call-order">{String(callIndex + 1).padStart(2, "0")}</div>
            <div className="call-main"><span>第 {call.day} 天 · {call.phase in phaseLabels ? phaseLabels[call.phase as keyof typeof phaseLabels] : call.phase}</span><h3>{callTypeLabels[call.call_type] ?? call.call_type.replaceAll("_", " ")}</h3><ReasoningBlock text={callReasoningText(call)} compact /><div className="final-answer"><b>最终回答</b><RichText compact>{callDisplayText(call)}</RichText></div></div>
            <div className="call-stats"><Pill tone={["completed", "succeeded", "success"].includes(call.status) ? "good" : call.status === "failed" ? "danger" : "neutral"}>{callStatusLabels[call.status] ?? call.status}</Pill><span>{call.latency_ms} 毫秒</span><span>{call.input_tokens + call.output_tokens} Token</span></div>
          </article>) : <InspectorEmpty />}
        </div>}

        {!calls.isError && inspectorTab === "conversation" && <div className="conversation-list">
          {callData.length ? callData.map((call) => <article key={call.id} className="conversation-card panel"><header><span><Sparkles size={15} />{callTypeLabels[call.call_type] ?? call.call_type.replaceAll("_", " ")}</span><small>{call.model} · {call.latency_ms} 毫秒</small></header><div className="message request"><b>任务与上下文</b><p>系统已保存本次调用的实际请求快照，并将模型思考与最终回答分开展示。</p></div><ReasoningBlock text={callReasoningText(call)} /><div className="message response"><b>智能体最终回答</b><RichText>{callDisplayText(call)}</RichText></div></article>) : <InspectorEmpty />}
        </div>}

        {!calls.isError && inspectorTab === "raw" && <div className="raw-list">
          {callData.length ? callData.map((call) => <details className="raw-call panel" key={call.id}><summary><span><Braces size={16} />{call.call_type}</span><Pill>{call.status}</Pill></summary><div className="raw-grid"><div><h3>实际请求快照</h3><JsonBlock value={call.request_snapshot} /></div><div><h3>模型响应与解析结果</h3><JsonBlock value={{ reasoning_text: call.reasoning_text, response_text: call.response_text, parsed_result: call.parsed_result, validation_errors: call.validation_errors }} /></div></div></details>) : <InspectorEmpty />}
        </div>}

        {inspectorTab === "memory" && <div className="memory-view">
          {!privateAccess ? <div className="locked-panel panel"><LockKeyhole /><h2>私有记忆已锁定</h2><p>安全观战不会请求或显示 Agent 私有记忆。请返回游戏桌，显式开启上帝视角。</p></div> : memory.isLoading ? <LoadingState label="正在读取记忆快照…" /> : memory.isError ? <ErrorState message={memory.error.message} /> : <><section className="panel current-memory"><span className="eyebrow">CURRENT MEMORY</span><h2>当前私有记忆</h2><JsonBlock value={memory.data?.current} /></section><section><h2>历史快照</h2><div className="snapshot-list">{memory.data?.snapshots.map((snapshot, snapshotIndex) => <details className="panel" key={snapshotIndex}><summary>记忆快照 {snapshotIndex + 1}</summary><JsonBlock value={snapshot} /></details>)}</div></section></>}
        </div>}

        {inspectorTab === "metrics" && <div className="metrics-view">
          <div className="metric-card"><span>总调用数</span><strong>{callData.length}</strong><small>本局该 Agent</small></div>
          <div className="metric-card"><span>累计 Token</span><strong>{totalTokens.toLocaleString()}</strong><small>含 reasoning 数量，不含内容</small></div>
          <div className="metric-card"><span>平均延迟</span><strong>{averageLatency}<em> ms</em></strong><small>端到端调用时间</small></div>
          <div className="metric-card"><span>失败调用</span><strong>{failed}</strong><small>{callData.length ? `${Math.round(failed / callData.length * 100)}% 失败率` : "暂无数据"}</small></div>
          <section className="panel provider-table"><h2>调用明细</h2><div role="table"><div role="row"><span>阶段</span><span>服务商 / 模型</span><span>Token</span><span>延迟</span><span>状态</span></div>{callData.map((call) => <div role="row" key={call.id}><span>{callTypeLabels[call.call_type] ?? call.call_type}</span><span>{call.provider} / {call.model}</span><span>{call.input_tokens + call.output_tokens}</span><span>{call.latency_ms} 毫秒</span><span>{callStatusLabels[call.status] ?? call.status}</span></div>)}</div></section>
        </div>}
      </section>
    </main>
  );
}

function RoleHistory({ player, roster, type }: { player: { role_history: Record<string, unknown>[] }; roster: { id: string; seat: number; name: string }[]; type: "seer" | "witch" | "hunter" }) {
  const nameOf = (id: unknown) => {
    const target = roster.find((item) => item.id === String(id));
    return target ? `${target.seat} 号 ${target.name}` : "未知玩家";
  };

  const renderHunterHistory = (item: Record<string, unknown>) => {
    const eventType = String(item.event_type ?? "").toUpperCase();
    const action = String(item.action ?? "").toLowerCase();
    if (eventType === "HUNTER_ACTION_REQUESTED") {
      const targets = Array.isArray(item.legal_target_ids) ? item.legal_target_ids : [];
      return <p><Target size={17} />获得猎人反应机会，可开枪或放弃{targets.length ? `；合法目标：${targets.map(nameOf).join("、")}` : ""}</p>;
    }
    if (eventType === "HUNTER_ACTION_SUBMITTED") {
      if (action === "shoot") {
        return <p><Target size={17} />提交开枪决定，目标为 <strong>{nameOf(item.target_id)}</strong></p>;
      }
      return <p><Target size={17} />提交放弃开枪决定</p>;
    }
    if (eventType === "HUNTER_SHOT") return <p><Target size={17} />枪杀结果：<strong>{nameOf(item.target_id)}</strong> 出局</p>;
    if (eventType === "HUNTER_PASSED") return <p><Target size={17} />最终选择放弃开枪</p>;
    return <p><Target size={17} />猎人行动已记录</p>;
  };

  return (
    <section className="role-history panel" aria-label={type === "seer" ? "预言家查验记录" : type === "witch" ? "女巫用药记录" : "猎人行动记录"}>
      <header>
        <span className={`role-history-icon ${type}`}>{type === "seer" ? <SearchCheck /> : type === "witch" ? <FlaskConical /> : <Target />}</span>
        <div><span className="eyebrow">角色行动记录</span><h2>{type === "seer" ? "预言家查验历史" : type === "witch" ? "女巫用药历史" : "猎人行动历史"}</h2></div>
        <Pill tone={type === "seer" ? "info" : "danger"}>{player.role_history.length} 条</Pill>
      </header>
      {player.role_history.length ? <div className="role-history-list">{player.role_history.map((item, historyIndex) => (
        <article key={`${String(item.day)}-${historyIndex}`}>
          <span>第 {Number(item.day) || 1} {type === "hunter" ? "天" : "夜"}</span>
          {type === "seer" ? <p><SearchCheck size={17} />查验 <strong>{nameOf(item.target_id)}</strong>，结果为 <b>{alignmentLabels[String(item.alignment)] ?? "未知阵营"}</b></p> : type === "witch" ? item.rescue_target_id ? <p><HeartPulse size={17} />使用解药救下 <strong>{nameOf(item.rescue_target_id)}</strong></p> : item.poison_target_id ? <p><FlaskConical size={17} />使用毒药对付 <strong>{nameOf(item.poison_target_id)}</strong></p> : <p><FlaskConical size={17} />本夜没有使用药剂</p> : renderHunterHistory(item)}
        </article>
      ))}</div> : <p className="role-history-empty">当前还没有角色行动记录。</p>}
    </section>
  );
}

function ReasoningBlock({ text, compact = false }: { text: string | null; compact?: boolean }) {
  if (!text) return null;
  return <details className="thinking-details" open>
    <summary>
      <span className="thinking-title"><BrainCircuit size={15} />思考</span>
      <span className="thinking-toggle"><span className="when-closed">展开</span><span className="when-open">收起</span><ChevronDown size={15} /></span>
    </summary>
    <RichText compact={compact}>{text}</RichText>
  </details>;
}

function InspectorEmpty() {
  return <div className="inspector-empty"><FileText /><h2>还没有模型调用</h2><p>推进对局后，这名 Agent 的实际请求、最终回答与用量会出现在这里。</p></div>;
}
