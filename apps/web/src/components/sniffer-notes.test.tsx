import { render, screen } from "@testing-library/react";
import { it, expect } from "vitest";
import { SnifferNotes } from "./sniffer-notes";

it("shows a truthful empty state", () => {
  render(<SnifferNotes />);
  expect(screen.getByRole("heading", { name: "Notes" })).toBeInTheDocument();
  expect(screen.getByText("No scent notes yet")).toBeInTheDocument();
});

it("fabricates no note dimensions or values", () => {
  render(<SnifferNotes />);
  for (const name of ["Momentum", "Stability", "Activity", "Trend", "Liquidity", "Volatility"]) {
    expect(screen.queryByText(name)).not.toBeInTheDocument();
  }
  expect(screen.queryByRole("table")).not.toBeInTheDocument();
});
