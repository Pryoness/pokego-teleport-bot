export type LoopStep = 0 | 1 | 2 | 3 | 4 | 5 | 6;

export type TaskStatus = "queued" | "processing" | "done" | "expired" | "awaiting";

export type HunterMode = "assist" | "sim";

export type EventType =
  | "queue"
  | "teleport"
  | "walk"
  | "catch"
  | "fled"
  | "expired"
  | "encounter"
  | "cooldown"
  | "skip"
  | "system"
  | "feed";

export type Priority = 0 | 1; // 0 = high

export interface TargetPokemon {
  name: string;
  priority: Priority;
  addedAt: number;
}

export interface QueueTask {
  id: string;
  pokemon: string;
  url: string;
  coords: string | null;
  lat: number | null;
  lng: number | null;
  dspMinutes: number;
  expiresAt: number;
  createdAt: number;
  messageId: string;
  shiny: boolean;
  ivInfo: string | null;
  ivPercent: number;
  attack: number | null;
  defense: number | null;
  stamina: number | null;
  cp: number | null;
  priority: Priority;
  status: TaskStatus;
  source: "feed" | "manual" | "demo" | "map";
}

export interface HuntEvent {
  id: string;
  timestamp: number;
  type: EventType;
  message: string;
  details?: Record<string, string | number | boolean | null>;
}

export interface TeleportPin {
  lat: number;
  lng: number;
  pokemon: string;
  timestamp: number;
  outcome?: "catch" | "fled" | "skip" | "expired" | "unknown";
}

export interface LastCatch {
  lat: number;
  lng: number;
  time: number;
  pokemon?: string;
}

export interface Settings {
  walkAfterTeleport: boolean;
  walkDistanceMeters: number;
  autoRemoveCaught: boolean;
  removeEvolutionLine: boolean;
  skipNonShiny: boolean;
  monitorTimeoutSeconds: number;
  queueLimitPerPokemon: number;
  clusterSkipThreshold: number;
  minDspSeconds: number;
  demoFeedEnabled: boolean;
  backgroundImageUrl: string;
  backgroundImageUrlMobile: string;
  notifyUserId: string;
}

export interface StatsData {
  totalTeleports: number;
  totalCaught: number;
  totalFled: number;
  totalExpired: number;
  totalSkipped: number;
  totalShundos: number;
  totalHundos: number;
  totalNonTargetHundos: number;
  totalShinies: number;
  caughtPokemon: Record<string, number>;
  encounteredPokemon: Record<string, number>;
  teleportTimestamps: number[];
  hundoTimestamps: number[];
  shinyTimestamps: number[];
  shundoTimestamps: number[];
  hundoIntervals: number[];
  hundosSinceCatch: number;
  startTime: number;
  stopTime: number;
  lastCaught: {
    pokemon: string;
    timestamp: number;
    cp: number | null;
    iv: string | null;
    shiny: boolean;
  } | null;
}

export interface HunterState {
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
}

export interface CooldownInfo {
  active: boolean;
  reason?: string;
  distanceKm?: number;
  requiredSeconds?: number;
  remainingSeconds?: number;
  elapsedSeconds?: number;
  lastCatch?: LastCatch;
}

export const DEFAULT_SETTINGS: Settings = {
  walkAfterTeleport: true,
  walkDistanceMeters: 10,
  autoRemoveCaught: true,
  removeEvolutionLine: false,
  skipNonShiny: true,
  monitorTimeoutSeconds: 8,
  queueLimitPerPokemon: 5,
  clusterSkipThreshold: 5,
  minDspSeconds: 120,
  demoFeedEnabled: true,
  backgroundImageUrl: "",
  backgroundImageUrlMobile: "",
  notifyUserId: "",
};

export const LOOP_LABELS: Record<LoopStep, string> = {
  0: "Idle",
  1: "Watching",
  2: "Extracting coords",
  3: "Lock location",
  4: "Walk offset",
  5: "Monitor encounter",
  6: "Cooldown",
};
