import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { it, expect, vi } from "vitest";
import { TruffleDirectionTabs } from "./truffle-direction-tabs";

it("shows a Green Truffles tab and a Red Truffles tab, never Long/Short text", () => {
  render(<TruffleDirectionTabs active="long" onChange={vi.fn()} />);
  expect(screen.getByRole("tab", { name: "Green Truffles" })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "Red Truffles" })).toBeInTheDocument();
  expect(screen.queryByText("Long Truffles")).not.toBeInTheDocument();
  expect(screen.queryByText("Short Truffles")).not.toBeInTheDocument();
});

it("marks the active direction's tab as selected, not by color alone", () => {
  render(<TruffleDirectionTabs active="long" onChange={vi.fn()} />);
  expect(screen.getByRole("tab", { name: "Green Truffles" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  expect(screen.getByRole("tab", { name: "Red Truffles" })).toHaveAttribute(
    "aria-selected",
    "false",
  );
});

it("calls onChange with the other direction when its tab is clicked", async () => {
  const user = userEvent.setup();
  const onChange = vi.fn();
  render(<TruffleDirectionTabs active="long" onChange={onChange} />);
  await user.click(screen.getByRole("tab", { name: "Red Truffles" }));
  expect(onChange).toHaveBeenCalledWith("short");
});

it("switches direction on ArrowRight/ArrowLeft, matching tab keyboard conventions", async () => {
  const user = userEvent.setup();
  const onChange = vi.fn();
  render(<TruffleDirectionTabs active="long" onChange={onChange} />);
  screen.getByRole("tab", { name: "Green Truffles" }).focus();
  await user.keyboard("{ArrowRight}");
  expect(onChange).toHaveBeenCalledWith("short");
});
