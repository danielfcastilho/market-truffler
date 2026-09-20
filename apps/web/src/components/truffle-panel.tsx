/**
 * One directional 🍄 Truffle table (`#`, `Symbol`, `Scent` columns), used
 * for the Long and Short panels on the 🍄 Truffles dashboard (`/sniffer`
 * itself). Sniffer has no scoring, ranking, or Truffle qualification yet,
 * so this always renders only a truthful empty state — no Scent, rank
 * number, or placeholder coin is ever fabricated. Kept as its own small
 * component (rather than inlined twice) so it's ready to take a `limit`
 * prop once real ranking exists and the dashboard needs to show more than
 * a top 5.
 */
export function TrufflePanel({ label }: { label: string }) {
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
