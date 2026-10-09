import { create } from "zustand";
import { uid } from "@/lib/utils";
import { evolutionLine } from "./evo";
import {
  formatCoords,
  getCooldownInfo,
  haversineKm,
} from "./cooldown";
import { emptyStats, makeTask } from "./defaults";
import { isValidPokemon } from "./pokemon";
import { api, type BotStatus } from "@/lib/api";
import type {
  CooldownInfo,
  EventType,
  HuntEvent,
  HunterMode,
  LastCatch,
  LoopStep,
  QueueTask,
  Settings,
  StatsData,
  TargetPokemon,
  TeleportPin,
} from "./types";
import { DEFAULT_SETTINGS } from "./types";

const MAX_EVENTS = 180;
const MAX_PINS = 80;

interface PersistShape {
  settings: Settings;
  targets: TargetPokemon[];
  queue: QueueTask[];
  stats: StatsData;
  events: HuntEvent[];
  lastCatch: LastCatch | null;
  pins: TeleportPin[];
  mode: HunterMode;
}

export interface HuntStore {
  hydrated: boolean;
  settings: Settings;
  targets: TargetPokemon[];
  queue: QueueTask[];
  stats: StatsData;
  events: HuntEvent[];
  lastCatch: LastCatch | null;
  pins: TeleportPin[];
  hunter: {
    running: boolean;
    paused: boolean;
    mode: HunterMode;
    loopStep: LoopStep;
    activity: string;
    currentTaskId: string | null;
    currentCoords: string | null;
    stepStartedAt: number;
    skipRequested: boolean;
    awaitingOutcome: boolean;
    lastPollAt: number;
  };
  toast: string | null;

  hydrate: () => Promise<void>;
  addTargets: (names: string[], priority: 0 | 1) => { added: string[]; failed: string[] };
  removeTarget: (name: string) => Promise<void>;
  togglePriority: (name: string) => Promise<void>;
  ingestFeed: (text: string) => { added: number; skipped: number; errors: string[] };
  addMapSpawn: (lat: number, lng: number, pokemon?: string) => boolean;
  clearQueue: () => Promise<void>;
  removeFromQueue: (id: string) => Promise<void>;
  start: () => Promise<void>;
  stop: () => Promise<void>;
  requestSkip: () => Promise<void>;
  setMode: (mode: HunterMode) => void;
  patchSettings: (patch: Partial<Settings>) => Promise<void>;
  setLastCatch: (lat: number, lng: number, minutesAgo?: number, pokemon?: string) => Promise<void>;
  clearLastCatch: () => Promise<void>;
  clearCooldown: () => Promise<void>;
  resetStats: () => Promise<void>;
  poll: () => Promise<void>;
  exportJson: () => string;
  importJson: (raw: string) => boolean;
  dismissToast: () => void;
  cooldownFor: (coords: string | { lat: number; lng: number } | null, now?: number) => CooldownInfo;
  rankedQueue: (now?: number) => Array<QueueTask & { distanceKm: number | null; cooldown: CooldownInfo }>;
  loadDemo: () => void;
  resetAll: () => void;
}

function pushEvent(
  events: HuntEvent[],
  type: EventType,
  message: string,
  details?: HuntEvent["details"],
): HuntEvent[] {
  const next = [
    ...events,
    { id: uid("ev"), timestamp: Date.now(), type, message, details },
  ];
  return next.length > MAX_EVENTS ? next.slice(-MAX_EVENTS) : next;
}

// --- Local cache for optimistic updates ---
let _localTargets: TargetPokemon[] = [];
let _localSettings: Settings = { ...DEFAULT_SETTINGS };

