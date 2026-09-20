import { fireEvent, render, screen, within } from "@testing-library/react";
import { expect, it } from "vitest";
import { SnifferTable } from "./sniffer-table";
import { FEATURE_COLUMNS } from "@/lib/sniffer-feature-columns";
import type { SnifferInstrument } from "@/lib/server-api";

const BASE: Omit<SnifferInstrument, "instrument_id" | "symbol"> = {
  return_5m: null,
  return_1h: null,
  rsi_14_5m: null,
  rsi_14_15m: null,
  rsi_14_1h: null,
  rsi_14_4h: null,
};

// Two header rows now exist (group row + leaf row), so data rows start at
// index 2, not 1.
function dataRows() {
  return screen.getAllByRole("row").slice(2);
}

function rowSymbols() {
  return dataRows().map((row) => within(row).getAllByRole("cell")[0].textContent);
}

// Leaf labels repeat across families ("5m" appears under both Returns and
// RSI), so a column is located by its canonical key (the header's `title`
// attribute), never by its ambiguous visible text.
function clickSort(key: string) {
  fireEvent.click(within(screen.getByTitle(key)).getByRole("button"));
}

it("shows factual returns and RSI sniffs with alphabetical default ordering and unavailable values", () => {
  render(
    <SnifferTable
      instruments={[
        {
          instrument_id: 2,
          symbol: "ETHUSDT",
          ...BASE,
          return_5m: "0.01",
          return_1h: "-0.03",
          rsi_14_5m: "63.42",
          rsi_14_1h: "50",
          rsi_14_4h: "10",
        },
        {
          instrument_id: 1,
          symbol: "BTCUSDT",
          ...BASE,
          return_1h: "0.03",
          rsi_14_15m: "80",
        },
        {
          instrument_id: 3,
          symbol: "SOLUSDT",
          ...BASE,
          return_5m: "0",
          rsi_14_5m: "0",
          rsi_14_15m: "100",
          rsi_14_1h: "0",
          rsi_14_4h: "100",
        },
      ]}
    />,
  );

  const rows = dataRows();
  expect(rowSymbols()).toEqual(["BTCUSDT", "ETHUSDT", "SOLUSDT"]);
  // BTCUSDT: return_5m, rsi_14_5m, rsi_14_1h, rsi_14_4h are all null -> 4 N/A cells.
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
  render(
    <SnifferTable
      instruments={[{ instrument_id: 1, symbol: "BTCUSDT", ...BASE, rsi_14_5m: "63.4213" }]}
    />,
  );
  expect(screen.getByText("63.42")).toBeInTheDocument();
  expect(screen.queryByText("63.42%")).not.toBeInTheDocument();
  expect(screen.queryByText("6342.00%")).not.toBeInTheDocument();
});

it("does not apply directional color to RSI values", () => {
  render(
    <SnifferTable
      instruments={[
        { instrument_id: 1, symbol: "BTCUSDT", ...BASE, rsi_14_5m: "80", rsi_14_15m: "20" },
      ]}
    />,
  );
  const high = screen.getByText("80.00");
  const low = screen.getByText("20.00");
  expect(high.className).not.toContain("text-primary");
  expect(high.className).not.toContain("text-destructive");
  expect(low.className).not.toContain("text-primary");
  expect(low.className).not.toContain("text-destructive");
});

it("groups Sniff headers by family without colliding on duplicate leaf labels", () => {
  render(<SnifferTable instruments={[{ instrument_id: 1, symbol: "BTCUSDT", ...BASE }]} />);

  expect(screen.getByRole("columnheader", { name: "Returns" })).toHaveAttribute("colSpan", "2");
  expect(screen.getByRole("columnheader", { name: "RSI" })).toHaveAttribute("colSpan", "4");

  // Two distinct "5m" leaf headers exist (return_5m, rsi_14_5m) — each is
  // identified unambiguously by its canonical key, never by its visible
  // (and duplicated) label.
  expect(screen.getByTitle("return_5m")).toBeInTheDocument();
  expect(screen.getByTitle("rsi_14_5m")).toBeInTheDocument();
  expect(screen.getByTitle("return_1h")).toBeInTheDocument();
  expect(screen.getByTitle("rsi_14_1h")).toBeInTheDocument();
  expect(screen.getAllByText("5m")).toHaveLength(2);
  expect(screen.getAllByText("1h")).toHaveLength(2);
});

it("gives every Sniff column a sensible minimum width", () => {
  render(<SnifferTable instruments={[{ instrument_id: 1, symbol: "BTCUSDT", ...BASE }]} />);
  for (const column of FEATURE_COLUMNS) {
    const header = screen.getByTitle(column.key);
    expect(header.style.minWidth).toBe(`${column.minWidthPx}px`);
  }
});

