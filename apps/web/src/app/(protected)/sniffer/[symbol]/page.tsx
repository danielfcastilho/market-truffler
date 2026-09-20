import { getLatestSnifferFrame } from "@/lib/server-api";
import { SnifferSymbolDetail } from "@/components/sniffer-symbol-detail";

export default async function SnifferSymbolPage({
  params,
}: {
  params: Promise<{ symbol: string }>;
}) {
  const { symbol } = await params;
  const frame = await getLatestSnifferFrame();
  return <SnifferSymbolDetail symbol={symbol.toUpperCase()} frame={frame} />;
}
