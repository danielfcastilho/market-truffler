import { ModuleCard } from "@/components/module-card";
import { PageContainer } from "@/components/page-container";

export default function HomePage() {
  return (
    <PageContainer className="max-w-4xl">
      <div className="mb-10 space-y-3">
        <h1 className="text-2xl font-semibold tracking-tight md:text-3xl">
          🐷🍄 Market Truffler
        </h1>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <ModuleCard
          href="/sniffer"
          icon="🐽"
          title="Sniffer"
          description="Senses the market: data, history, and measurable features."
        />
        <ModuleCard
          href="/warhog"
          icon="🐗"
          title="Warhog"
          description="Acts: entries, position management, and exits."
        />
        <ModuleCard
          href="/oink-corp"
          icon="🧬"
          title="OINK CORP"
          description="Thinks: research, and Long/Short rankings — the truffles."
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
