import type { Metadata } from "next";
import { AppShell } from "@/components/app-shell";
import { GameConsole } from "@/components/game-console";

export const metadata: Metadata = { title: "对局观察" };

export default async function GamePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <AppShell compact><GameConsole gameId={id} /></AppShell>;
}
