import { render, screen } from "@testing-library/react";
import { it, expect } from "vitest";
import { TrufflePanel } from "./truffle-panel";

it("renders no heading of its own — the active Truffle tab already identifies the direction", () => {
  render(<TrufflePanel direction="long" />);
  expect(screen.queryByRole("heading")).not.toBeInTheDocument();
});

it("shows #/Symbol/Score columns", () => {
  render(<TrufflePanel direction="long" />);
  expect(screen.getByRole("columnheader", { name: "#" })).toBeInTheDocument();
  expect(screen.getByRole("columnheader", { name: "Symbol" })).toBeInTheDocument();
  expect(screen.getByRole("columnheader", { name: "Score" })).toBeInTheDocument();
  // "Scent" is reserved for the intermediate Pillar-level dimensions, not
  // this final aggregate — never shown as a column header here.
  expect(screen.queryByRole("columnheader", { name: "Scent" })).not.toBeInTheDocument();
});

it("truthfully shows no truffles yet without fabricating score, ranks, or coins", () => {
  render(<TrufflePanel direction="short" />);
  expect(screen.getByRole("table")).toBeInTheDocument();
  // Header row plus exactly one empty-state row — no instrument rows.
  expect(screen.getAllByRole("row")).toHaveLength(2);
  expect(screen.getByText("No truffles yet")).toBeInTheDocument();
});

it("marks which direction's ranking it renders, for callers/tests to key off", () => {
  render(<TrufflePanel direction="short" />);
  expect(screen.getByRole("table")).toHaveAttribute("data-direction", "short");
});
