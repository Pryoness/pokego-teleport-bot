import { Panel } from "@/components/ui/panel";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { useHuntStore } from "@/lib/pokego/store";
import type { Settings } from "@/lib/pokego/types";

export function SettingsPanel({ className, bodyClassName }: { className?: string; bodyClassName?: string }) {
  const settings = useHuntStore((s) => s.settings);
  const patch = useHuntStore((s) => s.patchSettings);
  const resetStats = useHuntStore((s) => s.resetStats);
  const resetAll = useHuntStore((s) => s.resetAll);
  const exportJson = useHuntStore((s) => s.exportJson);
  const importJson = useHuntStore((s) => s.importJson);

  const num = (key: keyof Settings, label: string, unit: string) => (
    <label className="flex items-center justify-between gap-3 py-1.5">
      <span className="text-sm">{label}</span>
      <span className="flex items-center gap-1.5">
        <Input
          type="number"
          className="h-8 w-20 text-right font-mono text-xs"
          value={settings[key] as number}
          onChange={(e) => patch({ [key]: Number(e.target.value) } as Partial<Settings>)}
        />
        <span className="w-6 text-[10px] text-subtle">{unit}</span>
      </span>
    </label>
  );

  return (
    <Panel title="Settings" className={className} bodyClassName={bodyClassName}>
      <div className="divide-y divide-border">
        <div className="space-y-2 py-1">
          <Switch checked={settings.walkAfterTeleport} onCheckedChange={(v) => patch({ walkAfterTeleport: v })} label="Walk after lock" />
          <Switch checked={settings.autoRemoveCaught} onCheckedChange={(v) => patch({ autoRemoveCaught: v })} label="Auto-remove caught" />
          <Switch checked={settings.removeEvolutionLine} onCheckedChange={(v) => patch({ removeEvolutionLine: v })} label="Remove evolution line" />
          <Switch checked={settings.skipNonShiny} onCheckedChange={(v) => patch({ skipNonShiny: v })} label="Skip non-shiny hundos" />
        </div>
        <div className="py-1">
          {num("walkDistanceMeters", "Walk distance", "m")}
          {num("monitorTimeoutSeconds", "Monitor window", "s")}
          {num("queueLimitPerPokemon", "Queue cap / species", "")}
          {num("minDspSeconds", "Min DSP seconds", "s")}
        </div>
        <div className="py-1.5">
          <label className="flex flex-col gap-1">
            <span className="text-sm">Desktop background URL</span>
            <Input
              type="text"
              className="h-8 text-xs"
              placeholder="https://... or leave empty"
              value={settings.backgroundImageUrl}
              onChange={(e) => patch({ backgroundImageUrl: e.target.value })}
            />
          </label>
          <label className="mt-1.5 flex flex-col gap-1">
            <span className="text-sm">Mobile background URL</span>
            <Input
              type="text"
              className="h-8 text-xs"
              placeholder="https://... or leave empty"
              value={settings.backgroundImageUrlMobile}
              onChange={(e) => patch({ backgroundImageUrlMobile: e.target.value })}
            />
          </label>
        </div>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-1.5">
        <Button size="sm" className="w-full" onClick={resetStats}>
          Reset stats
        </Button>
        <Button
          size="sm"
          className="w-full"
          onClick={() => {
            const blob = new Blob([exportJson()], { type: "application/json" });
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            a.download = "pokego-command.json";
            a.click();
            URL.revokeObjectURL(url);
          }}
        >
          Export
        </Button>
        <Button
          size="sm"
          className="w-full"
          onClick={() => {
            const input = document.createElement("input");
            input.type = "file";
            input.accept = "application/json";
            input.onchange = async () => {
              const file = input.files?.[0];
              if (!file) return;
              const text = await file.text();
              importJson(text);
            };
            input.click();
          }}
        >
          Import
        </Button>
        <Button size="sm" variant="danger" className="w-full" onClick={resetAll}>
          Reset workspace
        </Button>
      </div>
    </Panel>
  );
}
