import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { it, expect, vi } from "vitest";
import { SnifferDashboard } from "./sniffer-dashboard";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

const FRAME = {
  frame_time: "2026-01-01T22:18:00Z",
  analyzed_at: "2026-01-01T22:18:05Z",
  instruments_analyzed: 771,
  instruments: [],
};

it("shows a no-analysis message when there is no frame yet", () => {
  render(<SnifferDashboard frame={null} />);
  expect(screen.getByText("No Sniffer analysis yet")).toBeInTheDocument();
});

it("shows frame context and the symbol search, shared above the Truffle tabs", () => {
  render(<SnifferDashboard frame={FRAME} />);
  expect(screen.getByText("Frame 22:18 UTC")).toBeInTheDocument();
  expect(screen.getByText("771 coins analyzed")).toBeInTheDocument();
  expect(screen.getByLabelText("Inspect symbol")).toBeInTheDocument();
});

it("shows Green Truffles and Red Truffles tabs, never Top Long/Short Truffles text", () => {
  render(<SnifferDashboard frame={FRAME} />);
  expect(screen.getByRole("tab", { name: "Green Truffles" })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "Red Truffles" })).toBeInTheDocument();
  expect(screen.queryByText("Top Long Truffles")).not.toBeInTheDocument();
  expect(screen.queryByText("Top Short Truffles")).not.toBeInTheDocument();
  expect(screen.queryByText("Long Truffles")).not.toBeInTheDocument();
  expect(screen.queryByText("Short Truffles")).not.toBeInTheDocument();
});

it("defaults to Green Truffles and shows only one ranking table at a time", () => {
  render(<SnifferDashboard frame={FRAME} />);
  expect(screen.getByRole("tab", { name: "Green Truffles" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  expect(screen.getAllByRole("table")).toHaveLength(1);
  expect(screen.getByRole("table")).toHaveAttribute("data-direction", "long");
});

it("switches to the Red ranking when its tab is selected, and back to Green", async () => {
  const user = userEvent.setup();
  render(<SnifferDashboard frame={FRAME} />);

  await user.click(screen.getByRole("tab", { name: "Red Truffles" }));
  expect(screen.getAllByRole("table")).toHaveLength(1);
  expect(screen.getByRole("table")).toHaveAttribute("data-direction", "short");
  expect(screen.getByRole("tab", { name: "Red Truffles" })).toHaveAttribute(
    "aria-selected",
    "true",
  );

  await user.click(screen.getByRole("tab", { name: "Green Truffles" }));
  expect(screen.getByRole("table")).toHaveAttribute("data-direction", "long");
});

it("shows no fabricated truffles in either direction", async () => {
  const user = userEvent.setup();
  render(<SnifferDashboard frame={FRAME} />);
  expect(screen.getByText("No truffles yet")).toBeInTheDocument();
  expect(screen.getAllByRole("row")).toHaveLength(2);

  await user.click(screen.getByRole("tab", { name: "Red Truffles" }));
  expect(screen.getByText("No truffles yet")).toBeInTheDocument();
});

it("has no awareness of trades, positions, or Warhog", () => {
  render(<SnifferDashboard frame={null} />);
  const text = document.body.textContent ?? "";
  for (const term of ["Position", "Trade", "PnL", "Entry", "Warhog", "Martin Gale"]) {
    expect(text).not.toContain(term);
  }
});
