import { useEffect, useRef } from "react";
import type { Map as LeafletMap, Layer } from "leaflet";
import "leaflet/dist/leaflet.css";
import { Panel } from "@/components/ui/panel";
import { useHuntStore } from "@/lib/pokego/store";
import { titleCasePokemon } from "@/lib/pokego/pokemon";
import { useNow } from "./use-now";

export function MapPanel({ className }: { className?: string }) {
  const hostRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<LeafletMap | null>(null);
  const layersRef = useRef<Layer[]>([]);
  const pins = useHuntStore((s) => s.pins);
  const lastCatch = useHuntStore((s) => s.lastCatch);
  const rankedQueue = useHuntStore((s) => s.rankedQueue);
  const addMapSpawn = useHuntStore((s) => s.addMapSpawn);
  const currentId = useHuntStore((s) => s.hunter.currentTaskId);
  const step = useHuntStore((s) => s.hunter.loopStep);
  const now = useNow(2000);
  const queue = rankedQueue(now);

  useEffect(() => {
    let cancelled = false;
    const host = hostRef.current;
    if (!host) return;
    void import("leaflet").then((L) => {
      if (cancelled || mapRef.current) return;
      const map = L.map(host, {
        zoomControl: true,
        attributionControl: true,
      }).setView([20, 10], 2);
      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19,
        attribution: "&copy; OpenStreetMap",
      }).addTo(map);
      map.on("click", (e) => {
        addMapSpawn(e.latlng.lat, e.latlng.lng);
      });
      mapRef.current = map;
      window.setTimeout(() => map.invalidateSize(), 80);
    });
    return () => {
      cancelled = true;
      mapRef.current?.remove();
      mapRef.current = null;
    };
  }, [addMapSpawn]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    void import("leaflet").then((L) => {
      layersRef.current.forEach((l) => map.removeLayer(l));
      layersRef.current = [];
      const bounds: [number, number][] = [];

      if (lastCatch) {
        const m = L.circleMarker([lastCatch.lat, lastCatch.lng], {
          radius: 8,
          color: "#7ea88a",
          fillColor: "#7ea88a",
          fillOpacity: 0.9,
          weight: 2,
        }).bindPopup(`Last catch${lastCatch.pokemon ? ` · ${titleCasePokemon(lastCatch.pokemon)}` : ""}`);
        m.addTo(map);
        layersRef.current.push(m);
        bounds.push([lastCatch.lat, lastCatch.lng]);
      }

      for (const t of queue) {
        if (t.lat == null || t.lng == null) continue;
        const live = t.id === currentId;
        const m = L.circleMarker([t.lat, t.lng], {
          radius: live ? 9 : 6,
          color: live ? "#9eb0c0" : "#5c636c",
          fillColor: live ? "#9eb0c0" : "#9eb0c0",
          fillOpacity: live ? 0.95 : 0.55,
          weight: live ? 3 : 1,
        }).bindPopup(
          `${titleCasePokemon(t.pokemon)}<br/>${t.coords ?? ""}<br/>${t.ivInfo ?? ""}`,
        );
        m.addTo(map);
        layersRef.current.push(m);
        bounds.push([t.lat, t.lng]);
      }

      for (const p of pins) {
        const m = L.circleMarker([p.lat, p.lng], {
          radius: 4,
          color: "#3a414a",
          fillColor: "#8b919a",
          fillOpacity: 0.45,
          weight: 1,
        }).bindPopup(`${titleCasePokemon(p.pokemon)}<br/>${new Date(p.timestamp).toLocaleTimeString()}`);
        m.addTo(map);
        layersRef.current.push(m);
      }

      const live = queue.find((t) => t.id === currentId);
      if (live && live.lat != null && live.lng != null && step >= 2 && step <= 5) {
        map.setView([live.lat, live.lng], 13);
      } else if (bounds.length >= 2) {
        map.fitBounds(bounds, { padding: [28, 28], maxZoom: 5 });
      } else if (bounds.length === 1) {
        map.setView(bounds[0], 4);
      }
    });
  }, [pins, queue, lastCatch, currentId, step]);

  const live = queue.find((t) => t.id === currentId);

  return (
    <Panel
      title="World lock"
      flush
      className={className}
      action={<span className="text-[10px] text-subtle">Click map to pin</span>}
    >
      <div className="relative min-h-0 flex-1 w-full max-h-[300px] xl:max-h-none" style={{ minHeight: 200 }}>
        <div ref={hostRef} className="absolute inset-0 rounded-b-lg" />
        <div className="pointer-events-none absolute bottom-2 left-2 right-2 flex justify-between font-mono text-[11px] text-accent">
          <span>
            {live?.coords
              ? live.coords
              : lastCatch
                ? `origin ${lastCatch.lat.toFixed(4)}, ${lastCatch.lng.toFixed(4)}`
                : "no lock"}
          </span>
          <span>
            {queue.length} queued · {pins.length} history
          </span>
        </div>
      </div>
    </Panel>
  );
}
