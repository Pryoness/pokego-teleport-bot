import { Copy, X } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Panel } from "@/components/ui/panel";
import { useHuntStore } from "@/lib/pokego/store";
import { titleCasePokemon } from "@/lib/pokego/pokemon";
import { formatDuration } from "@/lib/utils";
import { Sprite } from "./sprite";
import { useNow } from "./use-now";

export function QueuePanel({ className, readOnly = false }: { className?: string; readOnly?: boolean }) {
  const rankedQueue = useHuntStore((s) => s.rankedQueue);
  const clearQueue = useHuntStore((s) => s.clearQueue);
  const removeFromQueue = useHuntStore((s) => s.removeFromQueue);
  const currentId = useHuntStore((s) => s.hunter.currentTaskId);
  const now = useNow(1000);
  const items = rankedQueue(now);

  return (
    <Panel
      title={`Queue · ${items.length}`}
      action={
        readOnly ? undefined : (
          <Button size="sm" variant="ghost" onClick={clearQueue} disabled={!items.length}>
            Clear
          </Button>
        )
      }
      className={className}
    >
      {items.length === 0 ? (
        <p className="py-6 text-center text-sm text-muted">Queue is empty. Paste a feed or pin the map.</p>
      ) : (
        <ul className="min-h-0 flex-1 max-h-[24rem] space-y-1 overflow-y-auto overflow-x-hidden pr-1 xl:max-h-none">
          {items.map((item) => {
            const rem = Math.max(0, Math.floor((item.expiresAt - now) / 1000));
            const cd = item.cooldown.active ? item.cooldown.remainingSeconds ?? 0 : 0;
            return (
              <li
                key={item.id}
                className="flex items-center gap-2 rounded-sm px-1.5 py-1.5 hover:bg-elevated"
                data-active={item.id === currentId ? "true" : undefined}
              >
                <Sprite name={item.pokemon} size={28} />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5">
                    <span className="truncate text-sm">{titleCasePokemon(item.pokemon)}</span>
                    {item.priority === 0 ? <Badge tone="danger">High</Badge> : null}
                    {item.id === currentId ? <Badge tone="accent">Live</Badge> : null}
                  </div>
                  <div className="flex flex-wrap gap-x-2 font-mono text-[10px] text-muted">
                    <span>DSP {formatDuration(rem)}</span>
                    {item.ivInfo ? <span>{item.ivInfo}</span> : null}
                    {item.distanceKm != null ? <span>{item.distanceKm} km</span> : null}
                    {cd > 0 ? <span className="text-warn">CD {formatDuration(cd)}</span> : <span className="text-success">Ready</span>}
                  </div>
                </div>
                {readOnly ? null : (
                  <button
                    type="button"
                    className="grid size-8 place-items-center text-muted hover:text-fg"
                    aria-label="Copy coordinates"
                    onClick={async () => {
                      if (!item.coords) return;
                      await navigator.clipboard.writeText(item.coords);
                      toast("Copied " + item.coords);
                    }}
                  >
                    <Copy className="size-3.5" />
                  </button>
                )}
                {readOnly ? null : (
                  <button
                    type="button"
                    className="grid size-8 place-items-center text-muted hover:text-danger"
                    aria-label="Remove"
                    onClick={() => removeFromQueue(item.id)}
                  >
                    <X className="size-3.5" />
                  </button>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}
