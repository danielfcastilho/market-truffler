import {
  serverFetch,
  getSystemInfo,
  getMarketStatus,
  getSnifferStatus,
  getResearchStatus,
} from "@/lib/server-api";
import {
  buildVitalsSections,
  formatTimestampUtc,
  type VitalsInput,
  type VitalStatus,
} from "@/lib/vitals";
import { cn } from "@/lib/utils";
import { PageContainer } from "@/components/page-container";

async function getProbe(path: string): Promise<"ok" | "down"> {
  try {
    const response = await serverFetch(path);
    return response.ok ? "ok" : "down";
  } catch {
    return "down";
  }
}

const VALUE_CLASS: Record<VitalStatus, string> = {
  ok: "text-primary",
  down: "text-destructive",
  unavailable: "text-muted-foreground/70",
};

export default async function VitalsPage() {
  const [health, ready, system, market, sniffer, research] = await Promise.all([
    getProbe("/health"),
    getProbe("/ready"),
    getSystemInfo(),
    getMarketStatus(),
    getSnifferStatus(),
    getResearchStatus(),
  ]);

  const input: VitalsInput = { health, ready, system, market, sniffer, research };
  const sections = buildVitalsSections(input);

  return (
    <PageContainer className="max-w-2xl">
      <h1 className="mb-8 text-2xl font-semibold tracking-tight">🩺 Vitals</h1>

      <div className="space-y-8 font-mono text-sm">
        {sections.map((section) => (
          <div key={section.title}>
            <div className="mb-1 border-b border-dashed border-border pb-2 font-semibold tracking-wide">
              {section.title}
            </div>
            <div className="divide-y divide-border/60">
              {section.rows.map((row) => (
                <div key={row.label} className="flex items-center justify-between py-2">
                  <span className="text-muted-foreground">{row.label}</span>
                  <span className={cn("font-medium", VALUE_CLASS[row.status])}>{row.value}</span>
                </div>
              ))}
            </div>
            {section.alerts && section.alerts.length > 0 && (
              <ul aria-label={`${section.title} alerts`} className="mt-2 space-y-2">
                {section.alerts.map((alert) => (
                  <li
                    key={alert.id}
                    data-severity={alert.severity}
                    className={cn(
                      "rounded-md border p-2 text-xs",
                      alert.severity === "critical"
                        ? "border-destructive/60 text-destructive"
                        : "border-amber-500/60 text-amber-800 dark:text-amber-200",
                    )}
                  >
                    <p className="font-semibold">
                      {alert.severity.toUpperCase()} · {alert.registration_id ?? "research worker"}
                      {alert.role ? ` / ${alert.role}` : ""} · {alert.condition.replaceAll("_", " ")}
                      {alert.interval ? ` · ${alert.interval[0]} → ${alert.interval[1]}` : ""}
                    </p>
                    <p className="text-muted-foreground">{alert.explanation}</p>
                    <p className="text-muted-foreground">
                      {alert.state} · first observed {alert.first_observed ? formatTimestampUtc(alert.first_observed) : "—"} · last
                      checked {alert.last_checked ? formatTimestampUtc(alert.last_checked) : "—"}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </div>
        ))}
      </div>
    </PageContainer>
  );
}
