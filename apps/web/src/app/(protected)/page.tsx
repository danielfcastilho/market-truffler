import { ModuleCard } from "@/components/module-card";

export default function HomePage() {
  return (
    <div className="mx-auto max-w-4xl px-4 py-10 md:px-8 md:py-14">
      <div className="mb-10 space-y-3">
        <h1 className="text-2xl font-semibold tracking-tight md:text-3xl">
          🐷🍄 Market Truffler
        </h1>
        <p className="max-w-2xl text-muted-foreground">
          A foundation for a systematic crypto trading system. Sniffer gathers market data,
          Truffler turns it into opportunity intelligence, and Warhog acts on it — with OINK
          CORP developing and validating the strategy behind the scenes.
        </p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <ModuleCard
          href="/sniffer"
          icon="🐽"
          title="Sniffer — Data"
          description="Exchange connectivity and market-data acquisition."
        />
        <ModuleCard
          href="/truffler"
          icon="🍄"
          title="Truffler — Analysis"
          description="Turns market data into opportunity intelligence."
        />
        <ModuleCard
          href="/warhog"
          icon="🐗"
          title="Warhog — Trading"
          description="Entries, position management, and exits."
        />
        <ModuleCard
          href="/oink-corp"
          icon="🧬"
          title="OINK CORP — Research"
          description="Develops and validates the strategy."
        />
      </div>
    </div>
  );
}
