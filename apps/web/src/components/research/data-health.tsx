import {
  formatPercent,
  formatTimestamp,
  phaseLabel,
  shortHash,
  type DailyCoverage,
  type DataHealth,
  type RegistrationReadiness,
  type ShardEntry,
} from "@/lib/research";
import { cn } from "@/lib/utils";

/**
 * Research data health: how much history exists and how complete it is,
 * which research shards exist and whether they are continuous, which
 * temporal-contract versions they carry, and how far each prospective
 * registration is from being evaluable. Descriptive only.
 */
export function DataHealthView({ health }: { health: DataHealth }) {
  return (
    <div className="max-w-5xl space-y-10">
      <p className="max-w-3xl text-sm text-muted-foreground">
        Complete data is a precondition for research, not evidence of anything. Computed{" "}
        {formatTimestamp(health.computed_at)} (cached up to {health.cache_seconds}s).
      </p>
      {health.warnings.length > 0 && (
        <section role="status" aria-label="Warnings" className="rounded-xl border border-amber-500/50 bg-amber-500/10 p-4 text-sm">
          <p className="font-medium text-amber-900 dark:text-amber-200">Data issues</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-5 text-muted-foreground">
            {health.warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        </section>
      )}
      <Overview health={health} />
      <Readiness readiness={health.readiness} />
      <Daily health={health} />
      <Shards health={health} />
      <details className="text-xs text-muted-foreground">
        <summary className="cursor-pointer">Limitations</summary>
        <ul className="mt-1 list-disc space-y-0.5 pl-5">
          {health.limitations.map((l) => (
            <li key={l}>{l}</li>
          ))}
        </ul>
      </details>
    </div>
  );
}

function Stat({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="rounded-xl border border-border p-4">
      <p className="font-mono text-xs text-muted-foreground uppercase">{label}</p>
      <p className="mt-1 text-xl font-semibold">{value}</p>
      {note && <p className="mt-1 text-xs text-muted-foreground">{note}</p>}
    </div>
  );
}

function Overview({ health }: { health: DataHealth }) {
  const f = health.frames;
  const shards = health.shards.datasets;
  const contract = health.temporal_contract;
  return (
    <section aria-label="Overview" className="grid gap-3 md:grid-cols-4">
      {f.available ? (
        <>
          <Stat
            label="Frame coverage"
            value={formatPercent(f.coverage)}
            note={`${f.finalized} of ${f.expected} grid frames, ${formatTimestamp(f.observed_from)} → ${formatTimestamp(f.observed_to)}`}
          />
          <Stat
            label="Analyzed frames"
            value={String(f.analyzed)}
            note={`${f.unanalyzed} unanalyzed · ${f.analysis_unknown} pruned (unknown) · ${f.partial} partial · ${f.building} never finalized`}
          />
        </>
      ) : (
        <Stat label="Frame coverage" value="Unavailable" note={f.reason} />
      )}
      <Stat
        label="Research datasets"
        value={String(shards.length)}
        note={shards.length === 0 ? "No research shard exists yet." : `${shards.filter((s) => s.kind === "daily_shard").length} daily shards`}
      />
      <Stat
        label="Temporal contract"
        value={`v${contract.current_version} current`}
        note={
          Object.entries(contract.datasets)
            .map(([v, d]) => `${d.count} dataset(s) ${v}`)
            .join(" · ") || "No dataset manifests."
        }
      />
    </section>
  );
}

function Readiness({ readiness }: { readiness: DataHealth["readiness"] }) {
  return (
    <section aria-label="Prospective readiness" className="space-y-3">
      <h2 className="text-lg font-semibold">Prospective evaluations</h2>
      {!readiness.available ? (
        <p className="text-sm text-muted-foreground">Readiness unavailable: {readiness.reason}</p>
      ) : readiness.registrations.length === 0 ? (
        <p className="text-sm text-muted-foreground">No prospective registration exists.</p>
      ) : (
        <>
          <p className="text-xs text-muted-foreground">
            As last reported by the research worker ({formatTimestamp(readiness.reported_at)}), using
            the protocol&apos;s own verification and eligibility rules. No result is shown before an
            evaluation runs.
          </p>
          {readiness.registrations.map((r) => (
            <RegistrationCard key={r.registration_id ?? "error"} registration={r} />
          ))}
        </>
      )}
    </section>
  );
}

function RegistrationCard({ registration }: { registration: RegistrationReadiness }) {
  return (
    <article className="space-y-2 rounded-xl border border-border p-4 text-sm" data-registration={registration.registration_id ?? ""}>
      <p className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="font-mono font-medium">{registration.registration_id ?? "unreadable registration"}</span>
        <span className="font-mono text-xs text-muted-foreground">
          {registration.kind} · {shortHash(registration.registration_hash)} ·{" "}
          <span className={registration.verification === "verified" ? "text-primary" : "text-destructive"}>
            {registration.verification}
          </span>
        </span>
      </p>
      {registration.problems.length > 0 && (
        <ul role="alert" className="list-disc pl-5 font-mono text-xs text-destructive">
          {registration.problems.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      )}
      <table className="w-full text-left font-mono text-xs">
        <thead className="text-muted-foreground">
          <tr>
            <th className="py-1 pr-3 font-medium">Partition</th>
            <th className="py-1 pr-3 font-medium">Phase</th>
            <th className="py-1 pr-3 font-medium">Shards</th>
            <th className="py-1 font-medium">Before evaluation</th>
          </tr>
        </thead>
        <tbody>
          {registration.partitions.map((p) => (
            <tr key={p.role} data-phase={p.phase}>
              <td className="py-1 pr-3">
                {p.role} {p.start} → {p.end}
              </td>
              <td className={cn("py-1 pr-3", p.eligible_now && "text-primary")}>{phaseLabel(p.phase)}</td>
              <td className="py-1 pr-3">
                {p.days_with_shard}/{p.days}
                {p.missing_past_days.length > 0 && (
                  <span className="text-destructive"> · {p.missing_past_days.length} elapsed day(s) missing</span>
                )}
              </td>
              <td className="py-1">{p.blocking.length ? p.blocking.join("; ") : "nothing — eligible"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </article>
  );
}

function dayClass(d: DailyCoverage): string {
  if (d.missing === 0 && d.building === 0) return "";
  return d.partial_day ? "bg-muted/40" : "bg-destructive/10";
}

function Daily({ health }: { health: DataHealth }) {
  const f = health.frames;
  if (!f.available) {
    return (
      <section aria-label="Daily coverage" className="space-y-2">
        <h2 className="text-lg font-semibold">Daily coverage</h2>
        <p className="text-sm text-muted-foreground">No completed frame on the 5-minute grid yet.</p>
      </section>
    );
  }
  return (
    <section aria-label="Daily coverage" className="space-y-3">
      <h2 className="text-lg font-semibold">Daily coverage (UTC)</h2>
      <p className="text-xs text-muted-foreground">
        {f.observed_note}. A partial first or last day counts only the slots inside the interval.
        {f.off_grid_frames > 0 && ` ${f.off_grid_frames} frames off the 5-minute grid (pre-5m era) are not counted.`}
      </p>
      <div className="overflow-x-auto rounded-xl ring-1 ring-foreground/10">
        <table className="w-full text-left font-mono text-xs">
          <thead className="bg-muted/50 text-muted-foreground">
            <tr>
              <th className="px-3 py-2 font-medium">Day</th>
              <th className="px-3 py-2 text-right font-medium">Frames</th>
              <th className="px-3 py-2 text-right font-medium">Missing</th>
              <th className="px-3 py-2 text-right font-medium">Coverage</th>
              <th className="px-3 py-2 text-right font-medium">Largest gap</th>
              <th className="px-3 py-2 text-right font-medium">Analyzed v2 / v1</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border/60">
            {f.daily.map((d) => (
              <tr key={d.day} data-day={d.day} className={dayClass(d)}>
                <td className="px-3 py-1.5">
                  {d.day}
                  {d.partial_day && (
                    <span className="ml-2 text-muted-foreground" title="the observation interval cuts this day short">
                      partial day ({d.partial_day.replaceAll("_", " ")})
                    </span>
                  )}
                </td>
                <td className="px-3 py-1.5 text-right">
                  {d.finalized}/{d.expected}
                </td>
                <td className="px-3 py-1.5 text-right">{d.missing + d.building}</td>
                <td className="px-3 py-1.5 text-right">{formatPercent(d.coverage)}</td>
                <td className="px-3 py-1.5 text-right">{d.largest_gap_minutes ? `${d.largest_gap_minutes} min` : "—"}</td>
                <td className="px-3 py-1.5 text-right">
                  {d.analyzed_by_contract.v2} / {d.analyzed_by_contract.v1}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <details className="text-xs">
        <summary className="cursor-pointer text-muted-foreground">
          Missing intervals ({f.missing_intervals.count}
          {f.missing_intervals.truncated && ", most recent shown"})
        </summary>
        <ul className="mt-1 space-y-0.5 font-mono">
          {[...f.missing_intervals.items].reverse().map(([start, end, n]) => (
            <li key={start}>
              {formatTimestamp(start)} → {formatTimestamp(end)} · {n} frames
            </li>
          ))}
        </ul>
      </details>
    </section>
  );
}

function verificationLabel(s: ShardEntry): string {
  return s.verification === "verified" ? "verified" : s.verification === "failed" ? "verification failed" : "not verified this pass";
}

function Shards({ health }: { health: DataHealth }) {
  const datasets = health.shards.datasets;
  const continuity = health.shard_continuity;
  return (
    <section aria-label="Research shards" className="space-y-3">
      <h2 className="text-lg font-semibold">Research shards</h2>
      {continuity.available ? (
        <p className="font-mono text-xs text-muted-foreground">
          Since {formatTimestamp(continuity.shards_start)}:{" "}
          {Object.entries(continuity.by_status)
            .map(([status, v]) => `${v.count} ${status.replaceAll("_", " ")}`)
            .join(" · ")}
        </p>
      ) : (
        <p className="text-xs text-muted-foreground">Shard continuity unavailable: {continuity.reason}</p>
      )}
      {datasets.length === 0 ? (
        <p className="text-sm text-muted-foreground">No research dataset exists.</p>
      ) : (
        <ul className="space-y-2">
          {datasets.map((s) => (
            <li key={s.name} data-dataset={s.name} className="rounded-lg border border-border p-3 text-xs">
              <details>
                <summary className="flex cursor-pointer flex-wrap justify-between gap-2 font-mono">
                  <span className="font-medium">{s.name}</span>
                  <span className="text-muted-foreground">
                    {s.manifest !== "present"
                      ? `manifest ${s.manifest}`
                      : `${s.temporal_contract_version != null ? `contract v${s.temporal_contract_version}` : "contract unrecorded"} · ${verificationLabel(s)}`}
                    {!s.data_file_present && " · data file missing"}
                    {(s.manifest_problems?.length ?? 0) > 0 && " · manifest problems"}
                  </span>
                </summary>
                {s.manifest === "present" && (
                  <dl className="mt-2 grid grid-cols-2 gap-x-3 font-mono md:grid-cols-4">
                    <dt className="text-muted-foreground">interval</dt>
                    <dd className="md:col-span-3">
                      {formatTimestamp(s.first_frame_time)} → {formatTimestamp(s.last_frame_time)}
                    </dd>
                    <dt className="text-muted-foreground">rows</dt>
                    <dd>{s.row_count ?? "unrecorded"}</dd>
                    <dt className="text-muted-foreground">generated</dt>
                    <dd>{s.generated_at ? formatTimestamp(s.generated_at) : "unrecorded"}</dd>
                    <dt className="text-muted-foreground">data sha256</dt>
                    <dd>{shortHash(s.data_sha256)}</dd>
                    <dt className="text-muted-foreground">manifest sha256</dt>
                    <dd>{shortHash(s.manifest_sha256)}</dd>
                  </dl>
                )}
                {(s.manifest_problems?.length ?? 0) > 0 && (
                  <ul className="mt-2 list-disc pl-5 text-destructive">
                    {s.manifest_problems!.map((p) => (
                      <li key={p}>{p}</li>
                    ))}
                  </ul>
                )}
              </details>
            </li>
          ))}
        </ul>
      )}
      {health.shards.overlaps.length > 0 && (
        <p role="alert" className="text-xs text-destructive">
          {health.shards.overlaps.map((o) => `${o.kind}: ${o.datasets.join(" & ")}`).join("; ")}
        </p>
      )}
    </section>
  );
}
