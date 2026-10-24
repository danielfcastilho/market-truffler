import { ModuleCard } from "@/components/module-card";
import { PageContainer } from "@/components/page-container";

export default function HomePage() {
  return (
    <PageContainer className="max-w-4xl">
      <div className="mb-10 space-y-3">
        <h1 className="text-2xl font-semibold tracking-tight md:text-3xl">
          🐷🍄 Market Truffler
        </h1>
        <p className="text-muted-foreground">
          Sniffer senses. OINK CORP learns and discovers. The human decides.
        </p>
      </div>

      <div className="grid gap-4 sm:grid-cols-3">
        <ModuleCard
          href="/sniffer"
          icon="🐽"
          title="Sniffer"
          description="Senses the market: experimental Long/Short rankings, market data and measurable features."
        />
        <ModuleCard
          href="/oink-corp"
          icon="🧬"
          title="OINK CORP"
          description="Learns and discovers: market research and the ranking models behind Sniffer's rankings."
        />
        <ModuleCard
          href="/vitals"
          icon="🩺"
          title="Vitals"
          description="Is Market Truffler healthy and operating correctly?"
        />
      </div>
    </PageContainer>
  );
}
