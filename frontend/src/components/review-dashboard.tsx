"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  ArrowLeft, ArrowUpRight, BarChart3, Bot, CheckCircle2, Clock3, Eye,
  Filter, Flag, Gauge, Moon, RefreshCw, ShieldCheck, Sparkles, Sun, Target, Trophy,
} from "lucide-react";
import { api } from "@/lib/api";
import { eventText, eventTitle, phaseLabel, roleLabels, visibilityLabels } from "@/lib/display";
import { ErrorState, LoadingState, Pill } from "./ui";
import { RichText } from "./rich-text";

const filterOptions = [
  ["all", "全部事件"], ["night", "夜间"], ["day", "白天"], ["vote", "投票"], ["agent", "Agent 调用"],
] as const;

const winnerLabels: Record<string, string> = {
  GOOD: "好人阵营",
  WOLVES: "狼人阵营",
};

const winReasonLabels: Record<string, string> = {
  ALL_WOLVES_ELIMINATED: "灭狼",
  ALL_GODS_ELIMINATED: "屠神",
  ALL_VILLAGERS_ELIMINATED: "屠民",
};

const reviewModels = [
  ["minimaxM3", "MiniMax M3"],
  ["deepseekv4pro", "DeepSeek V4 Pro"],
  ["deepseekv4flash", "DeepSeek V4 Flash"],
] as const;
type ReviewModel = (typeof reviewModels)[number][0];

const dimensionLabels = {
  role_performance: "身份发挥",
  reasoning: "逻辑判断",
  communication: "沟通协作",
  adaptability: "应变调整",
} as const;

