import { z } from "zod";

export const phaseValues = [
  "LOBBY",
  "ROLE_ASSIGNED",
  "NIGHT_SEER",
  "NIGHT_WOLF_DISCUSSION",
  "NIGHT_WOLF",
  "NIGHT_WITCH",
  "NIGHT_RESOLUTION",
  "DAY_ANNOUNCEMENT",
  "DAY_DISCUSSION",
  "DAY_VOTING",
  "DAY_RUNOFF_DISCUSSION",
  "DAY_RESOLUTION",
  "FINISHED",
  "HUNTER_REACTION",
] as const;

export type KnownGamePhase = (typeof phaseValues)[number];
export const GamePhaseSchema = z.string().min(1);
export type GamePhase = z.infer<typeof GamePhaseSchema>;

const PlayerInnerSchema = z.object({
  id: z.string(),
  seat: z.coerce.number().default(0),
  name: z.string().default("未命名 Agent"),
  is_alive: z.boolean().default(true),
  role: z.string().nullish(),
  status: z.string().nullish(),
  personality: z.record(z.unknown()).nullish(),
  role_history: z.array(z.record(z.unknown())).default([]),
}).passthrough();
export const PlayerSchema = z.preprocess((input) => {
  if (!input || typeof input !== "object") return input;
  const value = input as Record<string, unknown>;
  return { ...value, is_alive: value.is_alive ?? value.status !== "DEAD" };
}, PlayerInnerSchema);
export type Player = z.infer<typeof PlayerSchema>;

const WinResultSchema = z.object({
  winner: z.string(),
  reason: z.string().nullish(),
  reason_code: z.string().nullish(),
  alive_wolves: z.coerce.number().nonnegative().nullish(),
  alive_good: z.coerce.number().nonnegative().nullish(),
  alive_gods: z.coerce.number().nonnegative().nullish(),
  alive_villagers: z.coerce.number().nonnegative().nullish(),
}).passthrough();

const GameInnerSchema = z.object({
  id: z.string(),
  name: z.string().nullish(),
  phase: GamePhaseSchema,
  day: z.coerce.number().default(0),
  status: z.string().default("active"),
  winner: z.string().nullish(),
  win_result: WinResultSchema.nullish(),
  rules_version: z.string().nullish(),
  ruleset_version: z.string().nullish(),
  player_count: z.coerce.number().nonnegative().nullish(),
  alive_player_count: z.coerce.number().nonnegative().nullish(),
  seed: z.coerce.number().nullish(),
  god_mode: z.boolean().optional(),
  god_mode_enabled: z.boolean().default(false),
  thinking_enabled: z.boolean().default(false),
  model: z.enum(["minimaxM3", "deepseekv4pro", "deepseekv4flash"]).nullish(),
  speech_temperature: z.coerce.number().min(0).max(2).default(0.55),
  vote_temperature: z.coerce.number().min(0).max(2).default(0.35),
  action_temperature: z.coerce.number().min(0).max(2).default(0.5),
  is_paused: z.boolean().default(false),
  is_processing: z.boolean().default(false),
  error: z.string().nullish(),
  created_at: z.string().nullish(),
  updated_at: z.string().nullish(),
  players: z.array(PlayerSchema).default([]),
  progress: z.object({
    completed: z.coerce.number().default(0),
    total: z.coerce.number().default(0),
    label: z.string().nullish(),
  }).passthrough().nullish(),
  discussion: z.object({
    completed: z.coerce.number().default(0),
    total: z.coerce.number().default(0),
    completed_player_ids: z.array(z.string()).default([]),
    next_player_id: z.string().nullish(),
  }).passthrough().nullish(),
}).passthrough();
export const GameSchema = z.preprocess((input) => {
  if (!input || typeof input !== "object") return input;
  const value = input as Record<string, unknown>;
  const config = value.config && typeof value.config === "object" ? value.config as Record<string, unknown> : {};
  const structuredWinner = value.win_result && typeof value.win_result === "object"
    ? value.win_result as Record<string, unknown>
    : value.winner && typeof value.winner === "object"
      ? value.winner as Record<string, unknown>
      : null;
  return {
    ...value,
    seed: value.seed ?? value.random_seed ?? config.random_seed,
    god_mode: value.god_mode ?? value.god_view ?? config.god_view,
    speech_temperature: value.speech_temperature ?? config.speech_temperature ?? 0.55,
    vote_temperature: value.vote_temperature ?? config.vote_temperature ?? 0.35,
    action_temperature: value.action_temperature ?? config.action_temperature ?? 0.5,
    winner: structuredWinner?.winner ?? value.winner,
    win_result: structuredWinner,
    rules_version: value.rules_version ?? value.ruleset_version,
    ruleset_version: value.ruleset_version ?? value.rules_version,
    is_paused: value.is_paused ?? value.status === "PAUSED",
    is_processing: value.is_processing ?? value.status === "RUNNING",
  };
}, GameInnerSchema);
export type Game = z.infer<typeof GameSchema>;

