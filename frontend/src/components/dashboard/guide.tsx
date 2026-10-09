import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Panel } from "@/components/ui/panel";

export function GuidePanel() {
  const [open, setOpen] = useState(false);
  if (!open) {
    return (
      <Button variant="ghost" size="sm" className="w-full" onClick={() => setOpen(true)}>
        What changed from v2
      </Button>
    );
  }
  return (
    <Panel title="Rebuild notes" action={<Button size="sm" variant="ghost" onClick={() => setOpen(false)}>Hide</Button>}>
      <div className="space-y-2 text-xs leading-relaxed text-muted">
        <p>
          The original was a Python Discord self-bot plus a Playwright driver for a third-party GPS dashboard.
          That stack cannot run in this app, and those integrations violate Discord / game terms.
        </p>
        <p>
          This command center keeps every operator workflow that actually mattered: hunt list, DSP queue,
          proximity ranking, softban chart, encounter outcomes, stats, and the map.
        </p>
        <p>
          Assist mode is for real hunts — paste feeds, copy coords, mark caught/fled. Sim mode runs a local
          engine so you can rehearse routing and cooldowns. Everything persists in this browser.
        </p>
        <p className="text-subtle">Keys: S start/stop · P pause · K skip</p>
      </div>
    </Panel>
  );
}
