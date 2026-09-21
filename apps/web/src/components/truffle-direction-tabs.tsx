"use client";

import { useRef } from "react";
import { TruffleIcon, TRUFFLE_DIRECTION_LABEL, type TruffleDirection } from "@/components/truffle-icon";
import { cn } from "@/lib/utils";

const DIRECTIONS: TruffleDirection[] = ["long", "short"];

function otherDirection(direction: TruffleDirection): TruffleDirection {
  return direction === "long" ? "short" : "long";
}

export function truffleTabId(direction: TruffleDirection): string {
  return `truffle-tab-${direction}`;
}

export function trufflePanelId(direction: TruffleDirection): string {
  return `truffle-panel-${direction}`;
}

/**
 * Secondary tab control for the Truffles dashboard: switches which
 * directional ranking (Green = Long-qualified, Red = Short-qualified) is
 * shown, one at a time, below the shared Frame/analyzed/symbol-search
 * context. Visually subtler than the primary 🍄 Truffles / Sniffs nav —
 * smaller text, no `mb-8` — so it reads as a sub-view, not a peer.
 *
 * Real ARIA tabs (not the `SnifferNav` Link+aria-current pattern) because
 * this switches panel content instantly via local state rather than
 * navigating: `role="tablist"`/`"tab"`, `aria-selected`, and a roving
 * tabindex with ArrowLeft/ArrowRight (trivial with exactly two tabs — a
 * key press always just selects "the other one").
 */
export function TruffleDirectionTabs({
  active,
  onChange,
}: {
  active: TruffleDirection;
  onChange: (direction: TruffleDirection) => void;
}) {
  const tabRefs = useRef<Partial<Record<TruffleDirection, HTMLButtonElement | null>>>({});

  function handleKeyDown(event: React.KeyboardEvent) {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const next = otherDirection(active);
    onChange(next);
    tabRefs.current[next]?.focus();
  }

  return (
    <div role="tablist" aria-label="Truffle direction" className="flex gap-1 text-sm">
      {DIRECTIONS.map((direction) => {
        const selected = active === direction;
        return (
          <button
            key={direction}
            ref={(node) => {
              tabRefs.current[direction] = node;
            }}
            type="button"
            role="tab"
            id={truffleTabId(direction)}
            aria-selected={selected}
            aria-controls={trufflePanelId(direction)}
            tabIndex={selected ? 0 : -1}
            onClick={() => onChange(direction)}
            onKeyDown={handleKeyDown}
            className={cn(
              "flex items-center gap-1.5 rounded-md px-2.5 py-1 font-medium transition-colors",
              selected
                ? "bg-accent text-accent-foreground"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            <TruffleIcon direction={direction} className="h-4 w-4" />
            {TRUFFLE_DIRECTION_LABEL[direction]}
          </button>
        );
      })}
    </div>
  );
}
