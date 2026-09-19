import { serverFetch, getSystemInfo } from "@/lib/server-api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

async function getProbe(path: string): Promise<"ok" | "down"> {
  try {
    const response = await serverFetch(path);
    return response.ok ? "ok" : "down";
  } catch {
    return "down";
  }
}

function StatusBadge({ status }: { status: "ok" | "down" | "unreachable" }) {
  const isOk = status === "ok";
  return (
    <Badge
      variant={isOk ? "secondary" : "destructive"}
      className={isOk ? "text-primary" : undefined}
    >
      {isOk ? "OK" : "DOWN"}
    </Badge>
  );
}

export default async function SystemPage() {
  const [health, ready, system] = await Promise.all([
    getProbe("/health"),
    getProbe("/ready"),
    getSystemInfo(),
  ]);

  const rows: { label: string; value: React.ReactNode }[] = [
    { label: "API liveness", value: <StatusBadge status={health} /> },
    { label: "API readiness", value: <StatusBadge status={ready} /> },
    {
      label: "Database connectivity",
      value: <StatusBadge status={system?.database ?? "unreachable"} />,
    },
    { label: "Application environment", value: system?.environment ?? "unknown" },
    { label: "Backend version", value: system?.version ?? "unknown" },
    { label: "Authenticated user", value: system?.authenticated_user ?? "unknown" },
  ];

  return (
    <div className="mx-auto max-w-2xl px-4 py-10 md:px-8 md:py-14">
      <div className="mb-8 space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight">⚙️ System</h1>
        <p className="text-muted-foreground">
          Live status for the pieces of Market Truffler that actually exist today.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Status</CardTitle>
        </CardHeader>
        <CardContent className="divide-y divide-border">
          {rows.map((row) => (
            <div key={row.label} className="flex items-center justify-between py-3 text-sm">
              <span className="text-muted-foreground">{row.label}</span>
              <span className="font-medium">{row.value}</span>
            </div>
          ))}
        </CardContent>
      </Card>
    </div>
  );
}
