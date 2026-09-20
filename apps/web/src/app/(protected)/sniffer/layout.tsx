import { SnifferNav } from "@/components/sniffer-nav";

/**
 * Shared shell for every Sniffer page: the 🍄 Truffles dashboard
 * (`/sniffer` itself — Sniffer's primary page, not a separate
 * "Dashboard"), the Sniffs matrix (`/sniffer/sniffs`), and per-symbol
 * detail pages (`/sniffer/{symbol}`). Owns only padding — no centering, no
 * max-width — so the title and nav stay pixel-identical across every
 * page; each page's own content area varies in width beneath it.
 */
export default function SnifferLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="px-4 py-10 md:px-8 md:py-14">
      <h1 className="mb-8 text-2xl font-semibold tracking-tight">🐽 Sniffer</h1>
      <SnifferNav />
      {children}
    </div>
  );
}
