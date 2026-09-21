import { render, screen } from "@testing-library/react";
import { it, expect } from "vitest";
import { PageContainer } from "./page-container";

it("renders children with the canonical left-aligned padding, never centered", () => {
  render(
    <PageContainer>
      <p>content</p>
    </PageContainer>,
  );
  const node = screen.getByText("content").parentElement!;
  expect(node.className).toContain("px-4");
  expect(node.className).toContain("py-10");
  // The whole point of this primitive: it must never re-introduce the
  // centering that caused pages to misalign with Sniffer.
  expect(node.className).not.toContain("mx-auto");
});

it("lets a page grow its own width without moving the shared left origin", () => {
  render(
    <PageContainer className="max-w-2xl">
      <p>content</p>
    </PageContainer>,
  );
  const node = screen.getByText("content").parentElement!;
  expect(node.className).toContain("max-w-2xl");
  expect(node.className).toContain("px-4");
  expect(node.className).not.toContain("mx-auto");
});
