import { useEffect, useState } from "react";
import { CooldownPanel } from "./cooldown-panel";
import { EngineHost } from "./engine-host";
import { LogPanel } from "./log-panel";
import { MapPanel } from "./map-panel";
import { QueuePanel } from "./queue-panel";
import { StatsStrip } from "./stats-strip";
import { TargetsPanel } from "./targets-panel";
import { useHuntStore } from "@/lib/pokego/store";

/** Read-only dashboard for monitoring — no controls, no settings, no start/stop. */
export function MonitorShell() {
  const backgroundImageUrl = useHuntStore((s) => s.settings.backgroundImageUrl);
  const backgroundImageUrlMobile = useHuntStore((s) => s.settings.backgroundImageUrlMobile);
  const [isNarrow, setIsNarrow] = useState(
    typeof window !== "undefined" ? window.matchMedia("(max-width: 1279px)").matches : true,
  );
  useEffect(() => {
    const mq = window.matchMedia("(max-width: 1279px)");
    const handler = (e: MediaQueryListEvent) => setIsNarrow(e.matches);
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, []);

  const bgUrl = isNarrow ? backgroundImageUrlMobile : backgroundImageUrl;
  const bgStyle = bgUrl
    ? { backgroundImage: `url(${bgUrl})`, backgroundSize: "cover", backgroundPosition: "center", backgroundAttachment: "fixed" }
    : {};

  return (
    <div className="min-h-dvh bg-bg text-fg" style={bgStyle}>
      <EngineHost />
      <div className="border-b border-border bg-card/95 px-4 py-2">
        <h1 className="text-sm font-bold text-fg">PokeGo Monitor (read-only)</h1>
      </div>
      {isNarrow ? (
        <main className="mx-auto flex max-w-[1600px] flex-col gap-2 px-3 py-2 sm:px-4 sm:py-2">
          <StatsStrip />
          <LogPanel />
          <MapPanel />
          <QueuePanel readOnly />
          <CooldownPanel readOnly />
          <TargetsPanel readOnly />
        </main>
      ) : (
        <main className="mx-auto flex max-w-[1600px] flex-col gap-2 px-3 py-2 sm:px-4 sm:py-2 xl:h-[calc(100dvh-3rem)] xl:overflow-hidden">
          <StatsStrip />
          <div className="grid grid-cols-1 gap-2 xl:grid-cols-12 xl:flex-1 xl:min-h-0">
            <section className="grid min-h-0 grid-cols-1 grid-rows-1 gap-2 xl:col-span-12 xl:grid-cols-9 xl:grid-rows-[minmax(0,1fr)_minmax(0,1fr)]">
              <div className="flex min-h-0 flex-col xl:col-span-5">
                <MapPanel className="min-h-0 flex-1" />
              </div>
              <div className="flex min-h-0 flex-col xl:col-span-4">
                <QueuePanel readOnly className="min-h-0 flex-1" />
              </div>
              <div className="flex min-h-0 flex-col xl:col-span-5">
                <LogPanel className="min-h-0 flex-1" />
              </div>
              <div className="flex min-h-0 flex-col xl:col-span-4">
                <TargetsPanel readOnly className="min-h-0 flex-1" />
              </div>
            </section>
          </div>
        </main>
      )}
    </div>
  );
}
