// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { Pagination } from "@/components/ui/Pagination";

const hrefForPage = (page: number): string => `/alerts?page=${page}`;

describe("Pagination", () => {
  it("hides Previous on the first page", () => {
    render(<Pagination page={1} pageSize={25} total={112} hrefForPage={hrefForPage} />);

    expect(screen.queryByText("Previous")).toBeNull();
    expect(screen.getByText("Next")).toBeInTheDocument();
  });

  it("hides Next on the last page", () => {
    render(<Pagination page={5} pageSize={25} total={112} hrefForPage={hrefForPage} />);

    expect(screen.queryByText("Next")).toBeNull();
    expect(screen.getByText("Previous")).toBeInTheDocument();
  });

  it("links Previous and Next via hrefForPage and prints the summary", () => {
    render(<Pagination page={2} pageSize={25} total={112} hrefForPage={hrefForPage} />);

    expect(screen.getByText("Page 2 of 5 · 112 alerts")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Previous" })).toHaveAttribute(
      "href",
      "/alerts?page=1",
    );
    expect(screen.getByRole("link", { name: "Next" })).toHaveAttribute("href", "/alerts?page=3");
  });
});
