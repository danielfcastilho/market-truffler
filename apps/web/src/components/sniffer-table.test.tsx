import { fireEvent, render, screen, within } from "@testing-library/react";
import { expect, it } from "vitest";
import { SnifferTable } from "./sniffer-table";

it("shows factual returns and RSI features with alphabetical default ordering and unavailable values", () => {
  render(<SnifferTable instruments={[
    {
      instrument_id: 2,
      symbol: "ETHUSDT",
      return_5m: "0.01",
      return_1h: "-0.03",
      rsi_14_5m: "63.42",
      rsi_14_15m: null,
      rsi_14_1h: "50",
      rsi_14_4h: "10",
    },
    {
      instrument_id: 1,
      symbol: "BTCUSDT",
      return_5m: null,
      return_1h: "0.03",
      rsi_14_5m: null,
      rsi_14_15m: "80",
      rsi_14_1h: null,
      rsi_14_4h: null,
    },
    {
      instrument_id: 3,
      symbol: "SOLUSDT",
      return_5m: "0",
      return_1h: null,
      rsi_14_5m: "0",
      rsi_14_15m: "100",
      rsi_14_1h: "0",
      rsi_14_4h: "100",
    },
  ]} />);
  expect(screen.getByRole("columnheader", { name: "return_1h" })).toBeInTheDocument();
  expect(screen.getByRole("columnheader", { name: "rsi_14_5m" })).toBeInTheDocument();
  expect(screen.getByRole("columnheader", { name: "rsi_14_15m" })).toBeInTheDocument();
  expect(screen.getByRole("columnheader", { name: "rsi_14_1h" })).toBeInTheDocument();
  expect(screen.getByRole("columnheader", { name: "rsi_14_4h" })).toBeInTheDocument();

  const rows = screen.getAllByRole("row").slice(1);
  // BTCUSDT: return_5m, rsi_14_5m, rsi_14_1h, rsi_14_4h are all null -> 4 N/A cells.
  expect(within(rows[0]).getByText("BTCUSDT")).toBeInTheDocument();
  expect(within(rows[0]).getByText("+3.00%")).toBeInTheDocument();
  expect(within(rows[0]).getByText("80.00")).toBeInTheDocument();
  expect(within(rows[0]).getAllByText("N/A")).toHaveLength(4);
  // ETHUSDT
  expect(within(rows[1]).getByText("+1.00%")).toBeInTheDocument();
  expect(within(rows[1]).getByText("-3.00%")).toBeInTheDocument();
  expect(within(rows[1]).getByText("63.42")).toBeInTheDocument();
  expect(within(rows[1]).getByText("50.00")).toBeInTheDocument();
  expect(within(rows[1]).getByText("10.00")).toBeInTheDocument();
  // SOLUSDT: rsi_14_5m/rsi_14_1h are both "0" -> "0.00"; rsi_14_15m/rsi_14_4h are both "100" -> "100.00".
  expect(within(rows[2]).getByText("0.00%")).toBeInTheDocument();
  expect(within(rows[2]).getAllByText("0.00")).toHaveLength(2);
  expect(within(rows[2]).getAllByText("100.00")).toHaveLength(2);
});

it("renders RSI as a plain number, never a percentage", () => {
  render(<SnifferTable instruments={[
    {
      instrument_id: 1,
      symbol: "BTCUSDT",
      return_5m: null,
      return_1h: null,
      rsi_14_5m: "63.4213",
      rsi_14_15m: null,
      rsi_14_1h: null,
      rsi_14_4h: null,
    },
  ]} />);
  expect(screen.getByText("63.42")).toBeInTheDocument();
  expect(screen.queryByText("63.42%")).not.toBeInTheDocument();
  expect(screen.queryByText("6342.00%")).not.toBeInTheDocument();
});

it("does not apply directional color to RSI values", () => {
  render(<SnifferTable instruments={[
    {
      instrument_id: 1,
      symbol: "BTCUSDT",
      return_5m: null,
      return_1h: null,
      rsi_14_5m: "80",
      rsi_14_15m: "20",
      rsi_14_1h: null,
      rsi_14_4h: null,
    },
  ]} />);
  const high = screen.getByText("80.00");
  const low = screen.getByText("20.00");
  expect(high.className).not.toContain("text-primary");
  expect(high.className).not.toContain("text-destructive");
  expect(low.className).not.toContain("text-primary");
  expect(low.className).not.toContain("text-destructive");
});

