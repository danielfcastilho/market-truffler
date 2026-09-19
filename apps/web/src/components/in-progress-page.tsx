import { Badge } from "@/components/ui/badge";

export function InProgressPage({
  icon,
  name,
  tagline,
  message,
}: {
  icon: string;
  name: string;
  tagline: string;
  message: string;
}) {
  return (
    <div className="flex min-h-[calc(100vh-3.5rem)] items-center justify-center px-4 py-16">
      <div className="flex max-w-md flex-col items-center gap-4 text-center">
        <span className="text-5xl leading-none">{icon}</span>
        <h1 className="text-2xl font-semibold tracking-tight">{name}</h1>
        <p className="text-muted-foreground">{tagline}</p>
        <Badge variant="secondary" className="mt-1 tracking-wide text-primary">
          IN PROGRESS
        </Badge>
        <p className="text-sm text-muted-foreground/80">{message}</p>
      </div>
    </div>
  );
}
