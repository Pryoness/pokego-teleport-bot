import { uid } from "@/lib/utils";
import type { QueueTask, StatsData, TargetPokemon } from "./types";
import { DEFAULT_SETTINGS } from "./types";

export const DEMO_TARGETS: TargetPokemon[] = [
  { name: "garchomp", priority: 0, targetOnly: false, skip: false, addedAt: Date.now() - 3600000 },
  { name: "axew", priority: 0, targetOnly: false, skip: false, addedAt: Date.now() - 3500000 },
  { name: "dragonite", priority: 0, targetOnly: false, skip: false, addedAt: Date.now() - 3400000 },
  { name: "ralts", priority: 1, targetOnly: false, skip: false, addedAt: Date.now() - 3300000 },
  { name: "bagon", priority: 1, targetOnly: false, skip: false, addedAt: Date.now() - 3200000 },
  { name: "dratini", priority: 1, targetOnly: false, skip: false, addedAt: Date.now() - 3100000 },
  { name: "larvitar", priority: 1, targetOnly: false, skip: false, addedAt: Date.now() - 3000000 },
  { name: "gible", priority: 1, targetOnly: false, skip: false, addedAt: Date.now() - 2900000 },
  { name: "noibat", priority: 1, targetOnly: false, skip: false, addedAt: Date.now() - 2800000 },
  { name: "goomy", priority: 1, targetOnly: false, skip: false, addedAt: Date.now() - 2700000 },
];

export function emptyStats(): StatsData {
  return {
    totalTeleports: 0,
    totalCaught: 0,
    totalFled: 0,
    totalExpired: 0,
    totalSkipped: 0,
    totalShundos: 0,
    totalHundos: 0,
    totalNonTargetHundos: 0,
    totalShinies: 0,
    caughtPokemon: {},
    encounteredPokemon: {},
    teleportTimestamps: [],
    hundoTimestamps: [],
    shinyTimestamps: [],
    shundoTimestamps: [],
    hundoIntervals: [],
    hundosSinceCatch: 0,
    hundosPerHour: 0,
    shiniesPerHour: 0,
    shundosPerHour: 0,
    teleportsPerHour: 0,
    startTime: 0,
    stopTime: 0,
    lastCaught: null,
  };
}

export function makeTask(partial: Partial<QueueTask> & { pokemon: string }): QueueTask {
  const now = Date.now();
  const dsp = partial.dspMinutes ?? 25;
  return {
    id: partial.id ?? uid("q"),
    pokemon: partial.pokemon.toLowerCase(),
    url: partial.url ?? "",
    coords: partial.coords ?? null,
    lat: partial.lat ?? null,
    lng: partial.lng ?? null,
    dspMinutes: dsp,
    expiresAt: partial.expiresAt ?? now + dsp * 60_000,
    createdAt: partial.createdAt ?? now,
    messageId: partial.messageId ?? uid("msg"),
    shiny: partial.shiny ?? false,
    ivInfo: partial.ivInfo ?? null,
    ivPercent: partial.ivPercent ?? 0,
    attack: partial.attack ?? null,
    defense: partial.defense ?? null,
    stamina: partial.stamina ?? null,
    cp: partial.cp ?? null,
    priority: partial.priority ?? 1,
    status: partial.status ?? "queued",
    source: partial.source ?? "manual",
  };
}

export { DEFAULT_SETTINGS };
