"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, getStreamUrl } from "@/lib/api";
import { EventSchema, type GameEvent } from "@/lib/schemas";

const EMPTY_EVENTS: GameEvent[] = [];

export function useGameStream(gameId: string, godMode: boolean) {
  const queryClient = useQueryClient();
  const streamKey = `${gameId}:${godMode ? "host" : "player"}`;
  const [liveState, setLiveState] = useState<{ key: string; events: GameEvent[] }>({ key: streamKey, events: [] });
  const liveEvents = liveState.key === streamKey ? liveState.events : EMPTY_EVENTS;
  const [connection, setConnection] = useState<"connecting" | "live" | "retrying">("connecting");
  const latestSequence = useRef(0);

  const eventsQuery = useQuery({
    queryKey: ["games", gameId, "events", godMode],
    queryFn: () => api.getEvents(gameId, 0, godMode),
  });

  const events = useMemo(() => {
    const byId = new Map<string, GameEvent>();
    [...(eventsQuery.data ?? []), ...liveEvents].forEach((event) => byId.set(event.id, event));
    return [...byId.values()].sort((a, b) => a.sequence - b.sequence);
  }, [eventsQuery.data, liveEvents]);

  useEffect(() => {
    // 切换公开/主持人视角时必须清空上一视角的增量缓存，避免私密事件回流到公开面板。
    setLiveState({ key: streamKey, events: [] });
    latestSequence.current = 0;
  }, [streamKey]);

  useEffect(() => {
    latestSequence.current = events.at(-1)?.sequence ?? 0;
  }, [events]);

  useEffect(() => {
    let source: EventSource | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let stopped = false;
    let retry = 0;

    const connect = () => {
      if (stopped) return;
      setConnection(retry ? "retrying" : "connecting");
      source = new EventSource(getStreamUrl(gameId, latestSequence.current, godMode));
      source.onopen = async () => {
        retry = 0;
        setConnection("live");
        const missing = await api.getEvents(gameId, latestSequence.current, godMode).catch(() => []);
        if (missing.length) {
          setLiveState((current) => ({
            key: streamKey,
            events: current.key === streamKey ? [...current.events, ...missing] : missing,
          }));
        }
      };
      source.onmessage = (message) => {
        try {
          const parsed = EventSchema.safeParse(JSON.parse(message.data));
          if (!parsed.success) return;
          latestSequence.current = Math.max(latestSequence.current, parsed.data.sequence);
          setLiveState((current) => ({
            key: streamKey,
            events: current.key === streamKey ? [...current.events, parsed.data] : [parsed.data],
          }));
          void queryClient.invalidateQueries({ queryKey: ["games", gameId] });
        } catch {
          // A malformed frame is ignored; the next REST compensation restores consistency.
        }
      };
      source.onerror = () => {
        source?.close();
        if (stopped) return;
        setConnection("retrying");
        retry += 1;
        timer = setTimeout(connect, Math.min(1000 * 2 ** retry, 15000));
      };
    };

    connect();
    return () => {
      stopped = true;
      source?.close();
      if (timer) clearTimeout(timer);
    };
  }, [gameId, godMode, queryClient, streamKey]);

  return { ...eventsQuery, events, connection };
}
