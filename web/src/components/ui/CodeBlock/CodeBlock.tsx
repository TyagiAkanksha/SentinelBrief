import type { CodeBlockProps } from "./interface";

export function CodeBlock({ text }: CodeBlockProps) {
  return <pre className="overflow-x-auto font-mono text-xs">{text}</pre>;
}
