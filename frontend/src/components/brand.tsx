import Link from "next/link";
import { MoonStar } from "lucide-react";

export function Brand() {
  return (
    <Link className="brand" href="/" aria-label="夜幕议会首页">
      <span className="brand-mark" aria-hidden="true"><MoonStar size={20} /></span>
      <span><strong>夜幕议会</strong><small>Agent Werewolf Lab</small></span>
    </Link>
  );
}
