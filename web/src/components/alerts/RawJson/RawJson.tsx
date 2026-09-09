import { CodeBlock } from "@/components/ui/CodeBlock";

import type { RawJsonProps } from "./interface";

export function RawJson({ raw }: RawJsonProps) {
  return (
    <details>
      <summary>Raw session JSON</summary>
      <CodeBlock text={JSON.stringify(raw, null, 2)} />
    </details>
  );
}
