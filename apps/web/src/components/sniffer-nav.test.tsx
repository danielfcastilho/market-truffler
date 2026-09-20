import { render, screen } from "@testing-library/react";
import { it, expect, vi, beforeEach } from "vitest";
import { usePathname } from "next/navigation";
import { SnifferNav } from "./sniffer-nav";

vi.mock("next/navigation", () => ({
  usePathname: vi.fn(),
}));

function setRoute(pathname: string) {
  vi.mocked(usePathname).mockReturnValue(pathname);
}

beforeEach(() => {
  setRoute("/sniffer");
});

it("exposes navigation for exactly Truffles and Sniffs — no Dashboard or Scents tab", () => {
  render(<SnifferNav />);
  expect(screen.getByRole("link", { name: "🍄 Truffles" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Sniffs" })).toBeInTheDocument();
  expect(screen.getAllByRole("link")).toHaveLength(2);
  expect(screen.queryByRole("link", { name: "Dashboard" })).not.toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Scents" })).not.toBeInTheDocument();
});

it("marks 🍄 Truffles as the active tab on /sniffer (the Truffles dashboard) and links Sniffs to its own route", () => {
  render(<SnifferNav />);
  expect(screen.getByRole("link", { name: "🍄 Truffles" })).toHaveAttribute("aria-current", "page");
  expect(screen.getByRole("link", { name: "🍄 Truffles" })).toHaveAttribute("href", "/sniffer");
  expect(screen.getByRole("link", { name: "Sniffs" })).not.toHaveAttribute("aria-current");
  expect(screen.getByRole("link", { name: "Sniffs" })).toHaveAttribute("href", "/sniffer/sniffs");
});

it("marks Sniffs as the active tab on /sniffer/sniffs", () => {
  setRoute("/sniffer/sniffs");
  render(<SnifferNav />);
  expect(screen.getByRole("link", { name: "Sniffs" })).toHaveAttribute("aria-current", "page");
  expect(screen.getByRole("link", { name: "🍄 Truffles" })).not.toHaveAttribute("aria-current");
});

it("highlights no tab on a symbol detail route", () => {
  setRoute("/sniffer/BTCUSDT");
  render(<SnifferNav />);
  expect(screen.getByRole("link", { name: "🍄 Truffles" })).not.toHaveAttribute("aria-current");
  expect(screen.getByRole("link", { name: "Sniffs" })).not.toHaveAttribute("aria-current");
});
