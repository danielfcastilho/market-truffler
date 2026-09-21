import type { TruffleDirection } from "@/components/truffle-icon";

/**
 * One directional Truffle ranking table (`#`, `Symbol`, `Score` columns),
 * shown one at a time beneath the Green/Red `TruffleDirectionTabs` on the
 * 🍄 Truffles dashboard (`/sniffer` itself) — the active tab already
 * identifies the direction (icon + "Green Truffles"/"Red Truffles" text),
 * so this renders no heading of its own. `#` is this symbol's Rank
 * position within the direction; the `Score` column shows the Long/Short
 * Score that rank is based on — never labeled "Scent", which is reserved
 * for the intermediate Pillar-level dimensions a Score is built from, not
 * the aggregate itself. Sniffer has no scoring, ranking, or Truffle
 * qualification yet, so this always renders only a truthful empty state —
 * no Score, rank number, or placeholder coin is ever fabricated. Takes
 * `direction` (unused today beyond documenting which ranking this table
 * represents) so it's ready to fetch/display real per-direction ranked
 * items once that exists, without changing its call sites.
 */
export function TrufflePanel({ direction }: { direction: TruffleDirection }) {
  return (
    <table className="w-full font-mono text-sm" data-direction={direction}>
      <thead>
        <tr className="border-b border-dashed border-border text-left text-muted-foreground">
          <th className="pb-2 font-medium">#</th>
          <th className="pb-2 font-medium">Symbol</th>
          <th className="pb-2 text-right font-medium">Score</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td colSpan={3} className="py-6 text-center text-muted-foreground/70">
            No truffles yet
          </td>
        </tr>
      </tbody>
    </table>
  );
}