it("keeps the Symbol column and the header structurally sticky", () => {
  render(<SnifferTable instruments={[{ instrument_id: 1, symbol: "BTCUSDT", ...BASE }]} />);

  const symbolHeader = screen.getByRole("columnheader", { name: /Symbol/ });
  expect(symbolHeader.className).toContain("sticky");
  expect(symbolHeader.className).toContain("left-0");

  const symbolCell = screen.getByRole("cell", { name: "BTCUSDT" });
  expect(symbolCell.className).toContain("sticky");
  expect(symbolCell.className).toContain("left-0");

  const thead = symbolHeader.closest("thead");
  expect(thead).not.toBeNull();
  expect(thead?.className).toContain("sticky");
  expect(thead?.className).toContain("top-0");
});

it("links every Symbol cell to its /sniffer/{symbol} detail page", () => {
  render(
    <SnifferTable
      instruments={[
        { instrument_id: 1, symbol: "BTCUSDT", ...BASE },
        { instrument_id: 2, symbol: "ETHUSDT", ...BASE },
      ]}
    />,
  );
  expect(screen.getByRole("link", { name: "BTCUSDT" })).toHaveAttribute(
    "href",
    "/sniffer/BTCUSDT",
  );
  expect(screen.getByRole("link", { name: "ETHUSDT" })).toHaveAttribute(
    "href",
    "/sniffer/ETHUSDT",
  );
});

it("scrolls horizontally inside its own container, not the page", () => {
  render(<SnifferTable instruments={[{ instrument_id: 1, symbol: "BTCUSDT", ...BASE }]} />);
  const table = screen.getByRole("table");
  expect(table.parentElement?.className).toContain("overflow-auto");
});

it("sorts hourly returns numerically in both directions and keeps N/A last", () => {
  render(
    <SnifferTable
      instruments={[
        { instrument_id: 1, symbol: "AAA", ...BASE, return_5m: "-1", return_1h: "0.1" },
        { instrument_id: 2, symbol: "BBB", ...BASE, return_5m: "1", return_1h: "-0.03" },
        { instrument_id: 3, symbol: "CCC", ...BASE },
        { instrument_id: 4, symbol: "DDD", ...BASE, return_5m: "0", return_1h: "0.02" },
      ]}
    />,
  );
  expect(rowSymbols()).toEqual(["AAA", "BBB", "CCC", "DDD"]);
  clickSort("return_1h");
  expect(rowSymbols()).toEqual(["BBB", "DDD", "AAA", "CCC"]);
  clickSort("return_1h");
  expect(rowSymbols()).toEqual(["AAA", "DDD", "BBB", "CCC"]);
  clickSort("return_5m");
  expect(rowSymbols()).toEqual(["AAA", "DDD", "BBB", "CCC"]);
});

it("sorts each RSI column numerically and keeps N/A last", () => {
  const instruments = [
    { instrument_id: 1, symbol: "AAA", ...BASE, rsi_14_5m: "30", rsi_14_15m: "10", rsi_14_1h: "70" },
    { instrument_id: 2, symbol: "BBB", ...BASE, rsi_14_5m: "90", rsi_14_15m: "60", rsi_14_4h: "40" },
    { instrument_id: 3, symbol: "CCC", ...BASE, rsi_14_15m: "40", rsi_14_1h: "20", rsi_14_4h: "80" },
  ];

  for (const column of ["rsi_14_5m", "rsi_14_15m", "rsi_14_1h", "rsi_14_4h"] as const) {
    const { unmount } = render(<SnifferTable instruments={instruments} />);
    clickSort(column);
    const values = rowSymbols();
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
    { instrument_id: 1, symbol: "BBB", ...BASE, rsi_14_5m: "50" },
    { instrument_id: 2, symbol: "AAA", ...BASE, rsi_14_5m: "10" },
  ];
  const original = [...instruments];
  render(<SnifferTable instruments={instruments} />);
  clickSort("rsi_14_5m");
  expect(instruments).toEqual(original); // the viewer-local sort never mutates the source prop
});

it("filters rows by symbol substring, case-insensitively", () => {
  render(
    <SnifferTable
      instruments={[
        { instrument_id: 1, symbol: "BTCUSDT", ...BASE },
        { instrument_id: 2, symbol: "ETHUSDT", ...BASE },
        { instrument_id: 3, symbol: "SOLUSDT", ...BASE },
      ]}
    />,
  );
  fireEvent.change(screen.getByPlaceholderText("Filter symbol…"), { target: { value: "eth" } });
  expect(rowSymbols()).toEqual(["ETHUSDT"]);
});

it("shows a truthful empty state when no symbol matches the filter, without fabricating rows", () => {
  render(<SnifferTable instruments={[{ instrument_id: 1, symbol: "BTCUSDT", ...BASE }]} />);
  fireEvent.change(screen.getByPlaceholderText("Filter symbol…"), { target: { value: "zzz" } });
  expect(screen.getByText('No symbols match "zzz"')).toBeInTheDocument();
  expect(screen.queryByText("BTCUSDT")).not.toBeInTheDocument();
});
