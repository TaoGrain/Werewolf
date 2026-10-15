import type { Metadata } from "next";
import { AppShell } from "@/components/app-shell";
import { CreateGameForm } from "@/components/create-game-form";

export const metadata: Metadata = { title: "创建对局" };

export default function NewGamePage() {
  return <AppShell><CreateGameForm /></AppShell>;
}
