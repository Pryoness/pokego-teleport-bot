import raw from "@/data/pokemon.json";
import { displayName } from "@/lib/utils";

export const POKEMON_BY_NAME: Record<string, number> = raw as Record<string, number>;

export const POKEMON_NAMES = Object.keys(POKEMON_BY_NAME).sort();

const NAME_SET = new Set(POKEMON_NAMES);

/** Longest names first so "mr mime" wins over "mime". */
const SORTED_FOR_MATCH = [...POKEMON_NAMES].sort((a, b) => b.length - a.length);

export function isValidPokemon(name: string) {
  const lower = name.trim().toLowerCase();
  if (NAME_SET.has(lower)) return true;
  // Try with spaces replaced by hyphens (e.g. "mr mime" -> "mr-mime")
  const hyphenated = lower.replace(/\s+/g, "-");
  if (NAME_SET.has(hyphenated)) return true;
  // Try with hyphens replaced by spaces
  const spaced = lower.replace(/-/g, " ");
  return NAME_SET.has(spaced);
}

export function getPokemonId(name: string) {
  const lower = name.trim().toLowerCase();
  if (POKEMON_BY_NAME[lower]) return POKEMON_BY_NAME[lower];
  const hyphenated = lower.replace(/\s+/g, "-");
  if (POKEMON_BY_NAME[hyphenated]) return POKEMON_BY_NAME[hyphenated];
  return undefined;
}

export function spriteUrl(name: string, shiny = false) {
  const id = getPokemonId(name);
  if (!id) return null;
  if (shiny) {
    return `https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/shiny/${id}.png`;
  }
  return `https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/${id}.png`;
}

export function officialArtUrl(name: string) {
  const id = getPokemonId(name);
  if (!id) return null;
  return `https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/other/official-artwork/${id}.png`;
}

export function titleCasePokemon(name: string) {
  return displayName(name);
}

export function suggestPokemon(query: string, limit = 8) {
  const q = query.trim().toLowerCase().replace(/\s+/g, "-");
  if (!q) return [];
  // Also try the original query with spaces for names that use spaces
  const qSpaced = query.trim().toLowerCase();
  const starts = POKEMON_NAMES.filter((n) => n.startsWith(q) || n.startsWith(qSpaced));
  const contains = POKEMON_NAMES.filter((n) => !n.startsWith(q) && !n.startsWith(qSpaced) && (n.includes(q) || n.includes(qSpaced)));
  return [...starts, ...contains].slice(0, limit);
}

export function findPokemonInText(text: string): string | null {
  const lower = text.toLowerCase();
  for (const name of SORTED_FOR_MATCH) {
    const spaced = name.replace(/-/g, " ");
    const pattern = new RegExp(`(?:^|[^a-z0-9])${escapeReg(spaced)}(?:$|[^a-z0-9])`, "i");
    const dashed = new RegExp(`(?:^|[^a-z0-9])${escapeReg(name)}(?:$|[^a-z0-9])`, "i");
    if (pattern.test(lower) || dashed.test(lower)) return name;
  }
  return null;
}

export function findAllPokemonInText(text: string): string[] {
  const found: string[] = [];
  const lower = text.toLowerCase();
  for (const name of SORTED_FOR_MATCH) {
    const spaced = name.replace(/-/g, " ");
    const pattern = new RegExp(`(?:^|[^a-z0-9])${escapeReg(spaced)}(?:$|[^a-z0-9])`, "i");
    if (pattern.test(lower) && !found.includes(name)) found.push(name);
  }
  return found;
}

function escapeReg(s: string) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
