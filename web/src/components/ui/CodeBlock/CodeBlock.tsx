import type { CodeBlockProps } from "./interface";

export function CodeBlock({ text }: CodeBlockProps) {
  return (
    <pre className="overflow-x-auto rounded-(--radius) border border-border bg-bg p-3 font-mono text-xs leading-relaxed text-muted">
      {text}
    </pre>
  );
}
