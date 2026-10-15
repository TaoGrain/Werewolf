"use client";

import { useEffect } from "react";
import { AppShell } from "@/components/app-shell";
import { ErrorState } from "@/components/ui";

export default function GlobalError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => console.error(error), [error]);
  return <AppShell><main className="page narrow"><ErrorState message={error.message} action={<button onClick={reset}>重新加载</button>} /></main></AppShell>;
}
