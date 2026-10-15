"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useMutation } from "@tanstack/react-query";
import { ArrowLeft, Bot, Check, Dice5, Eye, EyeOff, Shield, Sparkles } from "lucide-react";
import Link from "next/link";
import { api } from "@/lib/api";

const players = [
  ["沈砚", "沉稳分析"], ["林雾", "直觉敏锐"], ["周烬", "强势进攻"],
  ["许昼", "谨慎保守"], ["白露", "善于共情"], ["陆川", "逻辑优先"],
  ["秦野", "敢于试探"], ["苏禾", "细节敏锐"], ["顾舟", "平衡全局"],
];

type ModelKey = "minimaxM3" | "deepseekv4pro" | "deepseekv4flash";
type RulesetVersion = "mvp-v1" | "classic-9-hunter-v1";

const gameModes: Array<{
  key: RulesetVersion;
  name: string;
  description: string;
  playerCount: number;
  lineupLabel: string;
  roles: Array<{ count: number; name: string; wolf?: boolean }>;
  ruleNote: string;
}> = [
  {
    key: "mvp-v1",
    name: "6 人快速局",
    description: "2 狼人、预言家、女巫、2 平民，采用人数对等胜负规则",
    playerCount: 6,
    lineupLabel: "6 人快速局阵容",
    roles: [
      { count: 2, name: "狼人", wolf: true },
      { count: 1, name: "预言家" },
      { count: 1, name: "女巫" },
      { count: 2, name: "平民" },
    ],
    ruleNote: "人数对等规则：好人消灭全部狼人即获胜；存活狼人人数大于或等于存活好人人数时，狼人获胜。",
  },
  {
    key: "classic-9-hunter-v1",
    name: "经典 9 人局",
    description: "3 狼人、预言家、女巫、猎人、3 平民，采用屠边与人数对等规则",
    playerCount: 9,
    lineupLabel: "经典 9 人阵容",
    roles: [
      { count: 3, name: "狼人", wolf: true },
      { count: 1, name: "预言家" },
      { count: 1, name: "女巫" },
      { count: 1, name: "猎人" },
      { count: 3, name: "平民" },
    ],
    ruleNote: "屠边与人数对等规则：狼人消灭全部神职、全部平民，或存活狼人数大于等于好人数时获胜；猎人被刀或被放逐时可开枪；平票先进行 PK 发言再重投。",
  },
];

const modelOptions: Array<{ key: ModelKey; name: string; description: string }> = [
  { key: "minimaxM3", name: "MiniMax M3", description: "现有稳定模型，沿用提示词校验与格式修复" },
  { key: "deepseekv4pro", name: "DeepSeek V4 Pro", description: "能力优先，使用 DeepSeek 原生 JSON 输出" },
  { key: "deepseekv4flash", name: "DeepSeek V4 Flash", description: "速度与成本优先，使用 DeepSeek 原生 JSON 输出" },
];

