"use client";

import Link from "next/link";
import { useRef, useState, type CSSProperties } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import {
  Activity, AlertTriangle, ArrowRight, Bot, ChevronRight, CirclePause, Eye,
  EyeOff, LoaderCircle, LockKeyhole, Play, RefreshCw, RotateCcw, ShieldAlert,
  Skull, Sparkles, Wifi, WifiOff,
} from "lucide-react";
import { api } from "@/lib/api";
import { eventText, eventTitle, phaseLabel, phaseLabels, roleLabels, visibilityLabels } from "@/lib/display";
import type { GameEvent, Player } from "@/lib/schemas";
import { useGameStream } from "@/hooks/use-game-stream";
import { ErrorState, LoadingState, Pill } from "./ui";
import { RichText } from "./rich-text";

function seatPosition(index: number, total: number): CSSProperties {
  const angle = (Math.PI * 2 * index) / Math.max(total, 1) - Math.PI / 2;
  return { left: `${50 + Math.cos(angle) * 38}%`, top: `${50 + Math.sin(angle) * 40}%` };
}

function PlayerCard({ player, gameId, godMode, revealRoles, index, total }: { player: Player; gameId: string; godMode: boolean; revealRoles: boolean; index: number; total: number }) {
  return (
    <Link href={`/games/${gameId}/agents/${player.id}${godMode ? "?god_mode=true" : ""}`} className={`table-player seat-${index + 1} ${player.is_alive ? "" : "eliminated"}`} style={seatPosition(index, total)}>
      <span className="player-avatar">{player.is_alive ? player.name.slice(0, 1) : <Skull size={19} />}</span>
      <span className="player-info"><small>{player.seat || index + 1} 号</small><strong>{player.name}</strong>{revealRoles && player.role ? <em>{roleLabels[player.role] ?? player.role}</em> : <em>身份隐藏</em>}</span>
      <Bot className="player-bot" size={15} />
    </Link>
  );
}

function TimelineItem({ event, players }: { event: GameEvent; players: Player[] }) {
  const isSpeech = event.type.toUpperCase().includes("SPEECH");
  const isDanger = /DEAD|FAILED|ELIMINATED/.test(event.type.toUpperCase());
  const actor = event.player_name ?? players.find((player) => player.id === event.player_id)?.name;
  return (
    <article className={`timeline-item ${isSpeech ? "speech" : ""} ${isDanger ? "danger" : ""}`}>
      <span className="timeline-dot" />
      <div className="timeline-meta"><span>{event.day ? `第 ${event.day} 天` : "序章"}</span><i>#{event.sequence}</i></div>
      <h3>{actor && <b>{actor} · </b>}{eventTitle(event)}</h3>
      {isSpeech ? (
        <details className="speech-details" open>
          <summary><span>发言内容</span><span className="speech-collapse-label"><span className="when-open">收起</span><span className="when-closed">展开</span><ChevronRight size={14} /></span></summary>
          <RichText compact>{eventText(event, players)}</RichText>
        </details>
      ) : <RichText compact>{eventText(event, players)}</RichText>}
      {event.visibility !== "PUBLIC" && <Pill tone="danger"><LockKeyhole size={12} />{visibilityLabels[event.visibility] ?? event.visibility}</Pill>}
    </article>
  );
}

