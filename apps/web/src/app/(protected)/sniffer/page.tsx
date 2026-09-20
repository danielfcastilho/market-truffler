import { getLatestSnifferFrame } from "@/lib/server-api";
import { SnifferDashboard } from "@/components/sniffer-dashboard";

export default async function SnifferPage() {
  const frame = await getLatestSnifferFrame();
  return <SnifferDashboard frame={frame} />;
}
