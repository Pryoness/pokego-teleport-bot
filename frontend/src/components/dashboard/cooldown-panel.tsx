import { Panel } from "@/components/ui/panel";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useHuntStore } from "@/lib/pokego/store";
import { formatDuration } from "@/lib/utils";
import { useNow } from "./use-now";
import { useState } from "react";

export function CooldownPanel({ className, readOnly = false }: { className?: string; readOnly?: boolean }) {
  const lastCatch = useHuntStore((s) => s.lastCatch);
  const current = useHuntStore((s) => s.hunter.currentCoords);
  const cooldownFor = useHuntStore((s) => s.cooldownFor);
  const setLastCatch = useHuntStore((s) => s.setLastCatch);
  const clearCooldown = useHuntStore((s) => s.clearCooldown);
  const now = useNow(1000);
  const info = cooldownFor(current, now);
  const [lat, setLat] = useState("");
  const [lng, setLng] = useState("");
  const [mins, setMins] = useState("0");

  const rem = info.remainingSeconds ?? 0;
  const req = info.requiredSeconds ?? 0;
  const pct = req > 0 ? Math.min(100, ((req - rem) / req) * 100) : 100;

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
      {info.active ? (
        <div>
          <div className="flex items-baseline justify-between">
            <span className="font-mono text-xl tabular text-warn">{formatDuration(rem)}</span>
            <span className="text-xs text-muted">{info.distanceKm} km · {Math.round((req || 0) / 60)}m band</span>
          </div>
          <div className="mt-2 h-1 overflow-hidden rounded-full bg-elevated">
            <div className="h-full bg-warn" style={{ width: `${pct}%` }} />
          </div>
        </div>
      ) : (
        <p className="text-sm text-success">{info.reason ?? "Ready to travel"}</p>
      )}
      {lastCatch ? (
        <p className="mt-2 font-mono text-[11px] text-muted">
          Origin {lastCatch.lat.toFixed(4)}, {lastCatch.lng.toFixed(4)}
          {current ? ` → ${current}` : ""}
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
