import { Fragment } from "react";

/**
 * Renders a section of a campaign's Markdown report — the authoritative,
 * human-written interpretation — without a Markdown dependency. Supports
 * exactly what reports use: paragraphs, `-` lists, `|` tables, fenced code,
 * inline `code` and **bold**. Links render as their text. Everything is
 * React-escaped; no HTML from the file is ever injected.
 */
export function ReportText({ markdown }: { markdown: string }) {
  const blocks: React.ReactNode[] = [];
  const lines = markdown.split("\n");
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (line.trim() === "") {
      i++;
    } else if (line.startsWith("```")) {
      const code: string[] = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) code.push(lines[i++]);
      i++;
      blocks.push(
        <pre key={i} className="overflow-x-auto rounded-md bg-muted p-3 font-mono text-xs">
          {code.join("\n")}
        </pre>,
      );
    } else if (line.trimStart().startsWith("|")) {
      const rows: string[][] = [];
      while (i < lines.length && lines[i].trimStart().startsWith("|")) {
        const cells = lines[i].trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
        if (!cells.every((c) => /^:?-+:?$/.test(c))) rows.push(cells);
        i++;
      }
      const [head, ...body] = rows;
      if (!head) continue;
      blocks.push(
        <div key={i} className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="text-muted-foreground">
              <tr>
                {head.map((cell, c) => (
                  <th key={c} className="border-b border-border py-1.5 pr-3 font-medium">
                    {inline(cell)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-border/60">
              {body.map((row, r) => (
                <tr key={r}>
                  {row.map((cell, c) => (
                    <td key={c} className="py-1.5 pr-3 align-top">
                      {inline(cell)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
    } else if (/^\s*- /.test(line)) {
      const items: string[] = [];
      while (i < lines.length && (/^\s*- /.test(lines[i]) || /^\s{2,}\S/.test(lines[i]))) {
        if (/^\s*- /.test(lines[i])) items.push(lines[i].replace(/^\s*- /, ""));
        else items[items.length - 1] += ` ${lines[i].trim()}`;
        i++;
      }
      blocks.push(
        <ul key={i} className="list-disc space-y-1 pl-5">
          {items.map((item, n) => (
            <li key={n}>{inline(item)}</li>
          ))}
        </ul>,
      );
    } else {
      const paragraph: string[] = [];
      while (
        i < lines.length &&
        lines[i].trim() !== "" &&
        !/^\s*(- |\||```)/.test(lines[i]) &&
        !lines[i].startsWith("#")
      ) {
        paragraph.push(lines[i++].trim());
      }
      if (paragraph.length === 0) {
        // A heading line inside a section: show its text, de-emphasized.
        blocks.push(
          <p key={i} className="font-medium">
            {inline(lines[i++].replace(/^#+\s*/, ""))}
          </p>,
        );
      } else {
        blocks.push(<p key={i}>{inline(paragraph.join(" "))}</p>);
      }
    }
  }
  return <div className="space-y-3 text-sm leading-relaxed">{blocks}</div>;
}

function inline(text: string): React.ReactNode {
  const withoutLinks = text.replace(/\[([^\]]+)\]\([^)]*\)/g, "$1");
  return withoutLinks.split(/(`[^`]+`|\*\*[^*]+\*\*)/g).map((part, n) => {
    if (part.startsWith("`") && part.endsWith("`") && part.length > 1) {
      return (
        <code key={n} className="rounded bg-muted px-1 py-0.5 font-mono text-[0.85em] break-all">
          {part.slice(1, -1)}
        </code>
      );
    }
    if (part.startsWith("**") && part.endsWith("**") && part.length > 3) {
      return <strong key={n}>{part.slice(2, -2)}</strong>;
    }
    return <Fragment key={n}>{part}</Fragment>;
  });
}
