"""Configuration management with defaults and persistence."""

import json
import os
import threading

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")

DEFAULTS = {
    "discord_email": "",
    "discord_password": "",
    "discord_token": "YOUR_DISCORD_ACCOUNT_TOKEN_HERE",
    "controller_user_id": "YOUR_DISCORD_USER_ID_HERE",
    "watch_channel_id": "",
    "sx_dashboard_url": "https://dashboard.sx-pokego.xyz/#/dashboard/01a03064-e862-724c-a9c3-78c72fdbd509/ownspots",
    "sx_logs_url": "https://dashboard.sx-pokego.xyz/#/dashboard/01a03064-e862-724c-a9c3-78c72fdbd509/logs",
    "sx_login_url": "https://dashboard.sx-pokego.xyz/#",
    "sx_username": "",
    "sx_password": "",
    "target_pokemon": [],
    "catch_cooldown_seconds": 7200,
    "walk_after_teleport": True,
    "walk_distance_meters": 10,
    "auto_remove_caught": True,
    "monitor_timeout_seconds": 30,
    "cluster_skip_threshold": 5,
    "browser_profile_dir": "browser_profile",
    "headless": True,
    "web_port": 8765,
    "catch_log_patterns": ["[CatchPokemon] Caught"],
    "fled_log_patterns": ["fled"],
    "shiny_log_patterns": ["shiny"],
    "notify_user_id": "",
    "high_priority_pokemon": [],
    "target_only_pokemon": [],  # Pokémon to encounter but not catch
    "skip_non_shiny": True,
    "queue_limit_per_pokemon": 5,
    "remove_evolution_line": False,
    "additional_watch_channels": [],
    "dm_delay_min_seconds": 2,
    "dm_delay_max_seconds": 5,
    "min_dsp_seconds": 120,  # Skip targets with DSP below this (spawn likely gone)
}


class Config:
    """Thread-safe config manager."""

    def __init__(self):
        self._lock = threading.Lock()
        self._data = {}
        self.load()

    def load(self):
        with self._lock:
            if os.path.exists(CONFIG_PATH):
                with open(CONFIG_PATH, "r") as f:
                    self._data = json.load(f)
            # Apply defaults for missing keys
            for key, value in DEFAULTS.items():
                if key not in self._data:
                    self._data[key] = value

    def save(self):
        with self._lock:
            # Preserve stats_data from the current file (managed by StatsTracker)
            existing_stats = None
            if os.path.exists(CONFIG_PATH):
                try:
                    with open(CONFIG_PATH, "r") as f:
                        existing = json.load(f)
                    if "stats_data" in existing:
                        existing_stats = existing["stats_data"]
                except Exception:
                    pass
            self._data.pop("stats_data", None)
            to_save = dict(self._data)
            if existing_stats is not None:
                to_save["stats_data"] = existing_stats
            with open(CONFIG_PATH, "w") as f:
                json.dump(to_save, f, indent=4)

    def get(self, key, default=None):
        with self._lock:
            return self._data.get(key, default if default is not None else DEFAULTS.get(key))

    def set(self, key, value):
        with self._lock:
            self._data[key] = value
        self.save()

    def get_all(self):
        with self._lock:
            return dict(self._data)

    def update(self, updates):
        with self._lock:
            self._data.update(updates)
        self.save()

    def add_target(self, pokemon_name):
        name = pokemon_name.lower().strip()
        with self._lock:
            if name not in self._data["target_pokemon"]:
                self._data["target_pokemon"].append(name)
        self.save()

    def remove_target(self, pokemon_name):
        name = pokemon_name.lower().strip()
        with self._lock:
            if name in self._data["target_pokemon"]:
                self._data["target_pokemon"].remove(name)
            if name in self._data.get("high_priority_pokemon", []):
                self._data["high_priority_pokemon"].remove(name)
        self.save()

    def get_targets(self):
        with self._lock:
            return list(self._data.get("target_pokemon", []))

    def add_high_priority(self, pokemon_name):
        name = pokemon_name.lower().strip()
        with self._lock:
            if name not in self._data["high_priority_pokemon"]:
                self._data["high_priority_pokemon"].append(name)
        self.save()

    def remove_high_priority(self, pokemon_name):
        name = pokemon_name.lower().strip()
        with self._lock:
            if name in self._data["high_priority_pokemon"]:
                self._data["high_priority_pokemon"].remove(name)
        self.save()

    def get_high_priority(self):
        with self._lock:
            return list(self._data.get("high_priority_pokemon", []))

    def is_high_priority(self, pokemon_name):
        name = pokemon_name.lower().strip()
        with self._lock:
            return name in self._data.get("high_priority_pokemon", [])

    def toggle_target_only(self, pokemon_name):
        name = pokemon_name.lower().strip()
        with self._lock:
            only_list = self._data.get("target_only_pokemon", [])
            if name in only_list:
                only_list.remove(name)
            else:
                only_list.append(name)
            self._data["target_only_pokemon"] = only_list
        self.save()

    def is_target_only(self, pokemon_name):
        name = pokemon_name.lower().strip()
        with self._lock:
            return name in self._data.get("target_only_pokemon", [])


config = Config()
