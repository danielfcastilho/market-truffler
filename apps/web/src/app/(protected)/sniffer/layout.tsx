import { PageContainer } from "@/components/page-container";

/**
 * Shared shell for Sniffer's Market Data pages: the Sniffs matrix
 * (`/sniffer`) and per-symbol detail (`/sniffer/{symbol}`). Sniffer only
 * observes and measures the market — Long/Short rankings and Truffles
 * belong to OINK CORP (see docs/ARCHITECTURE.md), so nothing here ranks.
 * Uses the canonical PageContainer with no extra width class, so the
 * title stays pixel-identical across both pages.
 */
export default function SnifferLayout({ children }: { children: React.ReactNode }) {
  return (
    <PageContainer>
      <h1 className="mb-8 text-2xl font-semibold tracking-tight">
        🐽 Sniffer <span className="text-muted-foreground">/ Market Data</span>
      </h1>
      {children}
    </PageContainer>
  );
}
