import type { Metadata } from "next";
import { Noto_Sans_SC, Noto_Serif_SC } from "next/font/google";
import { Providers } from "@/components/providers";
import "./globals.css";

const sans = Noto_Sans_SC({ subsets: ["latin"], variable: "--font-sans", display: "swap" });
const serif = Noto_Serif_SC({ subsets: ["latin"], variable: "--font-serif", display: "swap" });

export const metadata: Metadata = {
  title: { default: "夜幕议会 · 多 Agent 狼人杀", template: "%s · 夜幕议会" },
  description: "本地运行的多 Agent 狼人杀对局观察、控制与复盘工具。",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body className={`${sans.variable} ${serif.variable}`}>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
