// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { ErrorState } from "@/components/ui/ErrorState";

describe("ErrorState", () => {
  it("renders title and detail with role=alert", () => {
    render(<ErrorState title="API error 503" detail="db down" />);

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("API error 503");
    expect(alert).toHaveTextContent("db down");
  });
});