const EventInnerSchema = z.object({
  id: z.union([z.string(), z.number()]).transform(String),
  sequence: z.coerce.number().default(0),
  type: z.string().default("event"),
  day: z.coerce.number().default(0),
  phase: z.string().default(""),
  title: z.string().nullish(),
  content: z.string().nullish(),
  message: z.string().nullish(),
  player_id: z.string().nullish(),
  player_name: z.string().nullish(),
  visibility: z.string().default("PUBLIC"),
  created_at: z.string().nullish(),
  payload: z.record(z.unknown()).default({}),
}).passthrough();
export const EventSchema = z.preprocess((input) => {
  if (!input || typeof input !== "object") return input;
  const value = input as Record<string, unknown>;
  const payload = value.payload && typeof value.payload === "object" ? value.payload as Record<string, unknown> : {};
  return {
    ...value,
    id: value.id ?? value.event_id ?? value.sequence,
    content: value.content ?? payload.content ?? payload.text ?? payload.reason,
    player_id: value.player_id ?? value.actor_player_id ?? payload.player_id ?? payload.actor_id,
    player_name: value.player_name ?? payload.player_name,
  };
}, EventInnerSchema);
export type GameEvent = z.infer<typeof EventSchema>;

export const CallSchema = z.object({
  id: z.string(),
  player_id: z.string().nullish(),
  day: z.coerce.number().default(0),
  phase: z.string().default(""),
  call_type: z.string().default("unknown"),
  provider: z.string().default("—"),
  model: z.string().default("—"),
  status: z.string().default("unknown"),
  request_id: z.string().nullish(),
  request_snapshot: z.unknown().nullish(),
  response_text: z.string().nullish(),
  reasoning_text: z.string().nullish(),
  parsed_result: z.unknown().nullish(),
  validation_errors: z.unknown().nullish(),
  input_tokens: z.coerce.number().default(0),
  output_tokens: z.coerce.number().default(0),
  reasoning_tokens: z.coerce.number().default(0),
  latency_ms: z.coerce.number().default(0),
  created_at: z.string().nullish(),
}).passthrough();
export type LlmCall = z.infer<typeof CallSchema>;

export const MemorySchema = z.object({
  player_id: z.string().nullish(),
  current: z.unknown().nullish(),
  snapshots: z.array(z.record(z.unknown())).default([]),
}).passthrough();
export type AgentMemory = z.infer<typeof MemorySchema>;

const ReviewMetricsSchema = z.object({
  vote_count: z.coerce.number().nonnegative().default(0),
  invalid_action_count: z.coerce.number().nonnegative().default(0),
  llm_call_count: z.coerce.number().nonnegative().nullish(),
  total_tokens: z.coerce.number().nonnegative().nullish(),
  average_latency_ms: z.coerce.number().nonnegative().nullish(),
}).passthrough();

const AIReviewDimensionsSchema = z.object({
  role_performance: z.coerce.number().min(0).max(10),
  reasoning: z.coerce.number().min(0).max(10),
  communication: z.coerce.number().min(0).max(10),
  adaptability: z.coerce.number().min(0).max(10),
});

const AgentEvaluationSchema = z.object({
  player_id: z.string(),
  overall_score: z.coerce.number().min(0).max(100),
  dimensions: AIReviewDimensionsSchema,
  summary: z.string(),
  strengths: z.array(z.string()).default([]),
  improvements: z.array(z.string()).default([]),
  decisive_moment: z.string().nullish(),
});

export const AIReviewSchema = z.object({
  overall_summary: z.string(),
  evaluations: z.array(AgentEvaluationSchema).default([]),
  model_key: z.string(),
  model: z.string(),
  provider: z.string(),
  generated_at: z.string(),
  llm_call_id: z.string().nullish(),
  usage: z.object({
    input_tokens: z.coerce.number().default(0),
    output_tokens: z.coerce.number().default(0),
    reasoning_tokens: z.coerce.number().default(0),
    total_tokens: z.coerce.number().default(0),
  }).passthrough().optional(),
}).passthrough();
export type AIReview = z.infer<typeof AIReviewSchema>;

export const ReviewSchema = z.object({
  game: GameSchema.optional(),
  events: z.array(EventSchema).default([]),
  players: z.array(PlayerSchema).default([]),
  metrics: ReviewMetricsSchema.default({}),
  calls: z.array(CallSchema).default([]),
  ai_review: AIReviewSchema.nullish(),
}).passthrough();
export type GameReview = z.infer<typeof ReviewSchema>;

export type CreateGameInput = {
  name: string;
  player_count: number;
  seed?: number;
  god_mode: boolean;
  allow_private_inspection: boolean;
  model: "minimaxM3" | "deepseekv4pro" | "deepseekv4flash";
  thinking_enabled: boolean;
  speech_temperature: number;
  vote_temperature: number;
  action_temperature: number;
  ruleset_version?: "mvp-v1" | "classic-9-hunter-v1";
  player_names?: string[];
};
