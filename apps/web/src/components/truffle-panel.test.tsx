import { render, screen } from "@testing-library/react";
import { it, expect } from "vitest";
import { TrufflePanel } from "./truffle-panel";

it("shows the given label and #/Symbol/Score columns", () => {
  render(<TrufflePanel label="Top Long Truffles" />);
  expect(screen.getByRole("heading", { name: "Top Long Truffles" })).toBeInTheDocument();
  expect(screen.getByRole("columnheader", { name: "#" })).toBeInTheDocument();
  expect(screen.getByRole("columnheader", { name: "Symbol" })).toBeInTheDocument();
  expect(screen.getByRole("columnheader", { name: "Score" })).toBeInTheDocument();
  // "Scent" is reserved for the intermediate Pillar-level dimensions, not
  // this final aggregate — never shown as a column header here.
  expect(screen.queryByRole("columnheader", { name: "Scent" })).not.toBeInTheDocument();
});

it("truthfully shows no truffles yet without fabricating score, ranks, or coins", () => {
  render(<TrufflePanel label="Top Long Truffles" />);
  expect(screen.getByRole("table")).toBeInTheDocument();
  // Header row plus exactly one empty-state row — no instrument rows.
  expect(screen.getAllByRole("row")).toHaveLength(2);
  expect(screen.getByText("No truffles yet")).toBeInTheDocument();
});
