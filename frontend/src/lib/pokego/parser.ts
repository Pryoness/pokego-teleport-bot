import { findPokemonInText } from "./pokemon";
import { parseCoords } from "./cooldown";

export interface ParsedSpawn {
  pokemon: string | null;
  coords: string | null;
  lat: number | null;
  lng: number | null;
  dspMinutes: number | null;
  shiny: boolean;
  ivPercent: number;
  ivInfo: string | null;
  attack: number | null;
  defense: number | null;
  stamina: number | null;
  cp: number | null;
  url: string | null;
  warnings: string[];
}

const DSP_PATTERNS: RegExp[] = [
  /DSP\s+in\s+(\d+)\s*m/i,
  /DSP\s+in\s+(\d+)\s*min/i,
  /DSP[:\s]+(\d+)\s*m/i,
  /despawn\s+in\s+(\d+)\s*m/i,
  /despawn\s+in\s+(\d+)\s*min/i,
  /DSP\s+(\d+)\s*min/i,
  /(\d+)\s*m\s+\d+\s*s/i,
  /(?:dsp|despawn|timer)[:\s]*(\d{1,2}):(\d{2})/i,
  /(?:remaining|left)[:\s]*(\d+)\s*m/i,
];

export function parseSpawnText(raw: string): ParsedSpawn {
  const warnings: string[] = [];
  const text = raw.replace(/\u00a0/g, " ");

  const pokemon = findPokemonInText(text);
  if (!pokemon) warnings.push("No recognized Pokemon name");

  const coordMatch = text.match(/(-?\d{1,2}\.\d{3,})\s*[, ]\s*(-?\d{1,3}\.\d{3,})/);
  let lat: number | null = null;
  let lng: number | null = null;
  let coords: string | null = null;
  if (coordMatch) {
    const parsed = parseCoords(`${coordMatch[1]},${coordMatch[2]}`);
    if (parsed) {
      lat = parsed.lat;
      lng = parsed.lng;
      coords = `${parsed.lat},${parsed.lng}`;
    }
  } else {
    warnings.push("No coordinates found");
  }

  let dspMinutes: number | null = null;
  for (const p of DSP_PATTERNS) {
    const m = text.match(p);
    if (m) {
      if (m[2] !== undefined && p.source.includes(":")) {
        dspMinutes = Number(m[1]) + Math.round(Number(m[2]) / 60);
      } else {
        dspMinutes = Number(m[1]);
      }
      break;
    }
  }
  if (dspMinutes == null) warnings.push("No DSP timer — defaulting to 30m");

  const shiny = /(?:^|[^a-z])(?:shiny|:shiny:|✨)(?:$|[^a-z])/i.test(text);

  let attack: number | null = null;
  let defense: number | null = null;
  let stamina: number | null = null;
  let ivPercent = 0;
  let ivInfo: string | null = null;

  const labeled = text.match(/IV\s*(\d+(?:\.\d+)?)\s*\(\s*A?\s*(\d+)\s*\/\s*D?\s*(\d+)\s*\/\s*S?\s*(\d+)\s*\)/i);
  const triple = text.match(/\b(\d{1,2})\s*\/\s*(\d{1,2})\s*\/\s*(\d{1,2})\b/);
  const pctOnly = text.match(/\b(\d{1,3}(?:\.\d+)?)\s*%\s*IV\b/i) || text.match(/\bIV\s*(\d{1,3}(?:\.\d+)?)\b/i);

  if (labeled) {
    ivPercent = Math.round(Number(labeled[1]));
    attack = Number(labeled[2]);
    defense = Number(labeled[3]);
    stamina = Number(labeled[4]);
  } else if (triple) {
    attack = Number(triple[1]);
    defense = Number(triple[2]);
    stamina = Number(triple[3]);
    if (attack <= 15 && defense <= 15 && stamina <= 15) {
      ivPercent = Math.round(((attack + defense + stamina) / 45) * 100);
    }
  } else if (pctOnly) {
    ivPercent = Math.round(Number(pctOnly[1]));
  }

  if (attack != null && defense != null && stamina != null) {
    ivInfo = `${attack}/${defense}/${stamina} IV (${ivPercent}%)`;
  } else if (ivPercent > 0) {
    ivInfo = `${ivPercent}% IV`;
  }

  const cpMatch = text.match(/\bCP\s*[:\s]*(\d{2,5})\b/i) || text.match(/\b(\d{3,4})cp\b/i);
  const cp = cpMatch ? Number(cpMatch[1]) : null;

  const urlMatch = text.match(/https?:\/\/[^\s<>\]]+/);
  const url = urlMatch ? urlMatch[0].replace(/[.,!?)]+$/, "") : null;

  return {
    pokemon,
    coords,
    lat,
    lng,
    dspMinutes,
    shiny,
    ivPercent,
    ivInfo,
    attack,
    defense,
    stamina,
    cp,
    url,
    warnings,
  };
}

export function parseSpawnBlock(raw: string): ParsedSpawn[] {
  const chunks = raw
    .split(/\n{2,}|^-{3,}$/m)
    .map((c) => c.trim())
    .filter(Boolean);
  if (chunks.length <= 1) {
    const lines = raw.split("\n").map((l) => l.trim()).filter(Boolean);
    if (lines.length > 1 && lines.filter((l) => findPokemonInText(l)).length > 1) {
      return lines.map(parseSpawnText).filter((p) => p.pokemon);
    }
    const single = parseSpawnText(raw);
    return single.pokemon || single.coords ? [single] : [];
  }
  return chunks.map(parseSpawnText).filter((p) => p.pokemon || p.coords);
}
