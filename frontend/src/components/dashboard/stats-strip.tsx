import { useHuntStore } from "@/lib/pokego/store";
import { formatDuration } from "@/lib/utils";
import { useNow } from "./use-now";

function Card({
  label,
  value,
  sub,
  tone,
}: {
  label: string;
  value: string | number;
  sub?: string;
  tone?: "accent" | "success" | "warn";
}) {
  const color =
    tone === "success" ? "text-success" : tone === "warn" ? "text-warn" : "text-accent";
  return (
    <div className="rounded-md bg-surface px-2.5 py-1.5 shadow-[var(--shadow-border)]">
      <div className={`font-mono text-base font-medium tabular leading-none ${color}`}>{value}</div>
      <div className="mt-0.5 text-[10px] uppercase tracking-[0.14em] text-muted">{label}</div>
      {sub ? <div className="mt-0.5 text-[10px] text-subtle">{sub}</div> : null}
    </div>
  );
}

export function StatsStrip() {
  const stats = useHuntStore((s) => s.stats);
  const running = useHuntStore((s) => s.hunter.running);
  const paused = useHuntStore((s) => s.hunter.paused);
  const now = useNow(1000);
  const hourAgo = now - 3600_000;
  const tpHour = stats.teleportTimestamps.filter((t) => t >= hourAgo).length;
  const hundoHour = stats.hundoTimestamps.filter((t) => t >= hourAgo).length;
  const shinyHour = stats.shinyTimestamps.filter((t) => t >= hourAgo).length;
  const rate = (n: number) =>
    stats.totalTeleports > 0 ? `${((n / stats.totalTeleports) * 100).toFixed(1)}%` : "0%";
  const elapsed = stats.startTime
    ? (running && !paused)
      ? Math.floor((now - stats.startTime) / 1000)
      : stats.stopTime
        ? Math.floor((stats.stopTime - stats.startTime) / 1000)
        : 0
    : 0;
  const shinyCum = (1 - (499 / 500) ** (stats.hundosSinceCatch + 1)) * 100;

  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 xl:grid-cols-8">
      <Card label="Locks" value={stats.totalTeleports} sub={`${tpHour}/hr`} />
      <Card label="Caught" value={stats.totalCaught} tone="success" />
      <Card label="Fled" value={stats.totalFled} />
      <Card label="Shundos" value={stats.totalShundos} tone="success" />
      <Card label="Hundos" value={stats.totalHundos} sub={stats.totalNonTargetHundos > 0 ? `${hundoHour}/hr · ${rate(stats.totalHundos)} · ${stats.totalNonTargetHundos} non-target` : `${hundoHour}/hr · ${rate(stats.totalHundos)}`} />
      <Card label="Shinies" value={stats.totalShinies} tone="warn" sub={`${shinyHour}/hr · ${rate(stats.totalShinies)}`} />
      <Card label="Elapsed" value={elapsed ? formatDuration(elapsed) : "—"} sub="session" />
      <Card label="Shiny odds" value={`${shinyCum.toFixed(1)}%`} sub="since last catch" tone="warn" />
    </div>
  );
}
