import { Panel } from "@/components/ui/panel";
import { Button } from "@/components/ui/button";
import { LOOP_LABELS } from "@/lib/pokego/types";
import { useHuntStore } from "@/lib/pokego/store";
import { cn } from "@/lib/utils";

const STEPS = [1, 2, 3, 4, 5, 6] as const;

export function LoopPanel({ className }: { className?: string }) {
  const step = useHuntStore((s) => s.hunter.loopStep);
  const activity = useHuntStore((s) => s.hunter.activity);
  const running = useHuntStore((s) => s.hunter.running);
  const paused = useHuntStore((s) => s.hunter.paused);
  const start = useHuntStore((s) => s.start);
  const stop = useHuntStore((s) => s.stop);
  const requestSkip = useHuntStore((s) => s.requestSkip);
  const skipRequested = useHuntStore((s) => s.hunter.skipRequested);

  const isRunning = running && !paused;

  return (
    <Panel title="Engine loop" className={className}>
      <div className="mb-3 flex items-center gap-1.5">
        {isRunning ? (
          <Button
            variant="danger"
            size="sm"
            onClick={stop}
          >
            Stop
          </Button>
        ) : (
          <Button
            variant="success"
            size="sm"
            onClick={start}
          >
            {paused ? "Resume" : "Start"}
          </Button>
        )}
        {isRunning && (
          <Button
            size="sm"
            onClick={requestSkip}
            disabled={skipRequested}
          >
            {skipRequested ? "Skipping…" : "Skip"}
          </Button>
        )}
      </div>
      <ol className="space-y-1.5">
        {STEPS.map((n) => {
          const active = step === n;
          const done = step > n && step !== 0;
          return (
            <li
              key={n}
              className={cn(
                "flex items-center gap-2.5 text-sm",
                active ? "text-accent" : done ? "text-fg" : "text-subtle",
              )}
            >
              <span
                className={cn(
                  "size-2.5 rounded-full shadow-[var(--shadow-border)]",
                  active && "bg-accent shadow-[0_0_0_4px_color-mix(in_oklab,var(--color-accent)_20%,transparent)]",
                  done && !active && "bg-success",
                  !active && !done && "bg-elevated",
                )}
              />
              {LOOP_LABELS[n]}
            </li>
          );
        })}
      </ol>
      <p className="mt-3 font-mono text-xs text-muted">{activity}</p>
    </Panel>
  );
}
