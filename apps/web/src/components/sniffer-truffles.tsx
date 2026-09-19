const SIDES = ["Long", "Short"] as const;

/**
 * Structurally represents Sniffer's future primary surface — independent
 * Long and Short 🍄 Truffle tables, each ordered by that direction's Scent
 * (the UI term for long_score/short_score, see docs/ARCHITECTURE.md) —
 * without fabricating any data. Sniffer has no scoring, ranking, or Truffle
 * qualification yet, so both tables intentionally render only an empty
 * state: no Scent, no rank number, no placeholder coins. A coin may
 * eventually surface as a Truffle on both sides at once, so Long and Short
 * are kept as two separate tables rather than one combined/opposed scale.
 */
export function SnifferTruffles() {
  return (
    <div className="grid gap-8 md:grid-cols-2">
      {SIDES.map((side) => (
        <TrufflePanel key={side} label={side} />
      ))}
    </div>
  );
}

function TrufflePanel({ label }: { label: string }) {
  return (
    <div className="space-y-2">
      <h2 className="font-mono text-sm font-medium text-foreground">{label}</h2>
      <table className="w-full font-mono text-sm">
        <thead>
          <tr className="border-b border-dashed border-border text-left text-muted-foreground">
            <th className="pb-2 font-medium">#</th>
            <th className="pb-2 font-medium">Symbol</th>
            <th className="pb-2 text-right font-medium">Scent</th>
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
    </div>
  );
}
