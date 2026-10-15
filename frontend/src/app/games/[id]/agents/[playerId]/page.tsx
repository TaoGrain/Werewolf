import type { Metadata } from "next";
import { AppShell } from "@/components/app-shell";
import { AgentInspector } from "@/components/agent-inspector";

export const metadata: Metadata = { title: "Agent 检查器" };

export default async function InspectorPage({ params }: { params: Promise<{ id: string; playerId: string }> }) {
  const { id, playerId } = await params;
  return <AppShell compact><AgentInspector gameId={id} playerId={playerId} /></AppShell>;
}
