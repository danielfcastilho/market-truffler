import { Suspense } from "react";
import { LoginForm } from "@/components/login-form";

export default function LoginPage() {
  return (
    <div className="flex min-h-screen w-full items-center justify-center bg-background px-4">
      <div className="w-full max-w-sm space-y-8">
        <div className="flex flex-col items-center gap-2 text-center">
          <span className="text-4xl leading-none">🐷🍄</span>
          <h1 className="text-xl font-semibold tracking-tight">Market Truffler</h1>
          <p className="text-sm text-muted-foreground">Sign in to continue.</p>
        </div>
        <Suspense fallback={null}>
          <LoginForm />
        </Suspense>
      </div>
    </div>
  );
}
