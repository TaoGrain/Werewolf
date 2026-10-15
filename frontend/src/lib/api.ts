import { z, type ZodTypeAny } from "zod";
import {
  AIReviewSchema,
  CallSchema,
  EventSchema,
  GameSchema,
  MemorySchema,
  ReviewSchema,
  type CreateGameInput,
} from "./schemas";

const configuredBase = process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "");
if (!configuredBase) {
  throw new Error("NEXT_PUBLIC_API_BASE_URL 未配置");
}
export const API_BASE_URL = configuredBase;

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly detail?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

const gameListSchema = z.union([
  z.array(GameSchema),
  z.object({ games: z.array(GameSchema) }).transform((value) => value.games),
  z.object({ items: z.array(GameSchema) }).transform((value) => value.items),
]);

const eventListSchema = z.union([
  z.array(EventSchema),
  z.object({ events: z.array(EventSchema) }).transform((value) => value.events),
  z.object({ items: z.array(EventSchema) }).transform((value) => value.items),
]);

const callListSchema = z.union([
  z.array(CallSchema),
  z.object({ calls: z.array(CallSchema) }).transform((value) => value.calls),
  z.object({ items: z.array(CallSchema) }).transform((value) => value.items),
]);

function withMode(path: string, godMode = false) {
  const joiner = path.includes("?") ? "&" : "?";
  return `${path}${joiner}god_mode=${godMode ? "true" : "false"}`;
}

async function request<S extends ZodTypeAny>(path: string, schema: S, init?: RequestInit): Promise<z.output<S>> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  });
  const contentType = response.headers.get("content-type") || "";
  const body = contentType.includes("application/json")
    ? await response.json()
    : await response.text();

  if (!response.ok) {
    const message = typeof body === "object" && body && "detail" in body
      ? String(body.detail)
      : `请求失败（${response.status}）`;
    throw new ApiError(message, response.status, body);
  }

  const result = schema.safeParse(body);
  if (!result.success) {
    throw new ApiError("服务端返回了无法识别的数据格式", 502, result.error.flatten());
  }
  return result.data;
}

export const api = {
  listGames: () => request("/api/games", gameListSchema),
  createGame: (input: CreateGameInput) =>
    request("/api/games", GameSchema, { method: "POST", body: JSON.stringify(input) }),
  getGame: (id: string, godMode = false) =>
    request(withMode(`/api/games/${id}`, godMode), GameSchema),
  getEvents: (id: string, after = 0, godMode = false) =>
    request(withMode(`/api/games/${id}/events?after=${after}`, godMode), eventListSchema),
  command: (id: string, action: "start" | "advance" | "pause" | "retry") =>
    request(`/api/games/${id}/${action}`, z.unknown(), {
      method: "POST",
      headers: { "Idempotency-Key": crypto.randomUUID() },
    }),
  getCalls: (id: string, playerId: string, godMode = false) =>
    request(withMode(`/api/games/${id}/players/${playerId}/calls`, godMode), callListSchema),
  getMemory: (id: string, playerId: string, godMode = false) =>
    request(withMode(`/api/games/${id}/players/${playerId}/memory`, godMode), MemorySchema),
  getReview: (id: string, playerId?: string) => {
    const suffix = playerId ? `?player_id=${encodeURIComponent(playerId)}` : "";
    return request(`/api/games/${id}/review${suffix}`, ReviewSchema);
  },
  generateAIReview: (
    id: string,
    model: "minimaxM3" | "deepseekv4pro" | "deepseekv4flash",
  ) => request(`/api/games/${id}/review/ai`, AIReviewSchema, {
    method: "POST",
    headers: { "Idempotency-Key": crypto.randomUUID() },
    body: JSON.stringify({ model }),
  }),
};

export function getStreamUrl(id: string, lastSequence: number, godMode: boolean) {
  const params = new URLSearchParams({
    after: String(lastSequence),
    god_mode: String(godMode),
  });
  return `${API_BASE_URL}/api/games/${id}/stream?${params.toString()}`;
}
