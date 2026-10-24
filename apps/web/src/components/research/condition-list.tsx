import { describeCondition, technicalCondition, type Condition } from "@/lib/research";

/** Selection conditions in words, each with its exact technical definition. */
export function ConditionList({ conditions }: { conditions: Condition[] }) {
  return (
    <ul className="space-y-1.5 text-sm">
      {conditions.map((condition) => (
        <li key={technicalCondition(condition)}>
          {describeCondition(condition)}
          <code className="ml-2 font-mono text-xs text-muted-foreground">
            {technicalCondition(condition)}
          </code>
        </li>
      ))}
    </ul>
  );
}
