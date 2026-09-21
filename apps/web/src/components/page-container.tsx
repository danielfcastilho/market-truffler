import { cn } from "@/lib/utils";

/**
 * Canonical outer content shell for every top-level page: fixed
 * left-aligned padding only (no `mx-auto`/`max-width`), so the content's
 * left edge stays anchored at the same position next to the sidebar
 * regardless of viewport width or how wide an individual page's own
 * content is. Pages control their own content width via `className`
 * (e.g. `max-w-2xl`) — that width is allowed to vary page to page, but
 * the left origin must not.
 */
export function PageContainer({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return <div className={cn("px-4 py-10 md:px-8 md:py-14", className)}>{children}</div>;
}
