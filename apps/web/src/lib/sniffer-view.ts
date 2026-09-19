export type SnifferView = "features" | "notes" | "truffles";

/**
 * Resolves Sniffer's URL-addressable `?view=` param into a known view.
 * Anything other than exactly "features" or "notes" (missing, "truffles",
 * or unrecognized) resolves to "truffles" — Sniffer's primary operational
 * surface and the default view.
 */
export function resolveSnifferView(view: string | string[] | undefined): SnifferView {
  const value = Array.isArray(view) ? view[0] : view;
  if (value === "features") return "features";
  if (value === "notes") return "notes";
  return "truffles";
}
