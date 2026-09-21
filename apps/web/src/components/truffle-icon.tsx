import { cn } from "@/lib/utils";

export type TruffleDirection = "long" | "short";

/**
 * The product's directional visual language: a Green Truffle is a symbol
 * that qualifies from the Long ranking, a Red Truffle one that qualifies
 * from the Short ranking. `long`/`short` remain the internal engineering
 * names (matching Long Score/Rank, Short Score/Rank) — Green/Red is the
 * user-facing Truffle vocabulary built on top of them, used for visible
 * labels such as the Truffle direction tabs.
 *
 * Plural ("Green Truffles") names the dashboard's collection of
 * qualifying symbols for that direction; singular ("Green Truffle") names
 * one specific symbol's qualification state on its own detail page.
 */
export const TRUFFLE_DIRECTION_LABEL: Record<TruffleDirection, string> = {
  long: "Green Truffles",
  short: "Red Truffles",
};

export const TRUFFLE_DIRECTION_SINGULAR_LABEL: Record<TruffleDirection, string> = {
  long: "Green Truffle",
  short: "Red Truffle",
};

const CAP_CLASS: Record<TruffleDirection, string> = {
  long: "fill-primary",
  short: "fill-destructive",
};

/**
 * Both directions render the exact same mushroom silhouette — only the
 * cap's fill color differs (green vs. red) — so direction is communicated
 * purely by color on an otherwise-identical shape, never by a different
 * icon or by text. Always `aria-hidden`: pair it with an accessible label
 * (see `TRUFFLE_DIRECTION_LABEL`) on the surrounding element instead.
 */
export function TruffleIcon({
  direction,
  className,
}: {
  direction: TruffleDirection;
  className?: string;
}) {
  return (
    <svg
      viewBox="0 0 24 24"
      aria-hidden="true"
      className={cn("h-6 w-6", className)}
    >
      <path
        className="fill-muted-foreground/30"
        d="M9 13 L9.5 20.5 C9.5 21.6 10.6 22.5 12 22.5 C13.4 22.5 14.5 21.6 14.5 20.5 L15 13 Z"
      />
      <path
        className={CAP_CLASS[direction]}
        d="M4 11 C4 5.5 7.8 1.5 12 1.5 C16.2 1.5 20 5.5 20 11 C20 12.1 19.1 13 18 13 H6 C4.9 13 4 12.1 4 11 Z"
      />
    </svg>
  );
}

/**
 * A qualification badge for a directional Score row: this direction's
 * Score/Rank qualified as a Truffle. Deliberately icon-only — the Score
 * row's own Long/Short label already establishes direction, so repeating
 * visible "Green Truffle"/"Red Truffle" text next to it would be
 * redundant. The accessible name ("Qualified as a Green/Red Truffle")
 * lives in `sr-only` text instead, so the qualification isn't lost to
 * anyone who can't rely on the icon's color.
 */
export function TruffleQualificationBadge({ direction }: { direction: TruffleDirection }) {
  const label = `Qualified as a ${TRUFFLE_DIRECTION_SINGULAR_LABEL[direction]}`;
  return (
    <span className="inline-flex items-center" title={label}>
      <TruffleIcon direction={direction} className="h-4 w-4" />
      <span className="sr-only">{label}</span>
    </span>
  );
}
