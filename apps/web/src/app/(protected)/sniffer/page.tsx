import { getLatestSnifferFrame } from "@/lib/server-api";
import { resolveSnifferView } from "@/lib/sniffer-view";
import { SnifferNav } from "@/components/sniffer-nav";
import { SnifferFeatures } from "@/components/sniffer-features";
import { SnifferNotes } from "@/components/sniffer-notes";
import { SnifferTruffles } from "@/components/sniffer-truffles";

export default async function SnifferPage({
  searchParams,
}: {
  searchParams: Promise<{ view?: string | string[] }>;
}) {
  const params = await searchParams;
  const view = resolveSnifferView(params.view);
  const frame = view === "features" ? await getLatestSnifferFrame() : null;

  return (
    <div className="mx-auto max-w-2xl px-4 py-10 md:px-8 md:py-14">
      <h1 className="mb-8 text-2xl font-semibold tracking-tight">🐽 Sniffer</h1>

      <SnifferNav view={view} />

      {view === "truffles" ? (
        <SnifferTruffles />
      ) : view === "notes" ? (
        <SnifferNotes />
      ) : (
        <SnifferFeatures frame={frame} />
      )}
    </div>
  );
}
