import { useEffect, useState } from "react";
import { Toaster } from "sonner";
import { CooldownPanel } from "./cooldown-panel";
import { EngineHost } from "./engine-host";
import { HeaderBar } from "./header";
import { LogPanel } from "./log-panel";
import { LoopPanel } from "./loop-panel";
import { MapPanel } from "./map-panel";
import { QueuePanel } from "./queue-panel";
import { SettingsPanel } from "./settings-panel";
import { StatsStrip } from "./stats-strip";
import { TargetsPanel } from "./targets-panel";
import { useHuntStore } from "@/lib/pokego/store";

export function CommandShell() {
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
      <Toaster
        theme="dark"
        position="bottom-right"
        toastOptions={{
          style: {
            background: "#181c22",
            color: "#ecece8",
            border: "1px solid color-mix(in oklab, #ecece8 12%, transparent)",
          },
        }}
      />
      <HeaderBar />
      {isNarrow ? (
        <main className="mx-auto flex max-w-[1600px] flex-col gap-2 px-3 py-2 sm:px-4 sm:py-2">
          <StatsStrip />
          <LoopPanel />
          <LogPanel />
          <QueuePanel />
          <MapPanel />
          <CooldownPanel />
          <TargetsPanel />
          <SettingsPanel />
        </main>
      ) : (
        <main className="mx-auto flex max-w-[1600px] flex-col gap-2 px-3 py-2 sm:px-4 sm:py-2 xl:h-[calc(100dvh-3.5rem)] xl:overflow-hidden">
          <StatsStrip />
          <div className="grid grid-cols-1 gap-2 xl:grid-cols-12 xl:flex-1 xl:min-h-0">
            {/* Left column: Engine Loop, Cooldown, Settings */}
            <aside className="flex min-h-0 flex-col gap-2 xl:col-span-3 xl:overflow-hidden">
              <LoopPanel className="shrink-0" />
              <CooldownPanel className="shrink-0" />
              <SettingsPanel className="min-h-0 flex-1" bodyClassName="overflow-y-auto overflow-x-hidden pr-2" />
            </aside>
            {/* Right area: shared 2-row grid so Map=Queue and Log=Hunt heights match */}
            <section className="grid min-h-0 grid-cols-1 grid-rows-1 gap-2 xl:col-span-9 xl:grid-cols-9 xl:grid-rows-[minmax(0,1fr)_minmax(0,1fr)]">
              <div className="flex min-h-0 flex-col xl:col-span-5">
                <MapPanel className="min-h-0 flex-1" />
              </div>
              <div className="flex min-h-0 flex-col xl:col-span-4">
                <QueuePanel className="min-h-0 flex-1" />
              </div>
              <div className="flex min-h-0 flex-col xl:col-span-5">
                <LogPanel className="min-h-0 flex-1" />
              </div>
              <div className="flex min-h-0 flex-col xl:col-span-4">
                <TargetsPanel className="min-h-0 flex-1" />
              </div>
            </section>
          </div>
        </main>
      )}
    </div>
  );
}
