import { redirect } from "next/navigation";
import { getCurrentUser } from "@/lib/server-api";
import { AppSidebar } from "@/components/app-sidebar";
import { MobileNav } from "@/components/mobile-nav";
import { UserMenu } from "@/components/user-menu";

export default async function ProtectedLayout({ children }: { children: React.ReactNode }) {
  const user = await getCurrentUser();

  if (!user) {
    redirect("/login");
  }

  return (
    <div className="flex min-h-screen w-full">
      <AppSidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center justify-between border-b border-border px-4 md:px-6">
          <div className="flex items-center gap-2">
            <MobileNav />
            <span className="text-sm font-medium text-foreground/70 md:hidden">
              Market Truffler
            </span>
          </div>
          <UserMenu email={user.email} />
        </header>
        <main className="flex-1 bg-background">{children}</main>
      </div>
    </div>
  );
}
