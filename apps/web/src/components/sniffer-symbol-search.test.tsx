import { render, screen, fireEvent } from "@testing-library/react";
import { it, expect, vi } from "vitest";
import { SnifferSymbolSearch } from "./sniffer-symbol-search";

const pushMock = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock }),
}));

it("navigates to the uppercased symbol's detail page on submit", () => {
  pushMock.mockClear();
  render(<SnifferSymbolSearch />);
  fireEvent.change(screen.getByLabelText("Inspect symbol"), { target: { value: "btcusdt" } });
  fireEvent.click(screen.getByRole("button", { name: "Go" }));
  expect(pushMock).toHaveBeenCalledWith("/sniffer/BTCUSDT");
});

it("trims whitespace before navigating", () => {
  pushMock.mockClear();
  render(<SnifferSymbolSearch />);
  fireEvent.change(screen.getByLabelText("Inspect symbol"), { target: { value: "  ethusdt  " } });
  fireEvent.click(screen.getByRole("button", { name: "Go" }));
  expect(pushMock).toHaveBeenCalledWith("/sniffer/ETHUSDT");
});

it("does not navigate for an empty/whitespace-only symbol", () => {
  pushMock.mockClear();
  render(<SnifferSymbolSearch />);
  fireEvent.change(screen.getByLabelText("Inspect symbol"), { target: { value: "   " } });
  fireEvent.click(screen.getByRole("button", { name: "Go" }));
  expect(pushMock).not.toHaveBeenCalled();
});
