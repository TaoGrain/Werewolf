import Link from "next/link";
import { AppShell } from "@/components/app-shell";
import { EmptyState } from "@/components/ui";

export default function NotFound() {
  return <AppShell><main className="page narrow"><EmptyState title="迷雾中没有这条路" detail="这个对局或页面不存在。" action={<Link className="button" href="/">返回大厅</Link>} /></main></AppShell>;
}
