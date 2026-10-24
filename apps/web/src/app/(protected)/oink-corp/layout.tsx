import Link from "next/link";

import { PageContainer } from "@/components/page-container";
import { ResearchNav } from "@/components/research/research-nav";

/**
 * Shared shell for OINK CORP's read-only Research Explorer: the overview
 * (`/oink-corp`), campaigns and experiments. It shows recorded research
 * only — no ranking, no Truffles, nothing executed from the UI.
 */
export default function OinkCorpLayout({ children }: { children: React.ReactNode }) {
  return (
    <PageContainer>
      <h1 className="mb-2 text-2xl font-semibold tracking-tight">
        <Link href="/oink-corp">🧬 OINK CORP</Link>{" "}
        <span className="text-muted-foreground">/ Research</span>
      </h1>
      <p className="mb-6 max-w-2xl text-sm text-muted-foreground">
        What OINK CORP is investigating, what it has tested, and what the evidence does — and does
        not — support.
      </p>
      <ResearchNav />
      {children}
    </PageContainer>
  );
}
