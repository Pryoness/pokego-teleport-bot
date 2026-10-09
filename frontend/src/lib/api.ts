/**
 * API client for the PokeGo Teleport Bot Python backend.
 * Maps between Python API response formats and the frontend's TypeScript types.
 */

import type {
  QueueTask,
  HuntEvent,
  StatsData,
  TargetPokemon,
  Settings,
  LastCatch,
  TeleportPin,
  CooldownInfo,
} from "./pokego/types";
import { getCooldownInfo } from "./pokego/cooldown";

const API_BASE = "";

function onAuthFail() {
  // Redirect to login page if not already there
  if (!window.location.pathname.startsWith("/login")) {
    window.location.href = "/login";
  }
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`);
  if (res.status === 401) { onAuthFail(); throw new Error("Unauthorized"); }
  if (!res.ok) throw new Error(`API ${path}: ${res.status}`);
  return res.json();
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401) { onAuthFail(); throw new Error("Unauthorized"); }
  if (!res.ok) throw new Error(`API ${path}: ${res.status}`);
  return res.json();
}

async function del<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { method: "DELETE" });
  if (res.status === 401) { onAuthFail(); throw new Error("Unauthorized"); }
  if (!res.ok) throw new Error(`API ${path}: ${res.status}`);
  return res.json();
}

// --- Mappers ---

function parseTimestampToMs(ts: string | number): number {
  if (typeof ts === "number") return ts * 1000;
  const d = new Date(ts.replace(" ", "T"));
  return d.getTime();
}

function mapQueueItem(raw: any, index: number): QueueTask {
  const now = Date.now();
  const dspMinutes = raw.dsp_minutes ?? raw.remaining_minutes ?? 30;
  const msgId = raw.message_id || 0;
  const counterKey = raw._counter_key || 0;
  // Use counter_key as the unique identifier for removal (always unique, non-zero)
  const queueKey = counterKey || msgId || (index + 1);
  return {
    id: `q_${queueKey}`,
    pokemon: (raw.pokemon || "").toLowerCase(),
    url: raw.url || "",
    coords: raw.coords ?? null,
    lat: raw.lat ?? null,
    lng: raw.lng ?? null,
    dspMinutes,
    expiresAt: raw.expires_at ? raw.expires_at * 1000 : now + dspMinutes * 60000,
    createdAt: raw.created_at ? raw.created_at * 1000 : now,
    messageId: String(queueKey),
    shiny: raw.shiny ?? false,
    ivInfo: raw.iv_info ?? null,
    ivPercent: raw.iv_percent ?? 0,
    attack: null,
    defense: null,
    stamina: null,
    cp: null,
    priority: raw.priority ?? 1,
    status: raw.status ?? "queued",
    source: "feed",
  };
}

function mapEvent(raw: any, index: number): HuntEvent {
  return {
    id: `ev_${index}`,
    timestamp: parseTimestampToMs(raw.timestamp),
    type: (raw.type || "system") as HuntEvent["type"],
    message: raw.message || "",
    details: raw.details,
  };
}

function mapStats(raw: any): StatsData {
  // Convert Unix timestamps from seconds to milliseconds
  const toMs = (arr: any[]): number[] => Array.isArray(arr) ? arr.map((t) => typeof t === 'number' ? t * 1000 : t) : [];
  return {
    totalTeleports: raw.total_teleports ?? 0,
    totalCaught: raw.total_caught ?? 0,
    totalFled: raw.total_fled ?? 0,
    totalExpired: raw.total_expired ?? 0,
    totalSkipped: raw.total_skipped ?? 0,
    totalShundos: raw.total_shundos ?? 0,
    totalHundos: raw.total_hundos ?? 0,
    totalNonTargetHundos: raw.total_non_target_hundos ?? 0,
    totalShinies: raw.total_shinies ?? 0,
    caughtPokemon: raw.caught_pokemon ?? {},
    encounteredPokemon: raw.encountered_pokemon ?? {},
    teleportTimestamps: toMs(raw.teleport_timestamps),
    hundoTimestamps: toMs(raw.hundo_timestamps),
    shinyTimestamps: toMs(raw.shiny_timestamps),
    shundoTimestamps: toMs(raw.shundo_timestamps),
    hundoIntervals: raw.hundo_intervals ?? [],
    hundosSinceCatch: raw.hundos_since_catch ?? 0,
    hundosPerHour: raw.hundos_hour ?? 0,
    shiniesPerHour: raw.shinies_hour ?? 0,
    shundosPerHour: raw.shundos_hour ?? 0,
    teleportsPerHour: raw.teleports_hour ?? 0,
    startTime: raw.start_time ? raw.start_time * 1000 : 0,
    stopTime: raw.stop_time ? raw.stop_time * 1000 : 0,
    elapsedSeconds: raw.elapsed_seconds ?? 0,
    lastCaught: null,
  };
}

function mapTargets(raw: any): TargetPokemon[] {
  if (!raw.targets) return [];
  return raw.targets.map((t: any) => ({
    name: t.name.toLowerCase(),
    priority: t.priority === "high" ? 0 : 1,
    targetOnly: t.target_only ?? false,
    skip: t.skip ?? false,
    addedAt: Date.now(),
  }));
}

function mapSettings(raw: any): Settings {
  return {
    walkAfterTeleport: raw.walk_after_teleport ?? true,
    walkDistanceMeters: raw.walk_distance_meters ?? 10,
    autoRemoveCaught: raw.auto_remove_caught ?? true,
    removeEvolutionLine: raw.remove_evolution_line ?? false,
    skipNonShiny: raw.skip_non_shiny ?? true,
    monitorTimeoutSeconds: raw.monitor_timeout_seconds ?? 30,
    queueLimitPerPokemon: raw.queue_limit_per_pokemon ?? 5,
    clusterSkipThreshold: raw.cluster_skip_threshold ?? 5,
    minDspSeconds: raw.min_dsp_seconds ?? 120,
    deviceTempIntervalSeconds: raw.device_temp_interval_seconds ?? 30,
    demoFeedEnabled: false,
    backgroundImageUrl: raw.background_image_url ?? "",
    backgroundImageUrlMobile: raw.background_image_url_mobile ?? "",
    notifyUserId: raw.notify_user_id ?? "",
  };
}

function mapLastCatch(raw: any): LastCatch | null {
  if (!raw || !raw.coords) return null;
  // coords can be a string "lat,lng" or a dict {lat, lng}
  let lat: number, lng: number;
  if (typeof raw.coords === "string") {
    const parts = raw.coords.replace(",", " ").split();
    if (parts.length < 2) return null;
    lat = parseFloat(parts[0]);
    lng = parseFloat(parts[1]);
  } else if (typeof raw.coords === "object") {
    lat = raw.coords.lat;
    lng = raw.coords.lng;
  } else {
    return null;
  }
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) return null;
  return {
    lat,
    lng,
    time: raw.time ? raw.time * 1000 : Date.now(),
  };
}

function mapPins(raw: any): TeleportPin[] {
  if (!raw.locations) return [];
  return raw.locations.map((p: any) => ({
    lat: p.lat,
    lng: p.lng,
    pokemon: p.pokemon ?? "",
    timestamp: parseTimestampToMs(p.timestamp),
    outcome: p.outcome,
  }));
}

// --- Public API ---

export interface BotStatus {
  running: boolean;
  paused: boolean;
  loopStep: number;
  activity: string;
  currentCoords: string | null;
  queueSize: number;
  inCooldown: boolean;
  cooldownRemaining: number;
  catchCooldownInfo: any;
}

export const api = {
  async getStatus(): Promise<BotStatus> {
    const raw = await get<any>("/api/status");
    return {
      running: raw.running ?? false,
      paused: raw.paused ?? false,
      loopStep: raw.loop_step ?? 0,
      activity: raw.current_activity ?? "Idle",
      currentCoords: raw.current_coords ?? null,
      queueSize: raw.queue_size ?? 0,
      inCooldown: raw.in_cooldown ?? false,
      cooldownRemaining: raw.cooldown_remaining ?? 0,
      catchCooldownInfo: raw.catch_cooldown_info,
      deviceTemp: raw.device_temp ?? null,
      deviceTempTime: raw.device_temp_time ?? null,
      deviceName: raw.device_name ?? null,
    };
  },

  async getQueue(): Promise<QueueTask[]> {
    const raw = await get<any>("/api/queue");
    return (raw.queue || []).map(mapQueueItem);
  },

  async getStats(): Promise<StatsData> {
    const raw = await get<any>("/api/stats");
    return mapStats(raw);
  },

  async getEvents(limit = 50): Promise<HuntEvent[]> {
    const raw = await get<any>(`/api/events?limit=${limit}`);
    return (raw.events || []).map(mapEvent);
  },

  async getTargets(): Promise<TargetPokemon[]> {
    const raw = await get<any>("/api/targets");
    return mapTargets(raw);
  },

  async getSettings(): Promise<Settings> {
    const raw = await get<any>("/api/settings");
    return mapSettings(raw);
  },

  async getLastCatch(): Promise<LastCatch | null> {
    const raw = await get<any>("/api/last_catch");
    return mapLastCatch(raw);
  },

  async getMap(): Promise<TeleportPin[]> {
    const raw = await get<any>("/api/map");
    return mapPins(raw);
  },

  async getPokemonList(): Promise<string[]> {
    const raw = await get<any>("/api/pokemon-list");
    return raw.pokemon || [];
  },

  // Actions
  async start(): Promise<void> {
    await post("/api/start");
  },

  async stop(): Promise<void> {
    await post("/api/stop");
  },

  async togglePause(): Promise<boolean> {
    const res = await post<any>("/api/pause");
    return res.paused ?? false;
  },

  async skip(): Promise<void> {
    await post("/api/skip");
  },

  async addTargets(names: string[], priority: "high" | "low"): Promise<{ added: string[]; failed: string[] }> {
    const nameStr = names.join(",");
    const res = await post<any>("/api/targets", { name: nameStr, priority });
    return { added: res.added || [], failed: res.failed || [] };
  },

  async removeTarget(name: string): Promise<void> {
    await del(`/api/targets/${encodeURIComponent(name)}`);
  },

  async setPriority(name: string, priority: "high" | "low"): Promise<void> {
    await post(`/api/targets/${encodeURIComponent(name)}/priority?priority=${priority}`);
  },

  async toggleSkip(name: string): Promise<void> {
    await post(`/api/targets/${encodeURIComponent(name)}/skip`);
  },

  async toggleTargetOnly(name: string): Promise<void> {
    await post(`/api/targets/${encodeURIComponent(name)}/target-only`);
  },

  async clearQueue(): Promise<void> {
    await del("/api/queue");
  },

  async removeQueueItem(messageId: string): Promise<void> {
    await del(`/api/queue-item/${encodeURIComponent(messageId)}`);
  },

  async removeQueuePokemon(pokemon: string): Promise<void> {
    await del(`/api/queue/${encodeURIComponent(pokemon)}`);
  },

  async updateSettings(patch: Partial<Settings>): Promise<void> {
    const body: Record<string, unknown> = {};
    if (patch.walkAfterTeleport !== undefined) body.walk_after_teleport = patch.walkAfterTeleport;
    if (patch.walkDistanceMeters !== undefined) body.walk_distance_meters = patch.walkDistanceMeters;
    if (patch.autoRemoveCaught !== undefined) body.auto_remove_caught = patch.autoRemoveCaught;
    if (patch.removeEvolutionLine !== undefined) body.remove_evolution_line = patch.removeEvolutionLine;
    if (patch.skipNonShiny !== undefined) body.skip_non_shiny = patch.skipNonShiny;
    if (patch.monitorTimeoutSeconds !== undefined) body.monitor_timeout_seconds = patch.monitorTimeoutSeconds;
    if (patch.queueLimitPerPokemon !== undefined) body.queue_limit_per_pokemon = patch.queueLimitPerPokemon;
    if (patch.clusterSkipThreshold !== undefined) body.cluster_skip_threshold = patch.clusterSkipThreshold;
    if (patch.minDspSeconds !== undefined) body.min_dsp_seconds = patch.minDspSeconds;
    if (patch.deviceTempIntervalSeconds !== undefined) body.device_temp_interval_seconds = patch.deviceTempIntervalSeconds;
    if (patch.backgroundImageUrl !== undefined) body.background_image_url = patch.backgroundImageUrl;
    if (patch.backgroundImageUrlMobile !== undefined) body.background_image_url_mobile = patch.backgroundImageUrlMobile;
    if (patch.notifyUserId !== undefined) body.notify_user_id = patch.notifyUserId;

    await post("/api/settings", body);
  },

  async setLastCatch(lat: number, lng: number, minutesAgo = 0): Promise<void> {
    await post("/api/last_catch", { lat, lng, minutes_ago: minutesAgo });
  },

  async clearLastCatch(): Promise<void> {
    await del("/api/last_catch");
  },

  async clearCooldown(): Promise<void> {
    await post("/api/clear-cooldown");
  },

  async resetStats(): Promise<void> {
    await post("/api/reset-stats");
  },

  async sxLogin(): Promise<void> {
    await post("/api/sx-login");
  },

  async sxLoginBack(): Promise<void> {
    await post("/api/sx-login-back");
  },

  // Auth
  async authCheck(): Promise<{ authenticated: boolean; auth_required: boolean }> {
    const res = await fetch(`${API_BASE}/api/auth-check`);
    if (!res.ok) return { authenticated: false, auth_required: true };
    return res.json();
  },

  async login(username: string, password: string): Promise<boolean> {
    const res = await fetch(`${API_BASE}/api/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    return res.ok;
  },

  async logout(): Promise<void> {
    await fetch(`${API_BASE}/api/logout`, { method: "POST" });
  },

  // Cooldown calculation (client-side)
  cooldownFor(
    coords: string | { lat: number; lng: number } | null,
    lastCatch: LastCatch | null,
    capSeconds: number,
    now = Date.now(),
  ): CooldownInfo {
    return getCooldownInfo(lastCatch, coords, now, capSeconds);
  },
};
