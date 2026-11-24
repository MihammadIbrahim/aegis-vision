import { Field } from "../../../components/ui";
import { useAsync } from "../../../hooks/useAsync";
import { incidents } from "../../incidents/api";

export const MAX_TAGS = 10;
export const MAX_TAG_LENGTH = 40;

const TAG_PATTERN = /^[\p{L}\p{N}_][\p{L}\p{N}_ .\-]*$/u;

/** Comma-separated text to tags, as the server cleans them: spaces collapsed, duplicates (ignoring capitals) dropped. */
export function parseTags(text: string): string[] {
  const tags: string[] = [];
  const seen = new Set<string>();
  for (const raw of text.split(",")) {
    const tag = raw.split(/\s+/).filter(Boolean).join(" ");
    if (tag && !seen.has(tag.toLowerCase())) {
      seen.add(tag.toLowerCase());
      tags.push(tag);
    }
  }
  return tags;
}

/** Why the tags cannot be saved, or null. */
export function tagsProblem(text: string): string | null {
  const tags = parseTags(text);
  if (tags.length > MAX_TAGS) return `At most ${MAX_TAGS} tags.`;
  const bad = tags.find((t) => t.length > MAX_TAG_LENGTH || !TAG_PATTERN.test(t));
  return bad ? `"${bad}": a tag is 1 to ${MAX_TAG_LENGTH} letters, digits, spaces, dots or dashes, starting with a letter or digit.` : null;
}

/** Labels for an evidence item, typed as comma-separated words (Evidence Vault, D-024). */
export function TagsField({ value, onChange }: { value: string; onChange: (text: string) => void }) {
  const problem = tagsProblem(value);
  return (
    <Field label="Tags" hint={`Comma-separated, up to ${MAX_TAGS} (${parseTags(value).length} now)`} error={problem ?? undefined}>
      <input value={value} maxLength={600} placeholder="e.g. gate 3, night shift" onChange={(e) => onChange(e.target.value)} />
    </Field>
  );
}

/** One of the campus's open cases (incidents), or none. */
export function CaseSelect({ value, onChange, label = "Link to case" }: { value: string; onChange: (id: string) => void; label?: string }) {
  const open = useAsync(() => incidents.list({ status: ["OPEN", "UNDER_INVESTIGATION", "RESPONDING"], limit: 100, offset: 0 }), []);
  return (
    <Field label={label} hint="Only open cases are listed.">
      <select value={value} onChange={(e) => onChange(e.target.value)} disabled={!open.data}>
        <option value="">No case</option>
        {(open.data?.items ?? []).map((i) => (
          <option key={i.id} value={i.id}>
            {i.reference} · {i.title}
          </option>
        ))}
      </select>
    </Field>
  );
}
