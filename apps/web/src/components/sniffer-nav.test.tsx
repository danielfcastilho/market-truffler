import { render, screen } from "@testing-library/react";
import { it, expect } from "vitest";
import { SnifferNav } from "./sniffer-nav";

it("exposes navigation for Truffles, Notes, and Features", () => {
  render(<SnifferNav view="truffles" />);
  expect(screen.getByRole("link", { name: "🍄 Truffles" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Notes" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Features" })).toBeInTheDocument();
});

it("marks Truffles as the active view and links Notes/Features correctly", () => {
  render(<SnifferNav view="truffles" />);
  expect(screen.getByRole("link", { name: "🍄 Truffles" })).toHaveAttribute("aria-current", "page");
  expect(screen.getByRole("link", { name: "Notes" })).not.toHaveAttribute("aria-current");
  expect(screen.getByRole("link", { name: "Features" })).not.toHaveAttribute("aria-current");
  expect(screen.getByRole("link", { name: "Notes" })).toHaveAttribute(
    "href",
    "/sniffer?view=notes",
  );
  expect(screen.getByRole("link", { name: "Features" })).toHaveAttribute(
    "href",
    "/sniffer?view=features",
  );
});

it("marks Notes as the active view when on the notes view", () => {
  render(<SnifferNav view="notes" />);
  expect(screen.getByRole("link", { name: "Notes" })).toHaveAttribute("aria-current", "page");
  expect(screen.getByRole("link", { name: "🍄 Truffles" })).not.toHaveAttribute("aria-current");
  expect(screen.getByRole("link", { name: "Features" })).not.toHaveAttribute("aria-current");
});

it("marks Features as the active view when on the features view", () => {
  render(<SnifferNav view="features" />);
  expect(screen.getByRole("link", { name: "Features" })).toHaveAttribute("aria-current", "page");
  expect(screen.getByRole("link", { name: "🍄 Truffles" })).not.toHaveAttribute("aria-current");
  expect(screen.getByRole("link", { name: "Notes" })).not.toHaveAttribute("aria-current");
});
