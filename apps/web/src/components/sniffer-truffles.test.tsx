import { render, screen, within } from "@testing-library/react";
import { it, expect } from "vitest";
import { SnifferTruffles } from "./sniffer-truffles";

it("shows independent Long and Short Truffle tables", () => {
  render(<SnifferTruffles />);
  expect(screen.getByRole("heading", { name: "Long" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Short" })).toBeInTheDocument();
  expect(screen.getAllByRole("table")).toHaveLength(2);
  expect(screen.getAllByRole("columnheader", { name: "Scent" })).toHaveLength(2);
});

it("truthfully shows no truffles yet without fabricating scent, ranks, or coins", () => {
  render(<SnifferTruffles />);
  const tables = screen.getAllByRole("table");
  for (const table of tables) {
    // Header row plus exactly one empty-state row — no instrument rows.
    expect(within(table).getAllByRole("row")).toHaveLength(2);
    expect(within(table).getByText("No truffles yet")).toBeInTheDocument();
  }
  expect(screen.getAllByText("No truffles yet")).toHaveLength(2);
});
