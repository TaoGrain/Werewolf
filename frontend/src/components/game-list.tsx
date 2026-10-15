"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, CircleDot, Plus, Radio, ShieldCheck, Sparkles } from "lucide-react";
import { api } from "@/lib/api";
import { formatDate, phaseLabel } from "@/lib/display";
import { EmptyState, ErrorState, LoadingState, Pill } from "./ui";

export function GameList() {
  const games = useQuery({ queryKey: ["games"], queryFn: api.listGames });

  return (
    <main>
      <section className="hero page">
        <div className="hero-copy">
          <Pill tone="gold"><Sparkles size={13} /> 多 Agent 推演实验室</Pill>
          <h1>当所有人都在思考，<br /><em>真相仍藏在夜里。</em></h1>
          <p>创建一场由六名独立 Agent 驱动的狼人杀。你掌控节奏，观察发言、投票与秘密决策，并在终局后还原每一个转折。</p>
          <div className="hero-actions">
            <Link className="button primary" href="/games/new"><Plus size={18} />创建新对局</Link>
            <a className="text-link" href="#games">查看进行中的对局 <ArrowRight size={16} /></a>
          </div>
          <div className="trust-row" aria-label="系统特性">
            <span><ShieldCheck />默认安全观战</span>
            <span><Radio />实时阶段事件</span>
            <span><CircleDot />独立 Agent 记忆</span>
          </div>
        </div>
        <div className="hero-table" aria-hidden="true">
          <div className="moon-disc"><span>DAY 02</span><strong>天亮了</strong><small>昨夜，1 人离场</small></div>
          {["谨", "烈", "静", "疑", "锋", "稳"].map((text, index) => (
            <span key={text} className={`orbit-player p${index + 1}`}>{index + 1}<small>{text}</small></span>
          ))}
          <i className="mist mist-one" /><i className="mist mist-two" />
        </div>
      </section>

      <section className="page games-section" id="games">
        <div className="section-heading">
          <div><span className="eyebrow">RECENT SESSIONS</span><h2>对局大厅</h2></div>
          <Link href="/games/new" className="button subtle"><Plus size={17} />新建</Link>
        </div>

        {games.isLoading && <LoadingState label="正在寻找尚未熄灭的篝火…" />}
        {games.isError && <ErrorState message={games.error.message} action={<button onClick={() => games.refetch()}>重试连接</button>} />}
        {games.data?.length === 0 && (
          <EmptyState title="还没有对局" detail="创建第一场推演，九名 Agent 会在同一张桌前醒来。" action={<Link className="button primary" href="/games/new">创建第一局</Link>} />
        )}
        {!!games.data?.length && (
          <div className="game-grid">
            {games.data.map((game) => {
              const total = game.player_count;
              const alive = game.alive_player_count;
              const finished = game.phase === "FINISHED";
              return (
                <Link className="game-card" href={finished ? `/games/${game.id}/review` : `/games/${game.id}`} key={game.id}>
                  <div className="game-card-top">
                    <Pill tone={finished ? "neutral" : "good"}>{finished ? "已结束" : game.is_paused ? "已暂停" : "进行中"}</Pill>
                    <span>{formatDate(game.updated_at ?? game.created_at)}</span>
                  </div>
                  <h3>{game.name || `第 ${game.id.slice(0, 6)} 号对局`}</h3>
                  <p className="phase-line">第 {Math.max(game.day, 1)} 天 · {phaseLabel(game.phase)}</p>
                  <p className="rules-version">规则版本 · <code>{game.rules_version ?? "API 未提供"}</code></p>
                  <div className="seat-dots" aria-label={alive == null || total == null ? "人数摘要待同步" : `${alive} / ${total} 名玩家存活`}>
                    {total != null && alive != null && Array.from({ length: total }, (_, index) => (
                      <i key={index} className={index < alive ? "alive" : "dead"} />
                    ))}
                  </div>
                  <footer><span>{alive == null || total == null ? "人数待同步" : `${alive} / ${total} 存活`}</span><strong>{finished ? "查看复盘" : "进入观察"}<ArrowRight size={16} /></strong></footer>
                </Link>
              );
            })}
          </div>
        )}
      </section>
    </main>
  );
}
