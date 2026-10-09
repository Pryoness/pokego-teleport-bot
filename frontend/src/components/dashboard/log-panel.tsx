import { Panel } from "@/components/ui/panel";
import { useHuntStore } from "@/lib/pokego/store";
import { formatClock } from "@/lib/utils";
import { cn } from "@/lib/utils";
import type { EventType } from "@/lib/pokego/types";

const TONE: Record<EventType, string> = {
  queue: "text-accent",
  feed: "text-accent",
  teleport: "text-accent",
  walk: "text-muted",
  catch: "text-success",
  fled: "text-danger",
  expired: "text-subtle",
  encounter: "text-fg",
  cooldown: "text-warn",
  skip: "text-warn",
  system: "text-muted",
};

export function LogPanel({ className }: { className?: string }) {
  const events = useHuntStore((s) => s.events);
  const rows = [...events].reverse().slice(0, 60);

  return (
    <Panel title="Operator log" className={className}>
      <ul className="min-h-0 flex-1 max-h-56 space-y-0.5 overflow-y-auto overflow-x-hidden pr-1 font-mono text-[11px] leading-5 xl:max-h-none">
        {rows.length === 0 ? (
          <li className="text-muted">No events yet.</li>
        ) : (
          rows.map((e) => (
            <li key={e.id} className="flex gap-2">
              <span className="shrink-0 text-subtle">{formatClock(e.timestamp)}</span>
              <span className={cn("shrink-0 uppercase", TONE[e.type])}>[{e.type}]</span>
              <span className="text-fg/90">{e.message}</span>
            </li>
          ))
        )}
      </ul>
    </Panel>
  );
}
