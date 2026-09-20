/**
 * Scents: future interpreted quantitative dimensions built from Sniffs,
 * one layer upstream of Overall Scent in Sniffer's pipeline (Sniffs →
 * Scents → Overall Scent → 🍄 Truffles — see docs/ARCHITECTURE.md). This
 * is the product term that replaces the earlier "Scent Notes" language
 * (which itself replaced "Pillars"). Scents belong to a symbol, not a
 * standalone global view — this section is embedded in
 * `/sniffer/[symbol]/page.tsx`, not its own route. No Scent is defined
 * yet, so it intentionally shows only a truthful empty state — no
 * Trend/Pullback Scent or any other dimension, value, or formula is
 * invented here.
 */
export function SnifferScents() {
  return (
    <div className="space-y-2">
      <h2 className="font-mono text-sm font-medium text-foreground">Scents</h2>
      <p className="font-mono text-sm text-muted-foreground/70">No scents yet</p>
    </div>
  );
}
