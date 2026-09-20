import { getLatestSnifferFrame } from "@/lib/server-api";
import { SnifferSniffs } from "@/components/sniffer-sniffs";

/**
 * Sniffs gets its own route (rather than a `?view=` case on `/sniffer`)
 * because it's a growing analytical data matrix, not prose — it needs the
 * full viewport width, while the Truffles dashboard stays at the shared
 * layout's narrower content width. The shared shell (sniffer/layout.tsx)
 * stays pixel-identical across every tab; only this page's content is
 * unconstrained.
 */
export default async function SnifferSniffsPage() {
  const frame = await getLatestSnifferFrame();
  return <SnifferSniffs frame={frame} />;
}
