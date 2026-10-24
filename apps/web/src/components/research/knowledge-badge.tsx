import { KNOWLEDGE_META, isKnowledgeStatus, type Direction } from "@/lib/research";
import { cn } from "@/lib/utils";

/**
 * Knowledge status — what accumulated evidence justifies about a
 * hypothesis — styled apart from run verdicts. Untested and insufficient
 * evidence are muted and dashed; preliminary states are amber (exploratory,
 * never "success"); only independently validated states use the primary
 * or destructive colours.
 */
const TONE = {
  muted: "border-dashed border-muted-foreground/60 bg-muted text-muted-foreground",
  neutral: "border-border bg-secondary text-secondary-foreground",
  preliminary:
    "border-amber-500/60 bg-amber-500/10 text-amber-800 dark:text-amber-300",
  supported: "border-primary/60 bg-primary/10 text-primary",
  against: "border-destructive/60 bg-destructive/10 text-destructive",
} as const;

export function KnowledgeBadge({
  status,
  size = "sm",
}: {
  status: string | null | undefined;
  size?: "sm" | "lg";
}) {
  const known = isKnowledgeStatus(status) ? KNOWLEDGE_META[status] : null;
  return (
    <span
      data-knowledge={known ? status : "none"}
      title={known?.meaning ?? "No knowledge ledger revision covers this hypothesis yet."}
      className={cn(
        "inline-flex w-fit shrink-0 items-center rounded-full border font-medium whitespace-nowrap",
        size === "lg" ? "px-3 py-1 text-sm" : "px-2 py-0.5 text-xs",
        known ? TONE[known.tone] : "border-border text-muted-foreground",
      )}
    >
      {known?.label ?? "Not yet in the ledger"}
    </span>
  );
}

export function DirectionTag({ direction }: { direction: Direction | null | undefined }) {
  if (!direction) return null;
  return (
    <span
      data-direction={direction}
      className={cn(
        "inline-flex w-fit items-center rounded-md px-1.5 py-0.5 font-mono text-[0.7rem] font-semibold tracking-wide uppercase",
        direction === "long"
          ? "bg-primary/10 text-primary"
          : "bg-foreground/10 text-foreground",
      )}
    >
      {direction === "long" ? "Long ↗" : "Short ↘"}
    </span>
  );
}