export const useHuntStore = create<HuntStore>((set, get) => ({
  hydrated: false,
  settings: { ...DEFAULT_SETTINGS },
  targets: [],
  queue: [],
  stats: emptyStats(),
  events: [
    {
      id: "ev_boot",
      timestamp: 0,
      type: "system",
      message: "Connecting to bot…",
    },
  ],
  lastCatch: null,
  pins: [],
  hunter: {
    running: false,
    paused: false,
    mode: "assist",
    loopStep: 0,
    activity: "Idle",
    currentTaskId: null,
    currentCoords: null,
    stepStartedAt: 0,
    skipRequested: false,
    awaitingOutcome: false,
    lastPollAt: 0,
  },
  toast: null,

  hydrate: async () => {
    if (typeof window === "undefined") return;
    // Load persisted UI preferences (mode, last view)
    let savedMode: HunterMode = "assist";
    try {
      const raw = localStorage.getItem("pokego-ui");
      if (raw) {
        const data = JSON.parse(raw);
        savedMode = data.mode ?? "assist";
      }
    } catch {
      /* ignore */
    }

    try {
      const [status, queue, stats, events, targets, settings, lastCatch, map] = await Promise.all([
        api.getStatus(),
        api.getQueue(),
        api.getStats(),
        api.getEvents(80),
        api.getTargets(),
        api.getSettings(),
        api.getLastCatch(),
        api.getMap(),
      ]);

      _localTargets = targets;
      _localSettings = settings;

      set({
        hydrated: true,
        settings,
        targets,
        queue,
        stats,
        events: events.length > 0 ? events : [{ id: "ev_boot", timestamp: Date.now(), type: "system", message: "No events yet." }],
        lastCatch,
        pins: map.slice(-MAX_PINS),
        hunter: {
          ...get().hunter,
          mode: savedMode,
          running: status.running,
          paused: status.paused,
          loopStep: status.loopStep as LoopStep,
          activity: status.activity,
          currentCoords: status.currentCoords,
          lastPollAt: Date.now(),
        },
      });
    } catch {
      // Bot not running or connection failed — load empty state
      set({
        hydrated: true,
        settings: { ...DEFAULT_SETTINGS },
        targets: [],
        hunter: {
          ...get().hunter,
          mode: savedMode,
          running: false,
          loopStep: 0,
          activity: "Bot offline — start the bot to connect.",
          lastPollAt: 0,
        },
        events: [{ id: "ev_boot", timestamp: Date.now(), type: "system", message: "Bot offline. Start the bot to connect." }],
      });
    }
  },

  addTargets: (names, priority) => {
    // Optimistic local update
    const added: string[] = [];
    const failed: string[] = [];
    const next = [..._localTargets];
    for (const rawName of names) {
      let name = rawName.trim().toLowerCase();
      // Normalize: try exact, then hyphenated, then spaced
      if (!isValidPokemon(name)) {
        const hyphenated = name.replace(/\s+/g, "-");
        if (isValidPokemon(hyphenated)) name = hyphenated;
        else {
          failed.push(rawName);
          continue;
        }
      }
      if (next.some((t) => t.name === name)) continue;
      next.push({ name, priority, addedAt: Date.now() });
      added.push(name);
    }
    _localTargets = next;
    set((s) => ({
      targets: next,
      events: added.length
        ? pushEvent(s.events, "system", `Added ${added.join(", ")}${priority === 0 ? " (high)" : ""}`)
        : s.events,
    }));

    // Fire API call — send normalized names (not raw input)
    const prioStr = priority === 0 ? "high" : "low";
    if (added.length) {
      api.addTargets(added, prioStr).catch(() => {
        set({ toast: "Failed to save targets to bot" });
      });
    }

    return { added, failed };
  },

  removeTarget: async (name) => {
    const lower = name.toLowerCase();
    const next = _localTargets.filter((t) => t.name !== lower);
    _localTargets = next;
    set((s) => ({
      targets: next,
      events: pushEvent(s.events, "system", `Removed ${lower} from hunt list`),
    }));
    try {
      await api.removeTarget(lower);
    } catch {
      set({ toast: "Failed to remove target" });
    }
  },

  togglePriority: async (name) => {
    const lower = name.toLowerCase();
    const current = _localTargets.find((t) => t.name === lower);
    if (!current) return;
    const newPriority: 0 | 1 = current.priority === 0 ? 1 : 0;
    const next = _localTargets.map((t) =>
      t.name === lower ? { ...t, priority: newPriority } : t,
    );
    _localTargets = next;
    set((s) => ({
      targets: next,
      queue: s.queue.map((q) =>
        q.pokemon === lower ? { ...q, priority: newPriority } : q,
      ),
    }));
    try {
      await api.setPriority(lower, newPriority === 0 ? "high" : "low");
    } catch {
      set({ toast: "Failed to update priority" });
    }
  },

  ingestFeed: (text) => {
    // Feed panel is for manual paste — in real bot mode, the bot handles this
    // We'll just show a toast
    set({ toast: "Feed parsing is handled by the bot automatically" });
    return { added: 0, skipped: 0, errors: [] };
  },

  addMapSpawn: (lat, lng, pokemon) => {
    // Map spawn pinning not supported in real bot mode
    set({ toast: "Manual map pinning not available — bot manages spawns" });
    return false;
  },

  clearQueue: async () => {
    set((s) => ({
      queue: [],
      events: pushEvent(s.events, "system", "Queue cleared"),
    }));
    try {
      await api.clearQueue();
    } catch {
      set({ toast: "Failed to clear queue" });
    }
  },

  removeFromQueue: async (id) => {
    // Find the task to get its message_id
    const task = get().queue.find((t) => t.id === id);
    set((s) => ({
      queue: s.queue.filter((t) => t.id !== id),
    }));
    if (task) {
      try {
        await api.removeQueueItem(task.messageId);
      } catch {
        // Fallback to removing by pokemon name
        try {
          await api.removeQueuePokemon(task.pokemon);
        } catch {
          set({ toast: "Failed to remove queue item" });
        }
      }
    }
  },

  start: async () => {
    set((s) => ({
      hunter: {
        ...s.hunter,
        running: true,
        paused: false,
        loopStep: s.hunter.loopStep || 1,
        activity: "Starting…",
      },
      stats: {
        ...s.stats,
        stopTime: 0,
      },
      events: pushEvent(s.events, "system", "Engine started"),
    }));
    try {
      await api.start();
      await get().poll();
    } catch {
      set({ toast: "Failed to start bot" });
    }
  },

  stop: async () => {
    set((s) => ({
      hunter: {
        ...s.hunter,
        running: true,  // Keep running=true so worker loop stays alive
        paused: true,   // But paused=true so worker pauses
        loopStep: 0,
        activity: "Paused",
      },
      stats: {
        ...s.stats,
        stopTime: Date.now(),
      },
      events: pushEvent(s.events, "system", "Engine paused"),
    }));
    try {
      await api.stop();
      await get().poll();
    } catch {
      set({ toast: "Failed to pause bot" });
    }
  },

  requestSkip: async () => {
    set((s) => ({ hunter: { ...s.hunter, skipRequested: true } }));
    try {
      await api.skip();
    } catch {
      set({ toast: "Failed to skip target" });
    }
    // Reset the flag after a short delay
    setTimeout(() => {
      set((s) => ({ hunter: { ...s.hunter, skipRequested: false } }));
    }, 2000);
  },

  setMode: (mode) => {
    set((s) => ({ hunter: { ...s.hunter, mode } }));
    // Persist UI preferences
    try {
      const raw = localStorage.getItem("pokego-ui");
      const data = raw ? JSON.parse(raw) : {};
      data.mode = mode;
      localStorage.setItem("pokego-ui", JSON.stringify(data));
    } catch {
      /* ignore */
    }
  },

  patchSettings: async (patch) => {
    const next = { ..._localSettings, ...patch };
    _localSettings = next;
    set({ settings: next });
    try {
      await api.updateSettings(patch);
    } catch {
      set({ toast: "Failed to save settings" });
    }
  },

  setLastCatch: async (lat, lng, minutesAgo = 0, pokemon) => {
    set((s) => ({
      lastCatch: { lat, lng, time: Date.now() - minutesAgo * 60000, pokemon },
      events: pushEvent(s.events, "catch", `Last catch set at ${formatCoords(lat, lng)} (${minutesAgo}m ago)`),
    }));
    try {
      await api.setLastCatch(lat, lng, minutesAgo);
    } catch {
      set({ toast: "Failed to set last catch" });
    }
  },

  clearLastCatch: async () => {
    set((s) => ({
      lastCatch: null,
      events: pushEvent(s.events, "system", "Last catch cleared"),
    }));
    try {
      await api.clearLastCatch();
    } catch {
      set({ toast: "Failed to clear last catch" });
    }
  },

  clearCooldown: async () => {
    set((s) => ({
      lastCatch: null,
      events: pushEvent(s.events, "cooldown", "Cooldown cleared"),
      toast: "Cooldown cleared",
    }));
    try {
      await api.clearCooldown();
    } catch {
      set({ toast: "Failed to clear cooldown" });
    }
  },

  resetStats: async () => {
    set((s) => ({
      stats: emptyStats(),
      pins: [],
      events: pushEvent(s.events, "system", "Stats reset"),
    }));
    try {
      await api.resetStats();
    } catch {
      set({ toast: "Failed to reset stats" });
    }
  },

  poll: async () => {
    try {
      const [status, queue, stats, events, targets, lastCatch, map] = await Promise.all([
        api.getStatus(),
        api.getQueue(),
        api.getStats(),
        api.getEvents(80),
        api.getTargets(),
        api.getLastCatch(),
        api.getMap(),
      ]);

      _localTargets = targets;

      // Don't overwrite settings on every poll — only update from API on hydrate
      // Settings are managed locally and synced via patchSettings API calls

      set({
        queue,
        stats,
        events: events.length > 0 ? events : get().events,
        targets,
        lastCatch,
        pins: map.slice(-MAX_PINS),
        hunter: {
          ...get().hunter,
          running: status.running,
          paused: status.paused,
          loopStep: status.loopStep as LoopStep,
          activity: status.activity,
          currentCoords: status.currentCoords,
          lastPollAt: Date.now(),
        },
      });
    } catch {
      // Connection lost — don't crash, just mark as potentially offline
      set((s) => ({
        hunter: { ...s.hunter, activity: "Connection lost — retrying…", lastPollAt: Date.now() },
      }));
    }
  },

  exportJson: () => {
    const s = get();
    return JSON.stringify(
      {
        version: 1,
        exportedAt: new Date().toISOString(),
        settings: s.settings,
        targets: s.targets,
        queue: s.queue,
        stats: s.stats,
        lastCatch: s.lastCatch,
        pins: s.pins,
        mode: s.hunter.mode,
      },
      null,
      2,
    );
  },

  importJson: (raw) => {
    try {
      const data = JSON.parse(raw) as PersistShape & { version?: number };
      if (!data || typeof data !== "object") return false;
      set((s) => ({
        settings: { ...DEFAULT_SETTINGS, ...data.settings },
        targets: Array.isArray(data.targets) ? data.targets : s.targets,
        events: pushEvent(s.events, "system", "Imported workspace"),
        toast: "Import complete",
      }));
      return true;
    } catch {
      return false;
    }
  },

  dismissToast: () => set({ toast: null }),

  cooldownFor: (coords, now = Date.now()) => {
    const s = get();
    return getCooldownInfo(s.lastCatch, coords, now, 7200);
  },

  rankedQueue: (now = Date.now()) => {
    const s = get();
    return s.queue
      .filter((t) => t.status === "queued" || t.status === "processing" || t.status === "awaiting")
      .filter((t) => t.expiresAt > now)
      .map((t) => {
        const cd = getCooldownInfo(
          s.lastCatch,
          t.lat != null && t.lng != null ? { lat: t.lat, lng: t.lng } : t.coords,
          now,
          7200,
        );
        const dist =
          s.lastCatch && t.lat != null && t.lng != null
            ? Math.round(haversineKm(s.lastCatch.lat, s.lastCatch.lng, t.lat, t.lng) * 10) / 10
            : null;
        return { ...t, distanceKm: dist, cooldown: cd };
      })
      .sort((a, b) => {
        // Catchable first (cooldown not active)
        const aReady = a.cooldown.active ? 1 : 0;
        const bReady = b.cooldown.active ? 1 : 0;
        if (aReady !== bReady) return aReady - bReady;
        // Then priority
        if (a.priority !== b.priority) return a.priority - b.priority;
        // Then distance
        const da = a.distanceKm ?? 9e9;
        const db = b.distanceKm ?? 9e9;
        if (da !== db) return da - db;
        // Then DSP remaining
        return a.expiresAt - b.expiresAt;
      });
  },

  loadDemo: () => {
    set({ toast: "Demo mode not available in live bot mode" });
  },

  resetAll: () => {
    set({
      settings: { ...DEFAULT_SETTINGS },
      targets: [],
      queue: [],
      stats: emptyStats(),
      lastCatch: null,
      pins: [],
      hunter: {
        running: false,
        paused: false,
        mode: "assist",
        loopStep: 0,
        activity: "Idle",
        currentTaskId: null,
        currentCoords: null,
        stepStartedAt: 0,
        skipRequested: false,
        awaitingOutcome: false,
        lastPollAt: 0,
      },
      events: [
        { id: uid("ev"), timestamp: Date.now(), type: "system", message: "Workspace reset." },
      ],
      toast: "Workspace reset",
    });
    api.resetStats().catch(() => {});
  },
}));
