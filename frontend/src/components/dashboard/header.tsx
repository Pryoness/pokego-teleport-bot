import { Play, SkipForward, Square } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { useHuntStore } from "@/lib/pokego/store";
import { cn } from "@/lib/utils";

export function HeaderBar() {
  const running = useHuntStore((s) => s.hunter.running);
  const paused = useHuntStore((s) => s.hunter.paused);
  const start = useHuntStore((s) => s.start);
  const stop = useHuntStore((s) => s.stop);
  const requestSkip = useHuntStore((s) => s.requestSkip);
  const lastCatch = useHuntStore((s) => s.lastCatch);
  const cooldownFor = useHuntStore((s) => s.cooldownFor);
  const currentCoords = useHuntStore((s) => s.hunter.currentCoords);
  const cd = cooldownFor(currentCoords);

  const status = !running
    ? { label: "Stopped", tone: "danger" as const }
    : paused
      ? { label: "Paused", tone: "warn" as const }
      : cd.active
        ? { label: "Cooldown", tone: "warn" as const }
        : { label: "Live", tone: "success" as const };

  return (
    <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-4 py-3 sm:px-5">
      <div className="flex items-center gap-3">
        <div className="grid size-8 place-items-center rounded-sm bg-elevated shadow-[var(--shadow-border)]">
          <span className="size-2 rounded-full bg-accent" />
        </div>
        <div>
          <h1 className="font-medium leading-tight tracking-tight">PokeGo Command</h1>
          <p className="text-xs text-muted">Hunt command center</p>
        </div>
        <Badge tone={status.tone}>{status.label}</Badge>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {running && !paused ? (
          <Button variant="danger" size="sm" onClick={stop}>
            <Square className="size-3.5" />
            Stop
          </Button>
        ) : (
          <Button variant="primary" size="sm" onClick={start}>
            <Play className="size-3.5" />
            {paused ? "Resume" : "Start"}
          </Button>
        )}
        <Button variant="secondary" size="sm" onClick={requestSkip} disabled={!running || paused}>
          <SkipForward className="size-3.5" />
          Skip
        </Button>
        {lastCatch ? (
          <span className="hidden font-mono text-[11px] text-muted lg:inline">
            Last {lastCatch.lat.toFixed(3)}, {lastCatch.lng.toFixed(3)}
          </span>
        ) : null}
      </div>
    </header>
  );
}
