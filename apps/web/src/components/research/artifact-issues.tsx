import type { ArtifactIssue } from "@/lib/research";

/** Research artifacts the API could not read — shown, never hidden. */
export function ArtifactIssues({ issues }: { issues: ArtifactIssue[] }) {
  if (issues.length === 0) return null;
  return (
    <div role="alert" className="rounded-lg border border-destructive/50 p-3 text-sm">
      <p className="font-medium text-destructive">
        {issues.length} research artifact{issues.length === 1 ? "" : "s"} could not be read and
        {issues.length === 1 ? " is" : " are"} excluded below
      </p>
      <ul className="mt-2 space-y-0.5 font-mono text-xs">
        {issues.map((issue) => (
          <li key={`${issue.artifact}:${issue.problem}`}>
            {issue.artifact}: {issue.problem}
          </li>
        ))}
      </ul>
    </div>
  );
}
