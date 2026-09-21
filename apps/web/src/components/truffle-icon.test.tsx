import { render, screen } from "@testing-library/react";
import { it, expect } from "vitest";
import { TruffleQualificationBadge } from "./truffle-icon";

it("gives the qualification badge an accessible name, without visible Green/Red Truffle text", () => {
  render(<TruffleQualificationBadge direction="long" />);
  expect(screen.getByText("Qualified as a Green Truffle")).toHaveClass("sr-only");
  // "Green Truffle" alone (with no "Qualified as a" prefix) would be
  // visible text — it must never appear next to the icon.
  expect(screen.queryByText("Green Truffle")).not.toBeInTheDocument();
});

it("uses the red variant for a short-direction qualification", () => {
  render(<TruffleQualificationBadge direction="short" />);
  expect(screen.getByText("Qualified as a Red Truffle")).toHaveClass("sr-only");
});
