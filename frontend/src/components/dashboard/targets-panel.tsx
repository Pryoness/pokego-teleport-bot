import { useMemo, useState } from "react";
import { X } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Panel } from "@/components/ui/panel";
import { isValidPokemon, suggestPokemon, titleCasePokemon } from "@/lib/pokego/pokemon";
import { useHuntStore } from "@/lib/pokego/store";
import { Sprite } from "./sprite";

export function TargetsPanel({ className, readOnly = false }: { className?: string; readOnly?: boolean }) {
  const targets = useHuntStore((s) => s.targets);
  const addTargets = useHuntStore((s) => s.addTargets);
  const removeTarget = useHuntStore((s) => s.removeTarget);
  const togglePriority = useHuntStore((s) => s.togglePriority);
  const [value, setValue] = useState("");
  const [high, setHigh] = useState(false);
  const last = value.split(",").map((s) => s.trim().toLowerCase()).filter(Boolean).at(-1) ?? "";
  const suggestions = useMemo(() => (last ? suggestPokemon(last, 7) : []), [last]);

  const commit = () => {
    const names = value.split(",").map((s) => s.trim()).filter(Boolean);
    if (!names.length) return;
    const { added, failed } = addTargets(names, high ? 0 : 1);
    if (failed.length) toast(`Unknown: ${failed.join(", ")}`);
    if (added.length) setValue("");
  };

  return (
    <Panel title={`Hunt list · ${targets.length}`} className={className}>
      {targets.length === 0 ? (
        <p className="py-4 text-center text-sm text-muted">No targets. Add species below.</p>
      ) : (
        <ul className="min-h-0 flex-1 max-h-96 space-y-1 overflow-y-auto overflow-x-hidden pr-1 xl:max-h-none">
          {targets.map((t) => (
            <li key={t.name} className="flex items-center gap-2 rounded-sm px-1 py-1">
              <Sprite name={t.name} size={28} />
              <span className="flex-1 truncate text-sm">{titleCasePokemon(t.name)}</span>
              <Badge tone={t.priority === 0 ? "danger" : "accent"}>{t.priority === 0 ? "High" : "Low"}</Badge>
              <button
                type="button"
                className="h-8 px-2 text-[11px] text-muted hover:text-fg"
                onClick={() => togglePriority(t.name)}
              >
                Flip
              </button>
              <button type="button" className="grid size-8 place-items-center text-muted hover:text-danger" onClick={() => removeTarget(t.name)}>
                <X className="size-3.5" />
              </button>
            </li>
          ))}
        </ul>
      )}
      {readOnly ? null : (
        <div className="relative shrink-0 pt-2">
          <div className="flex gap-1.5">
            <Input
              value={value}
              placeholder="axew, gible, ralts"
              onChange={(e) => setValue(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") commit();
              }}
              className={last && !isValidPokemon(last) && last.length > 2 ? "ring-1 ring-danger/50" : ""}
            />
            <Button size="md" variant={high ? "danger" : "secondary"} onClick={() => setHigh((h) => !h)} className="shrink-0 px-2 text-xs">
              {high ? "High" : "Low"}
            </Button>
            <Button size="md" variant="primary" onClick={commit} disabled={!value.trim()}>
              Add
            </Button>
          </div>
          {suggestions.length > 0 ? (
            <ul className="absolute z-20 mt-1 w-full overflow-hidden rounded-sm bg-elevated shadow-[var(--shadow-border)]">
              {suggestions.map((n) => (
                <li key={n}>
                  <button
                    type="button"
                    className="flex w-full items-center gap-2 px-2 py-2 text-left text-sm hover:bg-surface"
                    onClick={() => {
                      const parts = value.split(",").map((s) => s.trim()).filter(Boolean);
                      parts[parts.length - 1] = n;
                      setValue(parts.join(", "));
                    }}
                  >
                    <Sprite name={n} size={24} />
                    {titleCasePokemon(n)}
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      )}
    </Panel>
  );
}
