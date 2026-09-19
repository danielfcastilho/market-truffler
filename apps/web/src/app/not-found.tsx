import Link from "next/link";
import { Button } from "@/components/ui/button";

export default function NotFound() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-4 px-4 text-center">
      <span className="text-4xl leading-none">🐷❓</span>
      <h1 className="text-lg font-semibold tracking-tight">Page not found</h1>
      <p className="text-sm text-muted-foreground">
        Market Truffler doesn&apos;t have anything at this address.
      </p>
      <Button render={<Link href="/">Back home</Link>} />
    </div>
  );
}
