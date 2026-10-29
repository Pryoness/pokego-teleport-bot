"""Pokemon data - validation and sprites via PokeAPI."""

import json
import os
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(SCRIPT_DIR, "pokemon_cache.json")
STATIC_PATH = os.path.join(SCRIPT_DIR, "pokemon_list.json")
_pokemon_map = {}  # name (lowercase) -> id (int)
_pokemon_by_id = {}  # id (int) -> name (lowercase)


def _rebuild_by_id():
    global _pokemon_by_id
    _pokemon_by_id = {v: k for k, v in _pokemon_map.items()}


def _load_static():
    """Load the bundled static Pokemon list."""
    global _pokemon_map
    if os.path.exists(STATIC_PATH):
        try:
            with open(STATIC_PATH, "r") as f:
                _pokemon_map = {k: int(v) for k, v in json.load(f).items()}
            _rebuild_by_id()
            print(f"[Pokemon] Loaded {len(_pokemon_map)} Pokemon from static list")
        except Exception as e:
            print(f"[Pokemon] Failed to load static list: {e}")


def _fetch_from_api():
    """Try to fetch a fresh Pokemon list from PokeAPI."""
    global _pokemon_map
    try:
        url = "https://pokeapi.co/api/v2/pokemon?limit=1025"
        req = urllib.request.Request(url, headers={"User-Agent": "PokeGoBot/2.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        fresh = {}
        for entry in data.get("results", []):
            name = entry["name"].lower()
            pokemon_id = int(entry["url"].rstrip("/").split("/")[-1])
            fresh[name] = pokemon_id
        if fresh:
            _pokemon_map = fresh
            _rebuild_by_id()
            with open(CACHE_PATH, "w") as f:
                json.dump(_pokemon_map, f)
            print(f"[Pokemon] Fetched {len(_pokemon_map)} Pokemon from PokeAPI")
    except Exception as e:
        print(f"[Pokemon] Could not fetch from PokeAPI: {e}")
        # Try cache file
        if os.path.exists(CACHE_PATH):
            try:
                with open(CACHE_PATH, "r") as f:
                    _pokemon_map = {k: int(v) for k, v in json.load(f).items()}
                _rebuild_by_id()
                print(f"[Pokemon] Loaded {len(_pokemon_map)} Pokemon from cache")
            except Exception:
                pass


_SIZED = ("pumpkaboo", "gourgeist")


def _canon(name):
    """'pumpkaboo-small' / 'Pumpkaboo Super Size' -> 'pumpkaboo' (sizes are not differentiated)."""
    n = (name or "").lower().strip()
    for base in _SIZED:
        if n == base or n.startswith(base + "-") or n.startswith(base + " "):
            return base
    return n


def _collapse_sizes():
    global _pokemon_map
    m = {}
    for k, v in _pokemon_map.items():
        m.setdefault(_canon(k), v)
    _pokemon_map = m
    _rebuild_by_id()


def is_valid_pokemon(name):
    """Check if a name is a valid Pokemon. Returns False if list is empty."""
    if not _pokemon_map:
        return False  # Strict: reject if data isn't loaded
    return _canon(name) in _pokemon_map


def get_pokemon_id(name):
    """Get the PokeAPI ID for a Pokemon."""
    return _pokemon_map.get(_canon(name))


def get_sprite_url(name):
    """Get the sprite URL for a Pokemon."""
    pid = _pokemon_map.get(_canon(name))
    if pid:
        return f"https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/{pid}.png"
    return None


def get_all_pokemon():
    """Return the full Pokemon map (name -> id)."""
    return dict(_pokemon_map)


def get_name_by_id(dex_num):
    """Resolve a Pokedex number to the species name (lowercase). Returns None if unknown."""
    return _pokemon_by_id.get(int(dex_num))


# Load static list first (instant), then try API for updates
_load_static()
_fetch_from_api()
_collapse_sizes()
