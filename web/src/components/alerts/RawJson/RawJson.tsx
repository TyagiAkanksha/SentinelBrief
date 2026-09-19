import { CodeBlock } from "@/components/ui/CodeBlock";

import type { RawJsonProps } from "./interface";

export function RawJson({ raw }: RawJsonProps) {
  return (
    <details className="group rounded-(--radius) border border-border bg-surface">
      <summary className="cursor-pointer px-4 py-3 text-sm font-medium text-text select-none marker:text-faint hover:text-accent">
        Raw session JSON
      </summary>
      <div className="border-t border-border p-4">
        <CodeBlock text={JSON.stringify(raw, null, 2)} />
      </div>
    </details>
  );
}
