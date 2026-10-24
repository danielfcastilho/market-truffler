import { RankingBoard } from "@/components/ranking-board";
import { getLiveRankings } from "@/lib/server-api";

export default async function SnifferRankingsPage({
  searchParams,
}: {
  searchParams: Promise<{ direction?: string }>;
}) {
  const { direction } = await searchParams;
  const result = await getLiveRankings();
  return (
    <RankingBoard
      result={result}
      initialDirection={direction === "short" ? "short" : "long"}
    />
  );
}
