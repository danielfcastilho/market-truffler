import { ModuleCard } from "@/components/module-card";

export default function HomePage() {
  return (
    <div className="mx-auto max-w-4xl px-4 py-10 md:px-8 md:py-14">
      <div className="mb-10 space-y-3">
        <h1 className="text-2xl font-semibold tracking-tight md:text-3xl">
          🐷🍄 Market Truffler
        </h1>
        <p className="max-w-2xl text-muted-foreground">
          A foundation for a systematic crypto trading system. Sniffer digs through market data
          to surface opportunities — truffles — and Warhog acts on them, with OINK CORP
          developing and validating the strategy behind the scenes.
        </p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <ModuleCard
          href="/sniffer"
          icon="🐽"
          title="Sniffer"
          description="Finds and surfaces market opportunities — the truffles."
        />
        <ModuleCard
          href="/warhog"
          icon="🐗"
          title="Warhog"
          description="Entries, position management, and exits."
        />
        <ModuleCard
          href="/oink-corp"
          icon="🧬"
          title="OINK CORP"
          description="Develops and validates the strategy."
        />
        <ModuleCard
          href="/vitals"
          icon="🩺"
          title="Vitals"
          description="Is Market Truffler healthy and operating correctly?"
        />
      </div>
    </div>
  );
}
