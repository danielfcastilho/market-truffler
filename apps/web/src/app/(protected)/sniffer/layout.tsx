import { PageContainer } from "@/components/page-container";
import { SnifferNav } from "@/components/sniffer-nav";

/**
 * Shared shell for Sniffer: experimental Long/Short rankings (`/sniffer`,
 * the default), the Market Data matrix (`/sniffer/market-data`) and
 * per-symbol detail (`/sniffer/{symbol}`). OINK CORP develops and evaluates
 * ranking models; Sniffer only applies the configured ones to the latest
 * frame — the human decides.
 */
export default function SnifferLayout({ children }: { children: React.ReactNode }) {
  return (
    <PageContainer>
      <h1 className="mb-6 text-2xl font-semibold tracking-tight">🐽 Sniffer</h1>
      <SnifferNav />
      {children}
    </PageContainer>
  );
}
