// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";

import { CodeBlock } from "@/components/ui/CodeBlock";

describe("CodeBlock", () => {
  it("renders a PRE with the text verbatim, never as an element", () => {
    const text = '{"a": "<script>x</script>"}';

    const { container } = render(<CodeBlock text={text} />);

    const pre = container.querySelector("pre");
    expect(pre).not.toBeNull();
    expect(pre!.textContent).toBe(text);
    expect(container.querySelector("script")).toBeNull();
  });
});
