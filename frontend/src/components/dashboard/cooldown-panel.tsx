import { Panel } from "@/components/ui/panel";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useHuntStore } from "@/lib/pokego/store";
import { formatDuration } from "@/lib/utils";
import { useNow } from "./use-now";
import { useState } from "react";

export function CooldownPanel({ className, readOnly = false }: { className?: string; readOnly?: boolean }) {
  const lastCatch = useHuntStore((s) => s.lastCatch);
  const backendInfo = useHuntStore((s) => s.catchCooldownInfo);
  const lastPollAt = useHuntStore((s) => s.hunter.lastPollAt);
  const setLastCatch = useHuntStore((s) => s.setLastCatch);
  const clearCooldown = useHuntStore((s) => s.clearCooldown);
  const now = useNow(1000);
  const [lat, setLat] = useState("");
  const [lng, setLng] = useState("");
  const [mins, setMins] = useState("0");

  // Use backend's catch_cooldown_info which is calculated using the bot's actual position
  const active = backendInfo?.active ?? false;
  const distKm = backendInfo?.distance_km ?? 0;
  const req = (backendInfo?.required_minutes ?? 0) * 60;
  const elapsed = backendInfo?.elapsed_minutes != null ? backendInfo.elapsed_minutes * 60 : 0;
  const reason = backendInfo?.reason;

  // Live countdown: subtract time elapsed since last poll from the backend's remaining_seconds
  const snapshotRem = backendInfo?.remaining_seconds ?? 0;
  const secsSincePoll = lastPollAt > 0 ? Math.floor((now - lastPollAt) / 1000) : 0;
  const liveRem = active ? Math.max(0, snapshotRem - secsSincePoll) : 0;
  const anyTotal = backendInfo?.anywhere_total_seconds ?? 7200;
  const anyRem = Math.max(0, (backendInfo?.anywhere_remaining_seconds ?? 0) - secsSincePoll);
  const anyPct = Math.min(100, ((anyTotal - anyRem) / anyTotal) * 100);
  const anchorWhy: string | null = backendInfo?.anchor_reason ?? null;
  const anchorAge = backendInfo?.anchor_age_seconds != null ? backendInfo.anchor_age_seconds + secsSincePoll : null;
  const next = backendInfo?.next_target ?? null;
  const nextRem = next ? Math.max(0, (next.remaining_seconds ?? 0) - secsSincePoll) : 0;
  const pct = req > 0 ? Math.min(100, ((req - liveRem) / req) * 100) : active ? 0 : 100;

  return (
    <Panel
      title="Catch cooldown"
      className={className}
      action={
        readOnly ? undefined : (
          <Button size="sm" variant="ghost" onClick={clearCooldown}>
            Clear
          </Button>
        )
      }
    >
      {anyRem > 0 ? (
        <div>
          <div className="flex items-baseline justify-between">
            <span className="font-mono text-xl tabular text-warn">{formatDuration(anyRem)}</span>
            <span className="text-xs text-muted">until catch anywhere</span>
          </div>
          <div className="mt-2 h-1 overflow-hidden rounded-full bg-elevated">
            <div className="h-full bg-warn" style={{ width: `${anyPct}%` }} />
          </div>
          {anchorAge != null ? (
            <p className="mt-1 font-mono text-xs text-muted">
              {anchorWhy && anchorWhy !== "catch" ? "Reset by flee" : "Last catch"} {formatDuration(anchorAge)} ago
            </p>
          ) : null}
          {active && liveRem > 0 && distKm > 0 ? (
            <p className="mt-1 text-xs text-warn">
              Here: {formatDuration(liveRem)} left ({distKm} km · {Math.round((req || 0) / 60)}m)
            </p>
          ) : null}
        </div>
      ) : active ? (
        <div>
          <div className="flex items-baseline justify-between">
            <span className="font-mono text-xl tabular text-warn">{formatDuration(liveRem)}</span>
            <span className="text-xs text-muted">{distKm} km · {Math.round((req || 0) / 60)}m band</span>
          </div>
          <div className="mt-2 h-1 overflow-hidden rounded-full bg-elevated">
            <div className="h-full bg-warn" style={{ width: `${pct}%` }} />
          </div>
        </div>
      ) : (
        <div>
          <p className="text-sm text-success">{reason ?? "Ready to travel"}</p>
          {lastCatch && elapsed > 0 ? (
            <p className="mt-1 font-mono text-xs text-muted">
              Last catch {formatDuration(elapsed)} ago
            </p>
          ) : null}
        </div>
      )}
      {next ? (
        <div className="mt-2 rounded-md bg-elevated px-2 py-1.5 text-xs">
          <span className="text-muted">Next: </span>
          <span className="font-medium">{next.name}</span>
          {next.distance_km != null ? <span className="text-muted"> · {next.distance_km} km</span> : null}
          <span className={nextRem > 0 ? "text-warn" : "text-success"}>
            {" · "}
            {nextRem > 0 ? `ready in ${formatDuration(nextRem)}` : "ready"}
          </span>
        </div>
      ) : null}
      {lastCatch ? (
        <p className="mt-2 font-mono text-[11px] text-muted">
          Origin {lastCatch.lat.toFixed(4)}, {lastCatch.lng.toFixed(4)}
        </p>
      ) : null}

      {readOnly ? null : (
        <>
          <div className="mt-3 grid grid-cols-3 gap-1.5">
            <Input placeholder="Lat" value={lat} onChange={(e) => setLat(e.target.value)} className="h-8 text-xs" />
            <Input placeholder="Lng" value={lng} onChange={(e) => setLng(e.target.value)} className="h-8 text-xs" />
            <Input placeholder="Min ago" type="number" value={mins} onChange={(e) => setMins(e.target.value)} className="h-8 text-xs" />
          </div>
          <Button
            size="sm"
            className="mt-1.5 w-full"
            onClick={() => {
              const a = Number(lat);
              const b = Number(lng);
              if (!Number.isFinite(a) || !Number.isFinite(b)) return;
              setLastCatch(a, b, Number(mins) || 0);
            }}
          >
            Set last catch
          </Button>
        </>
      )}
    </Panel>
  );
}
