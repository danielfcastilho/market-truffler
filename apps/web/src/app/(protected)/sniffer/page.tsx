import { RankingBoard } from "@/components/ranking-board";
import {
  ComparisonSummary,
  HistoricalCoinPanel,
  HistoryEmpty,
  HistoryNavigator,
  RankingModeSwitch,
} from "@/components/ranking-history";
import { getLiveRankings, getRankingHistory, getRankingSnapshot } from "@/lib/server-api";

export default async function SnifferRankingsPage({
  searchParams,
}: {
  searchParams: Promise<{ direction?: string; view?: string; snapshot?: string; symbol?: string }>;
}) {
  const { direction, view, snapshot, symbol } = await searchParams;
  const initialDirection = direction === "short" ? "short" : "long";

  if (view !== "history") {
    const result = await getLiveRankings();
    return (
      <div className="space-y-4">
        <RankingModeSwitch mode="live" />
        <RankingBoard result={result} initialDirection={initialDirection} />
      </div>
    );
  }

  // History: only persisted published snapshots, never the live endpoint.
  const page = await getRankingHistory();
  const selected =
    snapshot && /^\d+$/.test(snapshot)
      ? Number(snapshot)
      : page.status === "ok"
        ? page.data.items[0]?.id
        : undefined;
  const detail = selected !== undefined ? await getRankingSnapshot(selected) : null;
  return (
    <div className="space-y-4">
      <RankingModeSwitch mode="history" />
      {page.status === "error" || detail?.status === "error" ? (
        <div role="alert" className="max-w-xl rounded-lg border border-destructive/50 p-4 text-sm">
          <p className="font-medium text-destructive">Ranking history could not be loaded.</p>
          <p className="font-mono text-xs text-muted-foreground">
            {page.status === "error" ? page.message : detail?.status === "error" ? detail.message : ""}
          </p>
        </div>
      ) : detail === null ? (
        <HistoryEmpty />
      ) : detail.status === "not_found" ? (
        <p className="text-sm text-muted-foreground">No published snapshot has that id.</p>
      ) : (
        <>
          <HistoryNavigator detail={detail.data} page={page.status === "ok" ? page.data : null} />
          {symbol && <HistoricalCoinPanel detail={detail.data} symbol={symbol.toUpperCase()} />}
          <ComparisonSummary comparison={detail.data.comparison} />
          <RankingBoard
            result={{ status: "ok", data: detail.data.ranking }}
            initialDirection={initialDirection}
            history={{
              snapshotId: detail.data.snapshot.id,
              publishedAt: detail.data.snapshot.published_at,
              comparison: detail.data.comparison,
            }}
          />
        </>
      )}
    </div>
  );
}
