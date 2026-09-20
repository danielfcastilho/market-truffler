import { render, screen } from "@testing-library/react";
import { it, expect, vi } from "vitest";
import { SnifferDashboard } from "./sniffer-dashboard";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

it("shows a no-analysis message when there is no frame yet", () => {
  render(<SnifferDashboard frame={null} />);
  expect(screen.getByText("No Sniffer analysis yet")).toBeInTheDocument();
});

it("shows frame context, the symbol search, and top Long/Short Truffle panels", () => {
  render(
    <SnifferDashboard
      frame={{
        frame_time: "2026-01-01T22:18:00Z",
        analyzed_at: "2026-01-01T22:18:05Z",
        instruments_analyzed: 771,
        instruments: [],
      }}
    />,
  );
  expect(screen.getByText("Frame 22:18 UTC")).toBeInTheDocument();
  expect(screen.getByText("771 coins analyzed")).toBeInTheDocument();
  expect(screen.getByLabelText("Inspect symbol")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Top Long Truffles" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Top Short Truffles" })).toBeInTheDocument();
});

it("shows no fabricated truffles — both panels are honestly empty", () => {
  render(
    <SnifferDashboard
      frame={{
        frame_time: "2026-01-01T22:18:00Z",
        analyzed_at: "2026-01-01T22:18:05Z",
        instruments_analyzed: 771,
        instruments: [],
      }}
    />,
  );
  expect(screen.getAllByText("No truffles yet")).toHaveLength(2);
  // Each panel is just a header row + one empty-state row — no instrument rows.
  expect(screen.getAllByRole("row")).toHaveLength(4);
});

it("has no awareness of trades, positions, or Warhog", () => {
  render(<SnifferDashboard frame={null} />);
  const text = document.body.textContent ?? "";
  for (const term of ["Position", "Trade", "PnL", "Entry", "Warhog", "Martin Gale"]) {
    expect(text).not.toContain(term);
  }
});
