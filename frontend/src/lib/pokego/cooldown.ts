import type { CooldownInfo, LastCatch } from "./types";

/** Catch cooldown chart: (min distance km, cooldown seconds). Max 2 hours (7200s). */
export const COOLDOWN_CHART: ReadonlyArray<readonly [number, number]> = [
  [0, 60],
  [1, 60],
  [2, 60],
  [3, 120],
  [5, 120],
  [7, 300],
  [9, 420],
  [10, 420],
  [12, 480],
  [18, 600],
  [26, 900],
  [42, 1140],
  [65, 1320],
  [76, 1500],
  [81, 1500],
  [100, 2100],
  [220, 2400],
  [250, 2700],
  [350, 3060],
  [375, 3240],
  [460, 3720],
  [500, 3900],
  [565, 4140],
  [700, 4680],
  [800, 5040],
  [900, 5520],
  [1000, 5940],
  [1100, 6420],
  [1200, 6840],
  [1300, 7020],
  [1350, 7200],
];

export const MAX_COOLDOWN = 7200;

export function haversineKm(lat1: number, lon1: number, lat2: number, lon2: number) {
  const R = 6371;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

function toRad(d: number) {
  return (d * Math.PI) / 180;
}

/** Stepwise lookup matching Niantic's published bands. */
export function cooldownForDistance(distanceKm: number, cap = MAX_COOLDOWN) {
  let cooldown = cap;
  for (const [minDist, seconds] of COOLDOWN_CHART) {
    if (distanceKm >= minDist) cooldown = seconds;
  }
  return Math.min(cooldown, cap);
}

export function parseCoords(input: string | null | undefined): { lat: number; lng: number } | null {
  if (!input) return null;
  const m = input.trim().match(/(-?\d+(?:\.\d+)?)\s*[, ]\s*(-?\d+(?:\.\d+)?)/);
  if (!m) return null;
  const lat = Number(m[1]);
  const lng = Number(m[2]);
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) return null;
  if (Math.abs(lat) > 90 || Math.abs(lng) > 180) return null;
  return { lat, lng };
}

export function formatCoords(lat: number, lng: number, digits = 5) {
  return `${lat.toFixed(digits)},${lng.toFixed(digits)}`;
}

export function offsetCoordinate(lat: number, lng: number, meters: number, bearingDeg = 45) {
  const brng = toRad(bearingDeg);
  const dLat = (meters * Math.cos(brng)) / 111320;
  const dLng = (meters * Math.sin(brng)) / (111320 * Math.cos(toRad(lat)));
  return { lat: lat + dLat, lng: lng + dLng };
}

export function getCooldownInfo(
  lastCatch: LastCatch | null,
  target: { lat: number; lng: number } | string | null,
  now = Date.now(),
  cap = MAX_COOLDOWN,
  snapshot?: any,
): CooldownInfo {
  // Use the backend's origins, chart and policy, including flee-only anchors.
  const policy = snapshot?.cooldown_policy;
  if (policy) {
    const parsed = typeof target === "string" ? parseCoords(target) : target;
    if (!parsed) return { active: false, reason: "No target coordinates" };
    const serverNow = snapshot.server_time +
      Math.max(0, now - (snapshot.received_at_ms ?? now)) / 1000;
    let winner: CooldownInfo = { active: false, remainingSeconds: 0, reason: "Ready" };
    let longest = -1;
    for (const origin of policy.origins ?? []) {
      const elapsed = serverNow - origin.t;
      if (elapsed >= cap) continue;
      const distance = haversineKm(origin.lat, origin.lng, parsed.lat, parsed.lng);
      const chart = policy.chart as Array<[number, number]>;
      let base = chart[chart.length - 1][1];
      if (distance <= chart[0][0]) base = chart[0][1];
      else for (let i = 0; i < chart.length - 1; i++) {
        const [d0, c0] = chart[i], [d1, c1] = chart[i + 1];
        if (d0 <= distance && distance < d1) {
          base = policy.mode === "floor" ? c0 : policy.mode === "round_up" ? c1 :
            c0 + (distance - d0) / (d1 - d0) * (c1 - c0);
          break;
        }
      }
      const required = Math.min(cap, base * (1 + policy.percent / 100) + policy.padding_seconds);
      const remaining = Math.max(0, Math.ceil(required - elapsed));
      if (remaining > longest) {
        longest = remaining;
        winner = {
          active: remaining > 0, remainingSeconds: remaining,
          requiredSeconds: required, elapsedSeconds: Math.floor(elapsed),
          distanceKm: Math.round(distance * 10) / 10,
          reason: origin.why?.startsWith("fled") ? "Flee reset" : "Catch cooldown",
        };
      }
    }
    return winner;
  }
  if (!lastCatch) return { active: false, reason: "No previous catch" };
  const elapsedMs = now - lastCatch.time;
  const elapsedSeconds = Math.floor(elapsedMs / 1000);
  if (elapsedSeconds >= cap) {
    return {
      active: false,
      reason: "Cap elapsed — free to travel",
      elapsedSeconds,
      lastCatch,
    };
  }
  const parsed = typeof target === "string" ? parseCoords(target) : target;
  if (!parsed) {
    return { active: false, reason: "No target coordinates", elapsedSeconds, lastCatch };
  }
  const distanceKm = haversineKm(lastCatch.lat, lastCatch.lng, parsed.lat, parsed.lng);
  const required = cooldownForDistance(distanceKm, cap);
  const remaining = Math.max(0, required - elapsedSeconds);
  return {
    active: remaining > 0,
    distanceKm: Math.round(distanceKm * 10) / 10,
    requiredSeconds: required,
    remainingSeconds: remaining,
    elapsedSeconds,
    lastCatch,
  };
}

export function coordsKey(lat: number, lng: number) {
  return `${lat.toFixed(5)},${lng.toFixed(5)}`;
}
