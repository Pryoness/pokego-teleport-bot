import { useEffect, useRef } from "react";
import { toast } from "sonner";
import { useHuntStore } from "@/lib/pokego/store";

export function EngineHost() {
  const hydrate = useHuntStore((s) => s.hydrate);
  const poll = useHuntStore((s) => s.poll);
  const running = useHuntStore((s) => s.hunter.running);
  const start = useHuntStore((s) => s.start);
  const stop = useHuntStore((s) => s.stop);
  const requestSkip = useHuntStore((s) => s.requestSkip);
  const notice = useHuntStore((s) => s.toast);
  const dismiss = useHuntStore((s) => s.dismissToast);
  const pollIntervalRef = useRef<number | null>(null);

  // Initial hydrate — connect to bot API
  useEffect(() => {
    hydrate();
  }, [hydrate]);

  // Always poll every 3 seconds for live data from the bot
  useEffect(() => {
    const id = window.setInterval(() => {
      poll();
    }, 3000);
    pollIntervalRef.current = id;
    return () => window.clearInterval(id);
  }, [poll]);

  // Also poll immediately when running state changes
  useEffect(() => {
    if (running) {
      poll();
    }
  }, [running, poll]);

  useEffect(() => {
    if (!notice) return;
    toast(notice);
    dismiss();
  }, [notice, dismiss]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA") return;
      if (e.key === "s" || e.key === "S") {
        e.preventDefault();
        const r = useHuntStore.getState().hunter.running;
        if (r) stop();
        else start();
      }
      if (e.key === "k" || e.key === "K") {
        e.preventDefault();
        requestSkip();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [start, stop, requestSkip]);

  return null;
}
