import { render, screen } from "@testing-library/react";
import { it, expect } from "vitest";
import { SnifferSymbolDetail } from "./sniffer-symbol-detail";

const FRAME = {
  frame_time: "2026-01-01T22:18:00Z",
  analyzed_at: "2026-01-01T22:18:05Z",
  instruments_analyzed: 2,
  instruments: [
    {
      instrument_id: 1,
      symbol: "BTCUSDT",
      return_5m: "0.0042",
      return_1h: "0.0184",
      rsi_14_5m: "63.42",
      rsi_14_15m: null,
      rsi_14_1h: "50",
      rsi_14_4h: "10",
    },
    {
      instrument_id: 2,
      symbol: "ETHUSDT",
      return_5m: null,
      return_1h: null,
      rsi_14_5m: null,
      rsi_14_15m: null,
      rsi_14_1h: null,
      rsi_14_4h: null,
    },
  ],
};

it("shows the symbol heading and its real existing Sniffs", () => {
  render(<SnifferSymbolDetail symbol="BTCUSDT" frame={FRAME} />);
  expect(screen.getByRole("heading", { name: "BTCUSDT" })).toBeInTheDocument();
  expect(screen.getByText("+0.42%")).toBeInTheDocument();
  expect(screen.getByText("+1.84%")).toBeInTheDocument();
  expect(screen.getByText("63.42")).toBeInTheDocument();
  expect(screen.getByText("50.00")).toBeInTheDocument();
  expect(screen.getByText("10.00")).toBeInTheDocument();
});

it("groups Sniffs into Returns and RSI sections", () => {
  render(<SnifferSymbolDetail symbol="BTCUSDT" frame={FRAME} />);
  expect(screen.getByText("Returns")).toBeInTheDocument();
  expect(screen.getByText("RSI")).toBeInTheDocument();
});

it("shows N/A for unavailable Sniffs, never 0", () => {
  render(<SnifferSymbolDetail symbol="ETHUSDT" frame={FRAME} />);
  expect(screen.getAllByText("N/A").length).toBeGreaterThanOrEqual(6); // 6 Sniffs, all null
  expect(screen.queryByText("0.00")).not.toBeInTheDocument();
  expect(screen.queryByText("0.00%")).not.toBeInTheDocument();
});

it("shows Truffle status, Long Scent, and Short Scent as N/A — never fabricated, never 0", () => {
  render(<SnifferSymbolDetail symbol="BTCUSDT" frame={FRAME} />);
  expect(screen.getByText("Truffle")).toBeInTheDocument();
  expect(screen.getByText("Long Scent")).toBeInTheDocument();
  expect(screen.getByText("Short Scent")).toBeInTheDocument();
  // Truffle/Long Scent/Short Scent + 2 null RSI/return values on BTCUSDT
  // (rsi_14_15m) all render "N/A" — assert at least the three status rows.
  const naValues = screen.getAllByText("N/A");
  expect(naValues.length).toBeGreaterThanOrEqual(3);
});

it("has an empty, unavailable Scents section — no dimension is invented", () => {
  render(<SnifferSymbolDetail symbol="BTCUSDT" frame={FRAME} />);
  expect(screen.getByRole("heading", { name: "Scents" })).toBeInTheDocument();
  expect(screen.getByText("No scents yet")).toBeInTheDocument();
  for (const name of ["Trend", "Pullback", "Momentum", "Stability", "Volatility"]) {
    expect(screen.queryByText(name)).not.toBeInTheDocument();
  }
});

it("handles an unknown symbol cleanly, without fabricating data", () => {
  render(<SnifferSymbolDetail symbol="DOESNOTEXISTUSDT" frame={FRAME} />);
  expect(screen.getByRole("heading", { name: "DOESNOTEXISTUSDT" })).toBeInTheDocument();
  expect(screen.getByText(/No Sniffer data for DOESNOTEXISTUSDT/)).toBeInTheDocument();
  expect(screen.queryByText("Truffle")).not.toBeInTheDocument();
  expect(screen.queryByText("N/A")).not.toBeInTheDocument();
});

it("handles no analysis having run yet cleanly", () => {
  render(<SnifferSymbolDetail symbol="BTCUSDT" frame={null} />);
  expect(screen.getByText(/No Sniffer analysis yet/)).toBeInTheDocument();
});

it("matches a symbol case-insensitively against the frame", () => {
  render(<SnifferSymbolDetail symbol="btcusdt" frame={FRAME} />);
  expect(screen.getByText("+0.42%")).toBeInTheDocument();
});
