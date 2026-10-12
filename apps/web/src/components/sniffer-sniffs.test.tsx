import { render, screen } from "@testing-library/react";
import { it, expect, vi } from "vitest";
import { SnifferSniffs } from "./sniffer-sniffs";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

it("shows a no-analysis message when there is no frame yet", () => {
  render(<SnifferSniffs frame={null} />);
  expect(screen.getByText(/No Sniffer analysis yet/)).toBeInTheDocument();
  // Symbol inspection stays available even before the first analysis.
  expect(screen.getByLabelText("Inspect symbol")).toBeInTheDocument();
});

it("is observation-only: no ranking, score, or truffle surface", () => {
  render(<SnifferSniffs frame={null} />);
  // Rankings and Truffles belong to OINK CORP, not Sniffer.
  expect(screen.queryByText(/truffle/i)).not.toBeInTheDocument();
  expect(screen.queryByText(/score/i)).not.toBeInTheDocument();
  expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
});

it("shows the frame metadata and Sniffs matrix when a frame exists", () => {
  render(
    <SnifferSniffs
      frame={{
        frame_time: "2026-01-01T22:18:00Z",
        analyzed_at: "2026-01-01T22:18:05Z",
        instruments_analyzed: 771,
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
      }}
    />,
  );
  expect(screen.getByText("Frame 22:18 UTC")).toBeInTheDocument();
  expect(screen.getByText("771 coins analyzed")).toBeInTheDocument();
  expect(screen.getByText("BTCUSDT")).toBeInTheDocument();
  expect(screen.getByText("+0.42%")).toBeInTheDocument();
  expect(screen.getByText("+1.84%")).toBeInTheDocument();
});
