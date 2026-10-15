import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

export function RichText({ children, compact = false }: { children: string; compact?: boolean }) {
  return (
    <div className={`rich-text${compact ? " compact" : ""}`}>
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{children}</ReactMarkdown>
    </div>
  );
}
