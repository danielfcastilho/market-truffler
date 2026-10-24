import { VERDICT_META, type Verdict } from "@/lib/research";
import { cn } from "@/lib/utils";

/**
 * The one visual language for evidence: exactly the backend's four
 * verdicts, plus "Run failed" for a run that produced no evaluation.
 * Insufficient data is deliberately muted and dashed — it must never look
 * like a result.
 */
const STYLE: Record<Verdict, string> = {
  positive_evidence: "border-primary/50 bg-primary/10 text-primary",
  negative_evidence: "border-destructive/50 bg-destructive/10 text-destructive",
  inconclusive: "border-border bg-secondary text-secondary-foreground",
  insufficient_data: "border-dashed border-muted-foreground/60 bg-muted text-muted-foreground",
};

export function VerdictBadge({
  verdict,
  failed = false,
  size = "sm",
}: {
  verdict: Verdict | null;
  failed?: boolean;
  size?: "sm" | "lg";
}) {
  const label = failed
    ? "Run failed"
    : verdict
      ? VERDICT_META[verdict].label
      : "Not evaluated";
  const style = failed
    ? "border-destructive text-destructive"
    : verdict
      ? STYLE[verdict]
      : "border-border text-muted-foreground";
  return (
    <span
      data-verdict={failed ? "failed" : (verdict ?? "none")}
      title={verdict && !failed ? VERDICT_META[verdict].meaning : undefined}
      className={cn(
        "inline-flex w-fit shrink-0 items-center rounded-md border font-mono font-medium whitespace-nowrap",
        size === "lg" ? "px-2.5 py-1 text-sm" : "px-1.5 py-0.5 text-xs",
        style,
      )}
    >
      {label}
    </span>
  );
}