it("sorts hourly returns numerically in both directions and keeps N/A last", () => {
  render(<SnifferTable instruments={[
    {
      instrument_id: 1,
      symbol: "AAA",
      return_5m: "-1",
      return_1h: "0.1",
      rsi_14_5m: null,
      rsi_14_15m: null,
      rsi_14_1h: null,
      rsi_14_4h: null,
    },
    {
      instrument_id: 2,
      symbol: "BBB",
      return_5m: "1",
      return_1h: "-0.03",
      rsi_14_5m: null,
      rsi_14_15m: null,
      rsi_14_1h: null,
      rsi_14_4h: null,
    },
    {
      instrument_id: 3,
      symbol: "CCC",
      return_5m: null,
      return_1h: null,
      rsi_14_5m: null,
      rsi_14_15m: null,
      rsi_14_1h: null,
      rsi_14_4h: null,
    },
    {
      instrument_id: 4,
      symbol: "DDD",
      return_5m: "0",
      return_1h: "0.02",
      rsi_14_5m: null,
      rsi_14_15m: null,
      rsi_14_1h: null,
      rsi_14_4h: null,
    },
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

it("sorts each RSI column numerically and keeps N/A last", () => {
  const instruments = [
    {
      instrument_id: 1,
      symbol: "AAA",
      return_5m: null,
      return_1h: null,
      rsi_14_5m: "30",
      rsi_14_15m: "10",
      rsi_14_1h: "70",
      rsi_14_4h: null,
    },
    {
      instrument_id: 2,
      symbol: "BBB",
      return_5m: null,
      return_1h: null,
      rsi_14_5m: "90",
      rsi_14_15m: "60",
      rsi_14_1h: null,
      rsi_14_4h: "40",
    },
    {
      instrument_id: 3,
      symbol: "CCC",
      return_5m: null,
      return_1h: null,
      rsi_14_5m: null,
      rsi_14_15m: "40",
      rsi_14_1h: "20",
      rsi_14_4h: "80",
    },
  ];
  const symbols = () => screen.getAllByRole("row").slice(1)
    .map((row) => within(row).getAllByRole("cell")[0].textContent);

  for (const column of ["rsi_14_5m", "rsi_14_15m", "rsi_14_1h", "rsi_14_4h"] as const) {
    const { unmount } = render(<SnifferTable instruments={instruments} />);
    fireEvent.click(screen.getByRole("button", { name: column }));
    const values = symbols();
    const nonNull = instruments.filter((i) => i[column] != null);
    const nullSymbols = instruments.filter((i) => i[column] == null).map((i) => i.symbol);
    const sortedNonNull = [...nonNull].sort((a, b) => Number(a[column]) - Number(b[column]));
    expect(values.slice(0, sortedNonNull.length)).toEqual(sortedNonNull.map((i) => i.symbol));
    expect(values.slice(sortedNonNull.length)).toEqual(nullSymbols);
    unmount();
  }
});

it("mutating the returned array does not affect the original source data", () => {
  const instruments = [
    {
      instrument_id: 1,
      symbol: "BBB",
      return_5m: "1",
      return_1h: null,
      rsi_14_5m: "50",
      rsi_14_15m: null,
      rsi_14_1h: null,
      rsi_14_4h: null,
    },
    {
      instrument_id: 2,
      symbol: "AAA",
      return_5m: "-1",
      return_1h: null,
      rsi_14_5m: "10",
      rsi_14_15m: null,
      rsi_14_1h: null,
      rsi_14_4h: null,
    },
  ];
  const original = [...instruments];
  render(<SnifferTable instruments={instruments} />);
  fireEvent.click(screen.getByRole("button", { name: "rsi_14_5m" }));
  expect(instruments).toEqual(original); // the viewer-local sort never mutates the source prop
});
