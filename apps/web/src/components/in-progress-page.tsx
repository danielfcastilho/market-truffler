import { Badge } from "@/components/ui/badge";
import { PageContainer } from "@/components/page-container";

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
    <PageContainer>
      <h1 className="mb-8 text-2xl font-semibold tracking-tight">
        {icon} {name}
      </h1>
      <div className="max-w-md space-y-4">
        <p className="text-muted-foreground">{tagline}</p>
        <Badge variant="secondary" className="tracking-wide text-primary">
          IN PROGRESS
        </Badge>
        <p className="text-sm text-muted-foreground/80">{message}</p>
      </div>
    </PageContainer>
  );
}