export function CreateGameForm() {
  const router = useRouter();
  const [name, setName] = useState("月蚀第 01 局");
  const [seed, setSeed] = useState(20260720);
  const [model, setModel] = useState<ModelKey>("minimaxM3");
  const [rulesetVersion, setRulesetVersion] = useState<RulesetVersion>("classic-9-hunter-v1");
  const [thinkingEnabled, setThinkingEnabled] = useState(false);
  const [speechTemperature, setSpeechTemperature] = useState(0.55);
  const [voteTemperature, setVoteTemperature] = useState(0.35);
  const [actionTemperature, setActionTemperature] = useState(0.5);
  const [godMode, setGodMode] = useState(false);
  const [privateInspection, setPrivateInspection] = useState(false);
  const selectedMode = gameModes.find((mode) => mode.key === rulesetVersion) ?? gameModes[1];
  const selectedPlayers = players.slice(0, selectedMode.playerCount);
  const create = useMutation({
    mutationFn: api.createGame,
    onSuccess: (game) => router.push(`/games/${game.id}${godMode ? "?god_mode=true" : ""}`),
  });

  return (
    <main className="page create-page">
      <Link className="back-link" href="/"><ArrowLeft size={17} />返回对局大厅</Link>
      <div className="create-heading"><span className="eyebrow">NEW SESSION</span><h1>创建一场新对局</h1><p>选择 6 人快速局或经典 9 人局。随机种子让每一次发牌都可复现、可审计。</p></div>
      <form onSubmit={(event) => {
        event.preventDefault();
        create.mutate({
          name,
          ruleset_version: selectedMode.key,
          player_count: selectedMode.playerCount,
          player_names: selectedPlayers.map(([player]) => player),
          seed,
          god_mode: godMode,
          allow_private_inspection: privateInspection,
          model,
          thinking_enabled: thinkingEnabled,
          speech_temperature: speechTemperature,
          vote_temperature: voteTemperature,
          action_temperature: actionTemperature,
        });
      }} className="create-layout">
        <div className="form-stack">
          <section className="panel form-panel">
            <div className="panel-title"><span>01</span><div><h2>游戏模式</h2><p>选择本局人数、角色阵容与胜负规则。</p></div></div>
            {gameModes.map((mode) => (
              <button
                key={mode.key}
                type="button"
                className={`choice-card ${rulesetVersion === mode.key ? "selected" : ""}`}
                onClick={() => setRulesetVersion(mode.key)}
                aria-pressed={rulesetVersion === mode.key}
              >
                <span className="choice-icon"><strong>{mode.playerCount}</strong></span>
                <span><strong>{mode.name}</strong><small>{mode.description}</small></span>
                {rulesetVersion === mode.key && <Check className="choice-check" />}
              </button>
            ))}
          </section>

          <section className="panel form-panel">
            <div className="panel-title"><span>02</span><div><h2>基础设置</h2><p>为这场推演留下一点辨识度。</p></div></div>
            <label className="field"><span>对局名称</span><input value={name} maxLength={40} onChange={(event) => setName(event.target.value)} required /></label>
            <label className="field"><span>随机种子</span><div className="input-action"><input type="number" value={seed} onChange={(event) => setSeed(Number(event.target.value))} required /><button type="button" onClick={() => setSeed(Math.floor(Math.random() * 99999999))} aria-label="生成随机种子"><Dice5 size={18} /></button></div><small>相同规则与种子会得到相同的身份分配。</small></label>
          </section>

          <section className="panel form-panel">
            <div className="panel-title"><span>03</span><div><h2>Agent 模型</h2><p>本局全部玩家将使用同一个模型，对局创建后不可更改。</p></div></div>
            {modelOptions.map((option) => (
              <button
                key={option.key}
                type="button"
                className={`choice-card ${model === option.key ? "selected" : ""}`}
                onClick={() => setModel(option.key)}
                aria-pressed={model === option.key}
              >
                <span className="choice-icon"><Bot /></span>
                <span><strong>{option.name}</strong><small>{option.description}</small></span>
                {model === option.key && <Check className="choice-check" />}
              </button>
            ))}
            <label className="switch-row"><span><strong>开启模型思考</strong><small>默认关闭；开启后可能提高复杂判断质量，但会增加耗时与 Token</small></span><input type="checkbox" checked={thinkingEnabled} onChange={(event) => setThinkingEnabled(event.target.checked)} /><i /></label>
            <details className="advanced-settings">
              <summary>
                <span><strong>高级配置</strong><small>按阶段调整模型输出的稳定性与多样性</small></span>
                <b className="advanced-toggle" aria-hidden="true">+</b>
              </summary>
              <div className="advanced-grid">
                <label className="field"><span>发言温度</span><input aria-label="发言温度" type="number" min={0} max={2} step={0.05} value={speechTemperature} onChange={(event) => setSpeechTemperature(Number(event.target.value))} required /><small>普通发言、PK 发言和遗言；默认 0.55。</small></label>
                <label className="field"><span>投票温度</span><input aria-label="投票温度" type="number" min={0} max={2} step={0.05} value={voteTemperature} onChange={(event) => setVoteTemperature(Number(event.target.value))} required /><small>白天投票与重投；默认 0.35。</small></label>
                <label className="field"><span>行动温度</span><input aria-label="行动温度" type="number" min={0} max={2} step={0.05} value={actionTemperature} onChange={(event) => setActionTemperature(Number(event.target.value))} required /><small>预言家、狼人、女巫和猎人行动；默认 0.5。</small></label>
              </div>
              <p className="temperature-note">取值范围 0～2。数值越低越稳定，数值越高越多样。</p>
            </details>
          </section>

          <section className="panel form-panel">
            <div className="panel-title"><span>04</span><div><h2>观察权限</h2><p>安全观战是默认选择，秘密由服务端过滤。</p></div></div>
            <button type="button" className={`choice-card ${!godMode ? "selected" : ""}`} onClick={() => setGodMode(false)}>
              <span className="choice-icon"><EyeOff /></span><span><strong>安全观战</strong><small>不展示身份、夜间行动与私有记忆</small></span>{!godMode && <Check className="choice-check" />}
            </button>
            <button type="button" className={`choice-card danger-choice ${godMode ? "selected" : ""}`} onClick={() => setGodMode(true)}>
              <span className="choice-icon"><Eye /></span><span><strong>上帝视角</strong><small>显式展示真实身份和全部秘密信息</small></span>{godMode && <Check className="choice-check" />}
            </button>
            <label className="switch-row"><span><strong>允许局中检查私有上下文</strong><small>仅在上帝视角下生效</small></span><input type="checkbox" checked={privateInspection} disabled={!godMode} onChange={(event) => setPrivateInspection(event.target.checked)} /><i /></label>
          </section>
        </div>

        <aside className="setup-summary panel">
          <div className="summary-heading"><Sparkles size={18} /><span>本局阵容</span></div>
          <div className="role-counts" aria-label={selectedMode.lineupLabel}>
            {selectedMode.roles.map((role) => (
              <span key={role.name} className={role.wolf ? "wolf" : undefined}><b>{role.count}</b>{role.name}</span>
            ))}
          </div>
          <div className="agent-list">
            {selectedPlayers.map(([player, style], index) => <div key={player}><span>{index + 1}</span><Bot size={16} /><strong>{player}</strong><small>{style}</small></div>)}
          </div>
          <div className="rule-note"><Shield size={16} /><p>{selectedMode.ruleNote}</p></div>
          {create.isError && <p className="form-error" role="alert">{create.error.message}</p>}
          <button className="button primary large" type="submit" disabled={create.isPending}>{create.isPending ? "正在召集 Agent…" : "创建并进入对局"}</button>
        </aside>
      </form>
    </main>
  );
}
