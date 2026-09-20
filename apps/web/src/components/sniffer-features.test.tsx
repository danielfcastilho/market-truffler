import { render, screen } from "@testing-library/react";
import { it, expect } from "vitest";
import { SnifferFeatures } from "./sniffer-features";

it("shows a no-analysis message when there is no frame yet", () => {
  render(<SnifferFeatures frame={null} />);
  expect(screen.getByText(/No Sniffer analysis yet/)).toBeInTheDocument();
});

it("shows the frame metadata and feature matrix when a frame exists", () => {
  render(
    <SnifferFeatures
      frame={{
        frame_time: "2026-01-01T22:18:00Z",
        analyzed_at: "2026-01-01T22:18:05Z",
        instruments_analyzed: 771,
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
