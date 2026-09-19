import { serverFetch, getSystemInfo } from "@/lib/server-api";
import { buildVitalsSections, type VitalsInput, type VitalStatus } from "@/lib/vitals";
import { cn } from "@/lib/utils";

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
  const [health, ready, system] = await Promise.all([
    getProbe("/health"),
    getProbe("/ready"),
    getSystemInfo(),
  ]);

  const input: VitalsInput = { health, ready, system };
  const sections = buildVitalsSections(input);

  return (
    <div className="mx-auto max-w-2xl px-4 py-10 md:px-8 md:py-14">
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
          </div>
        ))}
      </div>
    </div>
  );
}
