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
      return_15m: null,
      return_1h: "0.0184",
      return_4h: null,
      return_24h: null,
      rsi_14_5m: "63.42",
      rsi_14_15m: null,
      rsi_14_1h: "50",
      rsi_14_4h: "10",
      oi_change_5m: "0.0512",
      oi_change_15m: null,
      oi_change_1h: null,
      oi_change_4h: null,
      oi_change_24h: null,
      volatility_15m: null,
      volatility_1h: "0.023",
      volatility_4h: null,
      volatility_24h: null,
      relative_volume_15m: null,
      relative_volume_1h: "2.4",
      relative_volume_4h: null,
      funding_rate_current: "0.0001",
      funding_rate_24h_avg: null,
    },
    {
      instrument_id: 2,
      symbol: "ETHUSDT",
      return_5m: null,
      return_15m: null,
      return_1h: null,
      return_4h: null,
      return_24h: null,
      rsi_14_5m: null,
      rsi_14_15m: null,
      rsi_14_1h: null,
      rsi_14_4h: null,
      oi_change_5m: null,
      oi_change_15m: null,
      oi_change_1h: null,
      oi_change_4h: null,
      oi_change_24h: null,
      volatility_15m: null,
      volatility_1h: null,
      volatility_4h: null,
      volatility_24h: null,
      relative_volume_15m: null,
      relative_volume_1h: null,
      relative_volume_4h: null,
      funding_rate_current: null,
      funding_rate_24h_avg: null,
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
  expect(screen.getByText("+5.12%")).toBeInTheDocument(); // oi_change_5m
  expect(screen.getByText("2.30%")).toBeInTheDocument(); // volatility_1h, unsigned
  expect(screen.getByText("2.4x")).toBeInTheDocument(); // relative_volume_1h
  expect(screen.getByText("+0.0100%")).toBeInTheDocument(); // funding_rate_current
});

it("groups Sniffs into Returns, RSI, OI Δ, Volatility, Relative Volume, and Funding Rate sections", () => {
  render(<SnifferSymbolDetail symbol="BTCUSDT" frame={FRAME} />);
  expect(screen.getByText("Returns")).toBeInTheDocument();
  expect(screen.getByText("RSI")).toBeInTheDocument();
  expect(screen.getByText("OI Δ")).toBeInTheDocument();
  expect(screen.getByText("Volatility")).toBeInTheDocument();
  expect(screen.getByText("Relative Volume")).toBeInTheDocument();
  expect(screen.getByText("Funding Rate")).toBeInTheDocument();
});

it("shows N/A for unavailable Sniffs, never 0", () => {
  render(<SnifferSymbolDetail symbol="ETHUSDT" frame={FRAME} />);
  expect(screen.getAllByText("N/A").length).toBeGreaterThanOrEqual(23); // 23 Sniffs, all null
  expect(screen.queryByText("0.00")).not.toBeInTheDocument();
  expect(screen.queryByText("0.00%")).not.toBeInTheDocument();
});

it("is observation-only: no Scents, Score, Rank, or Truffle sections", () => {
  render(<SnifferSymbolDetail symbol="BTCUSDT" frame={FRAME} />);
  // Rankings and Truffle qualification belong to OINK CORP, not Sniffer.
  for (const name of ["Scents", "Score", "Rank", "Truffles"]) {
    expect(screen.queryByRole("heading", { name })).not.toBeInTheDocument();
  }
  expect(screen.queryByText(/truffle/i)).not.toBeInTheDocument();
});

it("handles an unknown symbol cleanly, without fabricating data", () => {
  render(<SnifferSymbolDetail symbol="DOESNOTEXISTUSDT" frame={FRAME} />);
  expect(screen.getByRole("heading", { name: "DOESNOTEXISTUSDT" })).toBeInTheDocument();
  expect(screen.getByText(/No Sniffer data for DOESNOTEXISTUSDT/)).toBeInTheDocument();
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
