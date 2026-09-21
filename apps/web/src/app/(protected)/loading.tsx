import { Skeleton } from "@/components/ui/skeleton";
import { PageContainer } from "@/components/page-container";

export default function ProtectedLoading() {
  return (
    <PageContainer className="max-w-4xl space-y-4">
      <Skeleton className="h-8 w-64" />
      <Skeleton className="h-4 w-full max-w-xl" />
      <div className="grid gap-4 pt-4 sm:grid-cols-2">
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-32 w-full" />
      </div>
    </PageContainer>
  );
}
