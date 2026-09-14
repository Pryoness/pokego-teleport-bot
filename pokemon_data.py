"""Pokemon data - validation and sprites via PokeAPI."""

import json
import os
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(SCRIPT_DIR, "pokemon_cache.json")
STATIC_PATH = os.path.join(SCRIPT_DIR, "pokemon_list.json")
_pokemon_map = {}  # name (lowercase) -> id (int)


def _load_static():
    """Load the bundled static Pokemon list."""
    global _pokemon_map
    if os.path.exists(STATIC_PATH):
        try:
            with open(STATIC_PATH, "r") as f:
                _pokemon_map = {k: int(v) for k, v in json.load(f).items()}
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
                print(f"[Pokemon] Loaded {len(_pokemon_map)} Pokemon from cache")
            except Exception:
                pass


def is_valid_pokemon(name):
    """Check if a name is a valid Pokemon. Returns False if list is empty."""
    if not _pokemon_map:
        return False  # Strict: reject if data isn't loaded
    return name.lower() in _pokemon_map


def get_pokemon_id(name):
    """Get the PokeAPI ID for a Pokemon."""
    return _pokemon_map.get(name.lower())


def get_sprite_url(name):
    """Get the sprite URL for a Pokemon."""
    pid = _pokemon_map.get(name.lower())
    if pid:
        return f"https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/{pid}.png"
    return None


def get_all_pokemon():
    """Return the full Pokemon map (name -> id)."""
    return dict(_pokemon_map)


# Load static list first (instant), then try API for updates
_load_static()
_fetch_from_api()
