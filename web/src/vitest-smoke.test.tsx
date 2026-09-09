// @vitest-environment jsdom
import { expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

it("renders into jsdom with RTL and jest-dom matchers", () => {
  render(<p>ok</p>);

  expect(screen.getByText("ok")).toBeInTheDocument();
});
