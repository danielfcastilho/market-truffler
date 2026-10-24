import { getLatestSnifferFrame } from "@/lib/server-api";
import { SnifferSniffs } from "@/components/sniffer-sniffs";

export default async function SnifferPage() {
  const frame = await getLatestSnifferFrame();
  return <SnifferSniffs frame={frame} />;
}
