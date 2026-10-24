import { VerdictBadge } from "@/components/research/verdict-badge";
import {
  VERDICT_META,
  evidenceFigures,
  formatCount,
  formatReturn,
  formatSpread,
  formatTimestamp,
  segmentLabel,
  verdictOf,
  type RunRecord,
} from "@/lib/research";

/**
 * Everything one run recorded, in reading order: the verdict first, then
 * why, then sample size, then the estimates — labelled as descriptive
 * observations whenever the verdict is not an evidence verdict — and the
 * uncertainty interval only if the evaluator computed one.
 */
export function RunEvidence({ record }: { record: RunRecord }) {
  const failed = record.status !== "completed";
  const verdict = verdictOf(record);
  const report = record.report ?? {};
  const figures = evidenceFigures(record);
  const isEvidence = verdict === "positive_evidence" || verdict === "negative_evidence";
  const uncertainty = report.uncertainty;

  return (
    <div className="space-y-4 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <VerdictBadge verdict={verdict} failed={failed} size="lg" />
        <span className="font-mono text-xs text-muted-foreground">
          {segmentLabel(record.segment)} · {formatTimestamp(record.executed_at)}
        </span>
      </div>

      {failed ? (
        <p className="font-mono text-xs text-destructive">
          No evaluation was produced: {record.error ?? "unknown error"}
        </p>
      ) : (
        <>
          {verdict && <p className="text-muted-foreground">{VERDICT_META[verdict].meaning}</p>}
          {report.verdict_reasons && report.verdict_reasons.length > 0 && (
            <div>
              <p className="mb-1 font-mono text-xs text-muted-foreground uppercase">
                {verdict === "insufficient_data"
                  ? "Why no conclusion is possible (minimums required)"
                  : "Criteria not met"}
              </p>
              <ul className="list-disc space-y-0.5 pl-5 font-mono text-xs">
                {report.verdict_reasons.map((reason) => (
                  <li key={reason}>{reason}</li>
                ))}
              </ul>
            </div>
          )}
          <SegmentNote record={record} />

          <dl className="grid grid-cols-2 gap-x-6 gap-y-2 font-mono text-xs sm:grid-cols-4">
            <Figure label="Timestamps with selections" value={formatCount(figures.timestampsWithSelection)} />
            <Figure label={figures.daysLabel} value={formatCount(figures.days)} />
            <Figure label="Distinct coins selected" value={formatCount(figures.symbols)} />
            <Figure
              label={figures.kind === "comparison" ? "Candidate rows" : "Selected rows with outcome"}
              value={formatCount(figures.selectedRows)}
            />
          </dl>

          <div className={isEvidence ? "" : "rounded-md border border-dashed border-border p-3"}>
            {!isEvidence && (
              <p className="mb-2 font-mono text-xs text-muted-foreground uppercase">
                Descriptive observations — not evidence
              </p>
            )}
            <dl className="grid grid-cols-1 gap-x-6 gap-y-2 font-mono text-xs sm:grid-cols-3">
              <Figure
                label={figures.kind === "comparison" ? "Candidate mean outcome" : "Selected mean outcome"}
                value={formatReturn(figures.selectedMean)}
              />
              <Figure label={figures.baselineLabel} value={formatReturn(figures.baselineMean)} />
              <Figure
                label={
                  figures.kind === "comparison"
                    ? "Incremental spread (timestamp-weighted)"
                    : "Spread vs universe (timestamp-weighted)"
                }
                value={formatSpread(figures.spread)}
              />
            </dl>
          </div>

          <p className="font-mono text-xs">
            {uncertainty && !uncertainty.suppressed && uncertainty.interval ? (
              <>
                {Math.round((uncertainty.confidence_level ?? 0) * 100)}% interval of the spread:{" "}
                [{formatSpread(uncertainty.interval[0])}, {formatSpread(uncertainty.interval[1])}] —{" "}
                <span className="text-muted-foreground">{uncertainty.method}</span>
              </>
            ) : (
              <span className="text-muted-foreground">
                No uncertainty interval:{" "}
                {uncertainty?.reason ?? "the evaluator did not record one"}.
              </span>
            )}
          </p>

          {report.disclosures && report.disclosures.length > 0 && (
            <details className="text-xs">
              <summary className="cursor-pointer font-mono text-muted-foreground">
                Standing limitations ({report.disclosures.length})
              </summary>
              <ul className="mt-2 list-disc space-y-1 pl-5 text-muted-foreground">
                {report.disclosures.map((d) => (
                  <li key={d}>{d}</li>
                ))}
              </ul>
            </details>
          )}
        </>
      )}
    </div>
  );
}

function SegmentNote({ record }: { record: RunRecord }) {
  if (record.segment === "validation") {
    const reuse = record.report?.validation_reuse;
    return (
      <p className="font-mono text-xs text-muted-foreground">
        Validation segment.{" "}
        {reuse?.independent === false
          ? `Not independent: ${reuse.prior_validation_runs_overlapping ?? "earlier"} earlier validation run(s) used overlapping days.`
          : reuse?.independent
            ? "No earlier validation run used these days."
            : ""}
      </p>
    );
  }
  return (
    <p className="font-mono text-xs text-muted-foreground">
      Development segment — exploratory evidence, not independent validation.
    </p>
  );
}

function Figure({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="font-medium">{value}</dd>
    </div>
  );
}
