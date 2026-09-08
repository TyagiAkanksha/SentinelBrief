import type { RawJsonProps } from "./interface";

export function RawJson({ raw }: RawJsonProps) {
  return (
    <details>
      <summary>Raw session JSON</summary>
      <pre className="overflow-x-auto font-mono text-xs">{JSON.stringify(raw, null, 2)}</pre>
    </details>
  );
}
