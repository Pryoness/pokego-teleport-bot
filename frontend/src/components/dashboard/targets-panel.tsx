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
  const toggleTargetOnly = useHuntStore((s) => s.toggleTargetOnly);
  const toggleSkip = useHuntStore((s) => s.toggleSkip);
  const skippedCount = targets.filter((t) => t.skip).length;
  const huntCount = targets.length - skippedCount;
  const [value, setValue] = useState("");
  const [mode, setMode] = useState<"low" | "high" | "skip">("low");
  const last = value.split(",").map((s) => s.trim().toLowerCase()).filter(Boolean).at(-1) ?? "";
  const suggestions = useMemo(() => (last ? suggestPokemon(last, 7) : []), [last]);

  const commit = () => {
    const names = value.split(",").map((s) => s.trim()).filter(Boolean);
    if (!names.length) return;
    const { added, failed } = addTargets(names, mode === "high" ? 0 : 1);
    if (failed.length) toast(`Unknown: ${failed.join(", ")}`);
    if (mode === "skip") {
      // Skip mode: also mark already-listed names as skipped
      const listed = new Set(targets.filter((t) => !t.skip).map((t) => t.name));
      const toSkip = [...added, ...names.map((n) => n.toLowerCase()).filter((n) => listed.has(n))];
      [...new Set(toSkip)].forEach((n) => void toggleSkip(n));
    }
    if (added.length || mode === "skip") setValue("");
  };

  return (
    <Panel
      title={`Hunt list · ${huntCount}${skippedCount ? ` · ${skippedCount} skipped` : ""}`}
      className={className}
      allowOverflow
    >
      {targets.length > 0 && huntCount === 0 ? (
        <p className="shrink-0 pb-1 text-[11px] text-muted">All listed Pokémon are skipped — hunting every other Pokémon.</p>
      ) : null}
      {targets.length === 0 ? (
        <p className="py-4 text-center text-sm text-muted">No targets — hunting every Pokémon. Add species below.</p>
      ) : (
        <ul className="min-h-0 flex-1 max-h-96 space-y-1 overflow-y-auto overflow-x-hidden pr-1 xl:max-h-none">
          {targets.map((t) => (
            <li key={t.name} className="flex items-center gap-2 rounded-sm px-1 py-1">
              <Sprite name={t.name} size={28} />
              <span className={`flex-1 truncate text-sm ${t.skip ? "text-muted line-through" : ""}`}>{titleCasePokemon(t.name)}</span>
              <button
                type="button"
                onClick={readOnly ? undefined : () => togglePriority(t.name)}
                disabled={readOnly}
                title={readOnly ? "Read-only mode" : `Click to set to ${t.priority === 0 ? "Low" : "High"} priority`}
                className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide ${t.priority === 0 ? "bg-danger/15 text-danger" : "bg-accent/15 text-accent"} ${readOnly ? "opacity-50 cursor-not-allowed" : "hover:opacity-70"}`}
              >
                {t.priority === 0 ? "High" : "Low"}
              </button>
              <button
                type="button"
                onClick={readOnly ? undefined : () => toggleTargetOnly(t.name)}
                disabled={readOnly}
                title={readOnly ? "Read-only mode" : t.targetOnly ? "Solo mode active — click to disable" : "Click to enable solo mode"}
                className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide ${t.targetOnly ? "bg-warn/15 text-warn" : "bg-elevated text-muted opacity-40"} ${readOnly ? "cursor-not-allowed" : "hover:opacity-100"}`}
              >
                Solo
              </button>
              <button
                type="button"
                onClick={readOnly ? undefined : () => toggleSkip(t.name)}
                disabled={readOnly}
                title={readOnly ? "Read-only mode" : t.skip ? "Skipped — click to hunt again" : "Click to skip (ignore) this Pokémon"}
                className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide ${t.skip ? "bg-danger/15 text-danger" : "bg-elevated text-muted opacity-40"} ${readOnly ? "cursor-not-allowed" : "hover:opacity-100"}`}
              >
                Skip
              </button>
              {readOnly ? null : (
                <button type="button" className="grid place-items-center text-muted hover:text-danger" onClick={() => removeTarget(t.name)}>
                  <X className="size-3.5" />
                </button>
              )}
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
            <Button
              size="md"
              variant={mode === "high" ? "danger" : "secondary"}
              onClick={() => setMode((m) => (m === "low" ? "high" : m === "high" ? "skip" : "low"))}
              title="Low → High → Skip"
              className={`shrink-0 px-2 text-xs ${mode === "skip" ? "text-danger line-through" : ""}`}
            >
              {mode === "high" ? "High" : mode === "skip" ? "Skip" : "Low"}
            </Button>
            <Button size="md" variant="primary" onClick={commit} disabled={!value.trim()}>
              Add
            </Button>
          </div>
          {suggestions.length > 0 ? (
            <ul className="absolute z-20 bottom-full mb-1 w-full overflow-hidden rounded-sm bg-elevated shadow-[var(--shadow-border)]">
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
