import type { Metadata } from "next";
import { AppShell } from "@/components/app-shell";
import { ReviewDashboard } from "@/components/review-dashboard";

export const metadata: Metadata = { title: "赛后复盘" };

export default async function ReviewPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <AppShell compact><ReviewDashboard gameId={id} /></AppShell>;
}
