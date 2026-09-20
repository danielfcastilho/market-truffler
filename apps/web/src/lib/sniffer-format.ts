/** Shared Sniffer frame-time display: "HH:MM UTC", used anywhere a page
 * shows "what did Sniffer last see" (the dashboard, the Sniffs matrix). */
export function formatFrameTimeUtc(iso: string): string {
  const date = new Date(iso);
  const hh = String(date.getUTCHours()).padStart(2, "0");
  const mm = String(date.getUTCMinutes()).padStart(2, "0");
  return `${hh}:${mm} UTC`;
}
