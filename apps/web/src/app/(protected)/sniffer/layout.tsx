import { SnifferNav } from "@/components/sniffer-nav";
import { PageContainer } from "@/components/page-container";

/**
 * Shared shell for every Sniffer page: the 🍄 Truffles dashboard
 * (`/sniffer` itself — Sniffer's primary page, not a separate
 * "Dashboard"), the Sniffs matrix (`/sniffer/sniffs`), and per-symbol
 * detail pages (`/sniffer/{symbol}`). Uses the canonical PageContainer
 * with no extra width class — no centering, no max-width — so the title
 * and nav stay pixel-identical across every page; each page's own content
 * area varies in width beneath it. This is the site's canonical content
 * alignment: every other top-level page follows the same PageContainer.
 */
export default function SnifferLayout({ children }: { children: React.ReactNode }) {
  return (
    <PageContainer>
      <h1 className="mb-8 text-2xl font-semibold tracking-tight">🐽 Sniffer</h1>
      <SnifferNav />
      {children}
    </PageContainer>
  );
}
