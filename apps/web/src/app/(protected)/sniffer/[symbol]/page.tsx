import { RankingContext } from "@/components/ranking-context";
import { SnifferSymbolDetail } from "@/components/sniffer-symbol-detail";
import { getLatestSnifferFrame, getLiveRankings } from "@/lib/server-api";

export default async function SnifferSymbolPage({
  params,
  searchParams,
}: {
  params: Promise<{ symbol: string }>;
  searchParams: Promise<{ direction?: string }>;
}) {
  const [{ symbol }, { direction }] = await Promise.all([params, searchParams]);
  const upper = symbol.toUpperCase();
  const [frame, rankings] = await Promise.all([getLatestSnifferFrame(), getLiveRankings()]);
  return (
    <div className="max-w-3xl space-y-10">
      <SnifferSymbolDetail symbol={upper} frame={frame} />
      <RankingContext
        symbol={upper}
        rankings={rankings}
        backDirection={direction === "short" ? "short" : "long"}
      />
    </div>
  );
}
