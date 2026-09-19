import { fireEvent, render, screen, within } from "@testing-library/react";
import { expect, it } from "vitest";
import { SnifferTable } from "./sniffer-table";

it("shows both factual returns with alphabetical default ordering and unavailable values", () => {
  render(<SnifferTable instruments={[
    { instrument_id: 2, symbol: "ETHUSDT", return_5m: "0.01", return_1h: "-0.03" },
    { instrument_id: 1, symbol: "BTCUSDT", return_5m: null, return_1h: "0.03" },
    { instrument_id: 3, symbol: "SOLUSDT", return_5m: "0", return_1h: null },
  ]} />);
  expect(screen.getByRole("columnheader", { name: "return_1h" })).toBeInTheDocument();
  const rows = screen.getAllByRole("row").slice(1);
  expect(within(rows[0]).getByText("BTCUSDT")).toBeInTheDocument();
  expect(within(rows[0]).getByText("+3.00%")).toBeInTheDocument();
  expect(within(rows[0]).getByText("N/A")).toBeInTheDocument();
  expect(within(rows[1]).getByText("+1.00%")).toBeInTheDocument();
  expect(within(rows[1]).getByText("-3.00%")).toBeInTheDocument();
  expect(within(rows[2]).getByText("0.00%")).toBeInTheDocument();
  expect(within(rows[2]).getByText("N/A")).toBeInTheDocument();
});


it("sorts hourly returns numerically in both directions and keeps N/A last", () => {
  render(<SnifferTable instruments={[
    { instrument_id: 1, symbol: "AAA", return_5m: "-1", return_1h: "0.1" },
    { instrument_id: 2, symbol: "BBB", return_5m: "1", return_1h: "-0.03" },
    { instrument_id: 3, symbol: "CCC", return_5m: null, return_1h: null },
    { instrument_id: 4, symbol: "DDD", return_5m: "0", return_1h: "0.02" },
  ]} />);
  const symbols = () => screen.getAllByRole("row").slice(1)
    .map((row) => within(row).getAllByRole("cell")[0].textContent);
  expect(symbols()).toEqual(["AAA", "BBB", "CCC", "DDD"]);
  fireEvent.click(screen.getByRole("button", { name: "return_1h" }));
  expect(symbols()).toEqual(["BBB", "DDD", "AAA", "CCC"]);
  fireEvent.click(screen.getByRole("button", { name: /return_1h/ }));
  expect(symbols()).toEqual(["AAA", "DDD", "BBB", "CCC"]);
  fireEvent.click(screen.getByRole("button", { name: "return_5m" }));
  expect(symbols()).toEqual(["AAA", "DDD", "BBB", "CCC"]);
});