export function ReviewDashboard({ gameId }: { gameId: string }) {
  const [filter, setFilter] = useState("all");
  const [playerId, setPlayerId] = useState("");
  const [selectedReviewModel, setSelectedReviewModel] = useState<ReviewModel | null>(null);
  const review = useQuery({ queryKey: ["games", gameId, "review", playerId], queryFn: () => api.getReview(gameId, playerId || undefined) });
  const effectiveReviewModel = selectedReviewModel ?? review.data?.game?.model ?? "minimaxM3";
  const aiReviewMutation = useMutation({
    mutationFn: () => api.generateAIReview(gameId, effectiveReviewModel),
    onSuccess: () => review.refetch(),
  });

  const events = useMemo(() => (review.data?.events ?? []).filter((event) => {
    if (filter === "all") return true;
    if (filter === "night") return event.phase.startsWith("NIGHT");
    if (filter === "day") return event.phase.startsWith("DAY") && !event.type.toUpperCase().includes("VOTE");
    if (filter === "vote") return event.type.toUpperCase().includes("VOTE");
    return event.type.toUpperCase().includes("AGENT") || event.type.toUpperCase().includes("LLM");
  }), [review.data?.events, filter]);

  if (review.isLoading) return <main className="page"><LoadingState label="正在还原完整事实记录…" /></main>;
  if (review.isError || !review.data) return <main className="page"><ErrorState message={review.error?.message ?? "复盘记录不存在"} action={<button onClick={() => review.refetch()}>重试</button>} /></main>;

  const data = review.data;
  const game = data.game;
  const players = data.players.length ? data.players : game?.players ?? [];
  const calls = data.calls;
  const totalTokens = data.metrics.total_tokens
    ?? calls.reduce((sum, call) => sum + call.input_tokens + call.output_tokens, 0);
  const avgLatency = calls.length ? Math.round(calls.reduce((sum, call) => sum + call.latency_ms, 0) / calls.length) : 0;
  const votes = data.metrics.vote_count;
  const invalid = data.events.filter((event) => /INVALID|FAILED/.test(event.type.toUpperCase())).length;
  const aiReview = aiReviewMutation.data ?? data.ai_review;

  const winResult = game?.win_result;
  const winReason = winResult?.reason_code ? winReasonLabels[winResult.reason_code] ?? winResult.reason_code : winResult?.reason;
  const hasWinCounts = [winResult?.alive_wolves, winResult?.alive_good, winResult?.alive_gods, winResult?.alive_villagers].some((count) => count != null);
  return (
    <main className="review-page">
      <div className="review-hero">
        <div className="page review-hero-inner">
          <Link className="back-link light" href={`/games/${gameId}`}><ArrowLeft size={17} />返回游戏桌</Link>
          <Pill tone="gold"><Flag size={13} /> FACT-BASED REVIEW</Pill>
          <h1>迷雾散去，<em>每一步都有迹可循。</em></h1>
          <p>{game?.name || `对局 ${gameId.slice(0, 8)}`} · 第 {game?.day ?? 0} 天结束 · 基于已保存事件与调用快照生成，未重新调用模型改写历史。</p>
          <div className="winner-card"><span><Sparkles /></span><div><small>获胜阵营{winReason ? ` · ${winReason}` : ""}</small><strong>{game?.winner ? winnerLabels[game.winner.toUpperCase()] ?? "未知阵营" : "对局结果待确认"}</strong></div><CheckCircle2 /></div>
        </div>
      </div>

      <div className="page review-body">
        <section className="metric-grid">
          <div className="review-metric"><Target /><span>投票记录</span><strong>{votes}</strong><small>全部公开票型</small></div>
          <div className="review-metric"><Bot /><span>模型调用</span><strong>{calls.length}</strong><small>{players.length} 名独立 Agent</small></div>
          <div className="review-metric"><Gauge /><span>累计 Token</span><strong>{totalTokens.toLocaleString()}</strong><small>审计用量</small></div>
          <div className="review-metric"><Clock3 /><span>平均延迟</span><strong>{avgLatency}<em>ms</em></strong><small>每次模型调用</small></div>
          <div className="review-metric"><ShieldCheck /><span>无效行动</span><strong>{invalid}</strong><small>规则引擎拦截</small></div>
        </section>

        {hasWinCounts && <section className="survival-summary panel" aria-label="终局分类存活数">
          <strong>终局分类存活数</strong>
          {winResult?.alive_wolves != null && <span><b>{winResult.alive_wolves}</b> 狼人</span>}
          {winResult?.alive_gods != null && <span><b>{winResult.alive_gods}</b> 神职</span>}
          {winResult?.alive_villagers != null && <span><b>{winResult.alive_villagers}</b> 平民</span>}
          {winResult?.alive_good != null && <span><b>{winResult.alive_good}</b> 好人合计</span>}
        </section>}

        <section className="ai-review panel">
          <header className="ai-review-header">
            <div>
              <span className="eyebrow">AI COACH REVIEW</span>
              <h2>Agent 表现评价</h2>
              <p>默认不调用模型。选择模型并主动生成后，才会分析本局事实并为每名 Agent 评分。</p>
            </div>
            <div className="ai-review-actions">
              <label>
                <span>复盘模型</span>
                <select
                  value={effectiveReviewModel}
                  onChange={(event) => setSelectedReviewModel(event.target.value as ReviewModel)}
                  disabled={aiReviewMutation.isPending}
                >
                  {reviewModels.map(([value, label]) => (
                    <option value={value} key={value}>{label}{game?.model === value ? " · 对局模型" : ""}</option>
                  ))}
                </select>
              </label>
              <button
                className="button primary"
                disabled={aiReviewMutation.isPending}
                onClick={() => aiReviewMutation.mutate()}
              >
                {aiReviewMutation.isPending ? <RefreshCw className="spin" size={16} /> : <Sparkles size={16} />}
                {aiReviewMutation.isPending ? "正在分析…" : aiReview ? "重新生成" : "开始 AI 复盘"}
              </button>
            </div>
          </header>

          {aiReviewMutation.isError && <div className="ai-review-error">{aiReviewMutation.error.message}</div>}

          {!aiReview && !aiReviewMutation.isPending && <div className="ai-review-empty">
            <Bot />
            <div><strong>尚未生成 AI 评价</strong><p>事实时间线可直接查看；只有点击上方按钮才会产生额外模型调用。</p></div>
          </div>}

          {aiReview && <div className="ai-review-result">
            <div className="ai-review-summary">
              <Trophy />
              <div><strong>整局总评</strong><p>{aiReview.overall_summary}</p><small>由 {aiReview.model} 生成 · {new Date(aiReview.generated_at).toLocaleString("zh-CN")}</small></div>
            </div>
            <div className="agent-evaluation-grid">
              {aiReview.evaluations.map((evaluation) => {
                const player = players.find((item) => item.id === evaluation.player_id);
                return <article className="agent-evaluation" key={evaluation.player_id}>
                  <header>
                    <div><span>{player?.seat ?? "?"} 号</span><h3>{player?.name ?? evaluation.player_id}</h3><small>{player?.role ? roleLabels[player.role] ?? player.role : "身份未知"}</small></div>
                    <strong>{evaluation.overall_score}<em>/100</em></strong>
                  </header>
                  <p>{evaluation.summary}</p>
                  <div className="evaluation-dimensions">
                    {Object.entries(evaluation.dimensions).map(([key, score]) => <span key={key}><small>{dimensionLabels[key as keyof typeof dimensionLabels] ?? key}</small><b>{score}/10</b></span>)}
                  </div>
                  <div className="evaluation-notes">
                    <div><strong>亮点</strong><ul>{evaluation.strengths.map((item) => <li key={item}>{item}</li>)}</ul></div>
                    <div><strong>改进</strong><ul>{evaluation.improvements.map((item) => <li key={item}>{item}</li>)}</ul></div>
                  </div>
                  {evaluation.decisive_moment && <blockquote><strong>关键时刻</strong>{evaluation.decisive_moment}</blockquote>}
                </article>;
              })}
            </div>
          </div>}
        </section>

        <section className="review-layout">
          <div className="review-timeline panel">
            <div className="section-heading review-title"><div><span className="eyebrow">FULL TIMELINE</span><h2>事实时间线</h2></div><Pill>{events.length} 项</Pill></div>
            <div className="review-filters">
              <Filter size={16} />
              {filterOptions.map(([value, label]) => <button className={filter === value ? "active" : ""} onClick={() => setFilter(value)} key={value}>{label}</button>)}
            </div>
            <div className="review-event-list">
              {events.map((event, index) => {
                const night = event.phase.startsWith("NIGHT");
                return <article className="review-event" key={event.id}>
                  <div className={`event-icon ${night ? "night" : "day"}`}>{night ? <Moon /> : <Sun />}</div>
                  <div className="event-sequence"><span>{String(index + 1).padStart(2, "0")}</span><i /></div>
                  <div className="event-body"><header><span>第 {event.day || 0} 天 · {phaseLabel(event.phase)}</span>{event.visibility !== "PUBLIC" && <Pill tone="danger">{visibilityLabels[event.visibility] ?? event.visibility}</Pill>}</header><h3>{event.player_name && `${event.player_name} · `}{eventTitle(event)}</h3><RichText compact>{eventText(event, players)}</RichText>{event.player_id && <Link href={`/games/${gameId}/agents/${event.player_id}?god_mode=true`}>查看关联智能体 <ArrowUpRight size={14} /></Link>}</div>
                </article>;
              })}
              {!events.length && <div className="inspector-empty"><Filter /><h2>没有匹配事件</h2><p>切换筛选条件以查看其他事实记录。</p></div>}
            </div>
          </div>

          <aside className="review-sidebar">
            <section className="panel perspective-card">
              <span className="eyebrow">PERSPECTIVE</span><h2>单 Agent 视角</h2><p>选择一名 Agent，还原它在每个时刻真正知道的信息。</p>
              <label><span>观察视角</span><select value={playerId} onChange={(event) => setPlayerId(event.target.value)}><option value="">上帝视角 · 全部事实</option>{players.map((player) => <option value={player.id} key={player.id}>{player.seat} 号 · {player.name}</option>)}</select></label>
              {playerId && <Link className="button subtle full" href={`/games/${gameId}/agents/${playerId}?god_mode=true`}><Eye size={16} />打开完整检查器</Link>}
            </section>

            <section className="panel identity-board">
              <div className="section-heading"><div><span className="eyebrow">IDENTITIES</span><h2>最终身份</h2></div></div>
              <div>{players.map((player) => <div key={player.id}><span className={player.is_alive ? "identity-avatar" : "identity-avatar dead"}>{player.name.slice(0, 1)}</span><span><strong>{player.name}</strong><small>{player.is_alive ? "存活" : "离场"}</small></span><Pill tone={player.role?.includes("WOLF") ? "danger" : "neutral"}>{player.role ? roleLabels[player.role] ?? player.role : "未知"}</Pill></div>)}</div>
            </section>

            <section className="panel truth-note"><BarChart3 /><div><h3>数据源说明</h3><p>事实时间线只读取追加式事件与调用快照；AI 评价会明确标注生成模型，并与原始事实分开显示。隐藏推理文本不会展示。</p></div></section>
          </aside>
        </section>
      </div>
    </main>
  );
}
