import { render, screen } from "@testing-library/react";
import { it, expect } from "vitest";
import { SnifferScents } from "./sniffer-scents";

it("shows a truthful empty state", () => {
  render(<SnifferScents />);
  expect(screen.getByRole("heading", { name: "Scents" })).toBeInTheDocument();
  expect(screen.getByText("No scents yet")).toBeInTheDocument();
});

it("fabricates no scent dimensions or values", () => {
  render(<SnifferScents />);
  for (const name of ["Trend", "Pullback", "Momentum", "Stability", "Activity", "Liquidity", "Volatility"]) {
    expect(screen.queryByText(name)).not.toBeInTheDocument();
  }
  expect(screen.queryByRole("table")).not.toBeInTheDocument();
});
