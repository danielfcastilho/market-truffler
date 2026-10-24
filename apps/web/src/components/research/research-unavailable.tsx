/** The research API couldn't be read — never presented as "no research". */
export function ResearchUnavailable({ message }: { message: string }) {
  return (
    <div role="alert" className="max-w-xl space-y-1 rounded-lg border border-destructive/50 p-4">
      <p className="font-medium text-destructive">Research records could not be loaded.</p>
      <p className="font-mono text-xs text-muted-foreground">{message}</p>
    </div>
  );
}
