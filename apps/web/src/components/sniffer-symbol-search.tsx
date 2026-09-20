"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";

/**
 * Navigates straight to `/sniffer/{symbol}` for any symbol the user types
 * — no autocomplete against the live universe (kept simple), and no
 * validation here: an unknown/mistyped symbol is handled cleanly by the
 * detail page itself, not by this control.
 */
export function SnifferSymbolSearch() {
  const router = useRouter();
  const [value, setValue] = useState("");

  function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    const symbol = value.trim().toUpperCase();
    if (!symbol) return;
    router.push(`/sniffer/${encodeURIComponent(symbol)}`);
  }

  return (
    <form onSubmit={handleSubmit} className="flex gap-2">
      <Input
        value={value}
        onChange={(event) => setValue(event.target.value)}
        placeholder="Inspect symbol…"
        aria-label="Inspect symbol"
        className="max-w-48"
      />
      <Button type="submit" variant="secondary">
        Go
      </Button>
    </form>
  );
}
