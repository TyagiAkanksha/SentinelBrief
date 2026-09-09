import type { ErrorStateProps } from "./interface";

export function ErrorState({ title, detail }: ErrorStateProps) {
  return (
    <div role="alert">
      <h2>{title}</h2>
      <p>{detail}</p>
    </div>
  );
}
