/**
 * Scent Notes: future higher-level interpreted dimensions built from
 * Features, one layer upstream of Scent in Sniffer's pipeline (Features →
 * Scent Notes → Scent → 🍄 Truffles — see docs/ARCHITECTURE.md). This is
 * the product term that replaces the earlier "Pillars" language. No Scent
 * Note is defined yet, so this view intentionally shows only a truthful
 * empty state — no Momentum/Stability/Activity/Trend/Liquidity/Volatility
 * or any other note, value, or formula is invented here.
 */
export function SnifferNotes() {
  return (
    <div className="space-y-2">
      <h2 className="font-mono text-sm font-medium text-foreground">Notes</h2>
      <p className="font-mono text-sm text-muted-foreground/70">No scent notes yet</p>
    </div>
  );
}
