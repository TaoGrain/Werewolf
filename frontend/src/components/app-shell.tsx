import type { ReactNode } from "react";
import Link from "next/link";
import { CircleHelp, Plus } from "lucide-react";
import { Brand } from "./brand";

export function AppShell({ children, compact = false }: { children: ReactNode; compact?: boolean }) {
  return (
    <div className={compact ? "app-shell compact" : "app-shell"}>
      <header className="topbar">
        <Brand />
        <nav aria-label="主导航">
          <Link href="/">对局大厅</Link>
          <Link href="/games/new" className="nav-create"><Plus size={16} />新建对局</Link>
          <span className="icon-button" title="本地观察台" aria-label="本地观察台说明"><CircleHelp size={18} /></span>
        </nav>
      </header>
      {children}
    </div>
  );
}
