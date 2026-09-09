// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { CountryFlag } from "@/components/ui/CountryFlag";

describe("CountryFlag", () => {
  it("renders an img-role element with the aria-label and title for a valid code", () => {
    render(<CountryFlag code="DE" />);

    const flag = screen.getByRole("img", { name: "Country DE" });

    expect(flag).toHaveAttribute("title", "DE");
    expect(flag.textContent).toBe("\u{1F1E9}\u{1F1EA}");
    expect(screen.queryByText("DE")).toBeNull();
  });

  it("renders nothing for null, undefined, or an invalid code", () => {
    for (const code of [null, undefined, "xx", "<b>"]) {
      const { container, unmount } = render(<CountryFlag code={code} />);

      expect(container.firstChild).toBeNull();
      unmount();
    }
  });
});