export function GameConsole({ gameId }: { gameId: string }) {
  const queryClient = useQueryClient();
  const searchParams = useSearchParams();
  const pathname = usePathname();
  const router = useRouter();
  const godMode = searchParams.get("god_mode") === "true";
  const [informationView, setInformationView] = useState<"player" | "host">(godMode ? "host" : "player");
  const commandLock = useRef(false);
  const game = useQuery({ queryKey: ["games", gameId, godMode], queryFn: () => api.getGame(gameId, godMode), refetchInterval: 10000 });
  const stream = useGameStream(gameId, informationView === "host");
  const command = useMutation({
    mutationFn: (action: "start" | "advance" | "pause" | "retry") => api.command(gameId, action),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["games", gameId] });
    },
    onSettled: () => {
      commandLock.current = false;
    },
  });


  const runCommand = (action: "start" | "advance" | "pause" | "retry") => {
    if (commandLock.current) return;
    commandLock.current = true;
    command.mutate(action);
  };
  const setGodMode = (next: boolean) => {
    const params = new URLSearchParams(searchParams.toString());
    if (next) params.set("god_mode", "true"); else params.delete("god_mode");
    router.replace(`${pathname}${params.size ? `?${params}` : ""}`);
  };

  if (game.isLoading) return <main className="page"><LoadingState /></main>;
  if (game.isError || !game.data) return <main className="page"><ErrorState message={game.error?.message ?? "对局不存在"} action={<button onClick={() => game.refetch()}>重试</button>} /></main>;

  const data = game.data;
  const isBusy = data.is_processing || command.isPending;
  const players = data.players.length ? data.players : [];
  const revealRoles = godMode || data.phase === "FINISHED";
  const alive = players.filter((player) => player.is_alive).length;
  const hostInformationAvailable = data.god_mode_enabled || data.phase === "FINISHED";
  const isRunoffDiscussion = data.phase === "DAY_RUNOFF_DISCUSSION";
  const isDiscussion = data.phase === "DAY_DISCUSSION" || isRunoffDiscussion;
  const isHunterReaction = data.phase === "HUNTER_REACTION";
  const isKnownPhase = Object.prototype.hasOwnProperty.call(phaseLabels, data.phase);
  const nextSpeaker = isDiscussion && data.discussion?.next_player_id
    ? players.find((player) => player.id === data.discussion?.next_player_id)
    : undefined;
  const discussionFinished = isDiscussion && !!data.discussion && !data.discussion.next_player_id;
  const isWolfDiscussion = data.phase === "NIGHT_WOLF_DISCUSSION";
  const isWolfKill = data.phase === "NIGHT_WOLF";
  const primaryAction = data.phase === "LOBBY" ? "start" : data.error || data.is_paused ? "retry" : "advance";
  const primaryLabel = data.phase === "LOBBY"
    ? "开始游戏"
    : data.is_paused
      ? "继续并推进"
      : data.error
        ? "重试当前阶段"
        : isHunterReaction
          ? "执行猎人开枪或放弃"
          : nextSpeaker
          ? `${isRunoffDiscussion ? "下一位 PK 发言" : "下一位发言"} · ${nextSpeaker.name}`
          : discussionFinished
            ? isRunoffDiscussion ? "结束 PK 发言并开始重投" : "结束发言并进入投票"
            : isWolfDiscussion
              ? "进行狼人交流"
              : isWolfKill
                ? "确认狼人出刀"
                : "推进下一阶段";
  const idleProgressTitle = isHunterReaction
    ? "等待猎人作出最终选择"
    : nextSpeaker
    ? `等待 ${nextSpeaker.seat} 号 ${nextSpeaker.name}${isRunoffDiscussion ? "进行 PK 发言" : "发言"}`
    : discussionFinished
      ? isRunoffDiscussion ? "PK 候选人均已完成发言" : "所有存活玩家均已发言"
      : data.phase === "FINISHED"
        ? "本局已经落幕"
        : isWolfDiscussion
          ? "等待进行狼人交流"
          : isWolfKill
            ? "狼人交流已结束"
            : "等待主持人指令";
  const idleProgressDetail = isHunterReaction
    ? "猎人可向一名存活玩家开枪，或选择放弃；本阶段只结算一次"
    : isDiscussion && data.discussion
    ? discussionFinished
      ? isRunoffDiscussion ? "确认后进入仅限非候选人参与的 PK 重投" : "确认后进入放逐投票阶段"
      : `已完成 ${data.discussion.completed} / ${data.discussion.total}，点击按钮后才会调用下一位 Agent`
    : isWolfDiscussion
      ? "交流完成后会停在狼人出刀阶段，不会自动出刀"
      : isWolfKill
        ? "点击后两名狼人将分别提交最终刀票"
        : "每次推进只会结算一次";

  return (
    <main className="game-console">
      <section className={`view-warning ${godMode ? "god" : "safe"}`} role="status">
        <span>{godMode ? <ShieldAlert size={17} /> : <EyeOff size={17} />}</span>
        <p><strong>{godMode ? "上帝视角已开启" : "安全观战中"}</strong>{godMode ? "身份、夜间事件和私有记忆可能暴露。" : "身份与夜间秘密由服务端过滤。"}</p>
        <button onClick={() => setGodMode(!godMode)}>{godMode ? <><EyeOff size={15} />切换安全观战</> : <><Eye size={15} />显式开启上帝视角</>}</button>
      </section>

      <div className="game-heading">
        <div><span className="breadcrumb"><Link href="/">对局大厅</Link><ChevronRight size={13} />{data.name || `对局 ${data.id.slice(0, 8)}`}</span><h1>{data.name || "未命名对局"}</h1></div>
        <div className="live-indicator">{stream.connection === "live" ? <Wifi /> : <WifiOff />}<span><strong>{stream.connection === "live" ? "实时连接" : stream.connection === "retrying" ? "正在重连" : "正在连接"}</strong><small>事件 #{stream.events.at(-1)?.sequence ?? 0}</small></span></div>
      </div>

      <div className="game-layout">
        <section className="table-panel panel">
          <div className="phase-strip">
            <div><span>DAY</span><strong>{String(Math.max(data.day, 1)).padStart(2, "0")}</strong></div>
            <i />
            <div><span>CURRENT PHASE</span><strong>{phaseLabel(data.phase)}</strong></div>
            <Pill tone={data.phase.startsWith("NIGHT") ? "info" : data.phase === "FINISHED" ? "gold" : "good"}>{data.is_paused ? "已暂停" : data.phase === "FINISHED" ? "已结束" : "运行中"}</Pill>
          </div>

          <div className={`game-table players-${players.length} ${data.phase.startsWith("NIGHT") ? "night" : "day"}`}>
            <div className="table-center"><span>{data.phase.startsWith("NIGHT") ? "NIGHT" : "DAY"}</span><strong>{phaseLabel(data.phase)}</strong><small>{alive || players.length} 名玩家仍在场</small></div>
            {players.map((player, index) => <PlayerCard key={player.id} player={player} gameId={gameId} godMode={godMode} revealRoles={revealRoles} index={index} total={players.length} />)}
            {!players.length && <div className="waiting-seats">开始游戏后，Agent 将在此入座</div>}
          </div>

          <div className="control-deck">
            <div className="progress-copy">
              {isBusy ? <LoaderCircle className="spin" /> : <Activity />}
              <span><strong>{isBusy ? data.progress?.label || (nextSpeaker ? `${nextSpeaker.name} 正在组织发言` : "Agent 正在完成阶段任务") : idleProgressTitle}</strong><small>{isBusy && data.progress?.total ? `${data.progress.completed} / ${data.progress.total} 项任务完成` : idleProgressDetail}</small></span>
            </div>
            <div className="controls">
              {data.phase !== "LOBBY" && data.phase !== "FINISHED" && !isHunterReaction && <button className="button subtle" disabled={isBusy || !isKnownPhase} onClick={() => runCommand("pause")}><CirclePause size={17} />暂停</button>}
              {data.phase === "FINISHED" ? <Link href={`/games/${gameId}/review`} className="button primary"><Sparkles size={17} />查看赛后复盘</Link> : <button className="button primary" disabled={isBusy || !isKnownPhase} onClick={() => runCommand(primaryAction)}>{data.error ? <RotateCcw size={17} /> : <Play size={17} />}{isKnownPhase ? primaryLabel : "未知阶段，等待服务端更新"}</button>}
            </div>
          </div>
          {(data.error || command.isError) && <div className="inline-alert" role="alert"><AlertTriangle /><div><strong>阶段执行失败</strong><p>{data.error || command.error?.message}</p></div><button onClick={() => runCommand("retry")} disabled={isBusy}><RefreshCw size={15} />重试</button></div>}
        </section>

        <aside className="event-panel panel">
          <header>
            <div className="event-panel-title">
              <div><span className="eyebrow">{informationView === "host" ? "HOST LOG" : "GAME LOG"}</span><h2>{informationView === "host" ? "主持人信息" : "对局信息"}</h2></div>
              <Pill tone={informationView === "host" ? "danger" : undefined}>{stream.events.length}</Pill>
            </div>
            <div className="event-view-tabs" role="tablist" aria-label="信息视角">
              <button role="tab" aria-selected={informationView === "player"} className={informationView === "player" ? "active" : ""} onClick={() => setInformationView("player")}><EyeOff size={14} />对局信息</button>
              <button role="tab" aria-selected={informationView === "host"} className={informationView === "host" ? "active host" : ""} disabled={!hostInformationAvailable} title={hostInformationAvailable ? "显示身份与所有私密事件" : "创建对局时未开启上帝视角"} onClick={() => setInformationView("host")}><ShieldAlert size={14} />主持人信息</button>
            </div>
          </header>
          <div className="timeline-scroll" aria-live="polite">
            {stream.events.length ? stream.events.slice().reverse().map((event) => <TimelineItem event={event} players={players} key={event.id} />) : <div className="mini-empty"><span /><p>暂无{informationView === "host" ? "主持人" : "对局"}信息</p><small>{informationView === "host" ? "狼人出刀、身份分配和其他私密行动会显示在这里。" : "玩家只能看到公开发言、死亡公布和公开投票结果。"}</small></div>}
          </div>
        </aside>
      </div>

      {!!players.length && <section className="agents-strip page-inner">
        <div className="section-heading"><div><span className="eyebrow">智能体名单</span><h2>智能体状态</h2></div><small>点击玩家可查看调用记录；预言家、女巫和猎人可查看历史行动</small></div>
        <div className="agent-cards">{players.map((player, index) => <Link href={`/games/${gameId}/agents/${player.id}${godMode ? "?god_mode=true" : ""}`} key={player.id}><span>{index + 1}</span><div><strong>{player.name}</strong><small>{revealRoles && player.role && ["SEER", "WITCH", "HUNTER"].includes(player.role) ? `查看${roleLabels[player.role]}行动记录` : player.is_alive ? "在线 · 等待阶段任务" : "已离场"}</small></div><ArrowRight size={16} /></Link>)}</div>
      </section>}
    </main>
  );
}
