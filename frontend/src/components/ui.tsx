import type { ReactNode } from "react";
import { AlertCircle, LoaderCircle } from "lucide-react";

export function LoadingState({ label = "正在读取对局…" }: { label?: string }) {
  return <div className="state-card"><LoaderCircle className="spin" /><p>{label}</p></div>;
}

export function ErrorState({ message, action }: { message: string; action?: ReactNode }) {
  return <div className="state-card error"><AlertCircle /><h2>暂时无法完成</h2><p>{message}</p>{action}</div>;
}

export function EmptyState({ title, detail, action }: { title: string; detail: string; action?: ReactNode }) {
  return <div className="empty-state"><span className="empty-moon" aria-hidden="true" /><h2>{title}</h2><p>{detail}</p>{action}</div>;
}

export function Pill({ children, tone = "neutral" }: { children: ReactNode; tone?: "neutral" | "good" | "danger" | "gold" | "info" }) {
  return <span className={`pill ${tone}`}>{children}</span>;
}

export function JsonBlock({ value }: { value: unknown }) {
  const sanitize = (input: unknown): unknown => {
    if (Array.isArray(input)) return input.map(sanitize);
    if (input && typeof input === "object") {
      return Object.fromEntries(Object.entries(input).filter(([key]) => key !== "reasoning_content").map(([key, child]) => [key, sanitize(child)]));
    }
    return input;
  };
  return <pre className="json-block">{JSON.stringify(sanitize(value ?? null), null, 2)}</pre>;
}
