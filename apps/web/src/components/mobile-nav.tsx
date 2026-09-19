"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { NAV_ITEMS } from "@/lib/navigation";

export function MobileNav() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);

  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger
        render={
          <Button variant="ghost" size="icon" className="md:hidden" aria-label="Open navigation">
            <span className="text-lg">🐷</span>
          </Button>
        }
      />
      <DropdownMenuContent align="start" className="w-48">
        {NAV_ITEMS.map((item) => {
          const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
          return (
            <DropdownMenuItem
              key={item.href}
              className={active ? "bg-accent" : undefined}
              render={
                <Link href={item.href} onClick={() => setOpen(false)}>
                  <span className="mr-2">{item.icon}</span>
                  {item.label}
                </Link>
              }
            />
          );
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
