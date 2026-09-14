"""Statistics tracking with persistence."""

import json
import os
import time
import threading
import math
from collections import defaultdict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STATS_PATH = os.path.join(SCRIPT_DIR, "stats.json")
MAX_EVENTS = 200

# Cooldown chart: (min_distance_km, cooldown_seconds)
# Based on the user-provided cooldown chart. Max 2 hours (7200s).
COOLDOWN_CHART = [
    (0, 60),       # < 1 km: ~1 min
    (1, 60),       # 1 km: < 1 min
    (2, 60),       # 2 km: 1 min
    (3, 120),      # 3 km: < 2 min
    (5, 120),      # 5 km: 2 min
    (7, 300),      # 7 km: 5 min
    (9, 420),      # 9 km: < 7 min
    (10, 420),     # 10 km: 7 min
    (12, 480),     # 12 km: 8 min
    (18, 600),     # 18 km: 10 min
    (26, 900),     # 26 km: 15 min
    (42, 1140),    # 42 km: 19 min
    (65, 1320),    # 65 km: 22 min
    (76, 1500),    # 76 km: < 25 min
    (81, 1500),    # 81 km: 25 min
    (100, 2100),   # 100 km: 35 min
    (220, 2400),   # 220 km: < 40 min
    (250, 2700),   # 250 km: 45 min
    (350, 3060),   # 350 km: < 51 min
    (375, 3240),   # 375 km: 54 min
    (460, 3720),   # 460 km: 62 min
    (500, 3900),   # 500 km: < 65 min
    (565, 4140),   # 565 km: 69 min
    (700, 4680),   # 700 km: 78 min
    (800, 5040),   # 800 km: 84 min
    (900, 5520),   # 900 km: 92 min
    (1000, 5940),  # 1000 km: 99 min
    (1100, 6420),  # 1100 km: 107 min
    (1200, 6840),  # 1200 km: < 114 min
    (1300, 7020),  # 1300 km: 117 min
    (1350, 7200),  # 1350 km+: 2 hours
]

MAX_COOLDOWN = 7200  # 2 hours


def haversine_km(lat1, lon1, lat2, lon2):
    """Calculate distance between two points in km using haversine formula."""
    R = 6371.0  # Earth radius in km
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def get_cooldown_for_distance(distance_km):
    """Look up cooldown time based on distance from last catch."""
    cooldown = MAX_COOLDOWN  # Default to max
    for min_dist, cd_seconds in COOLDOWN_CHART:
        if distance_km >= min_dist:
            cooldown = cd_seconds
    return min(cooldown, MAX_COOLDOWN)


class StatsTracker:
    """Tracks all bot statistics with thread-safe access and JSON persistence."""

    def __init__(self):
        self._lock = threading.RLock()
        self._data = self._default()
        self._logged_encounter_ids = set()  # Dedup by encounter_id
        self._caught_encounter_ids = set()  # Separate dedup for catches
        self._is_running = False
        self.load()

    def _default(self):
        return {
            "total_teleports": 0,
            "total_caught": 0,
            "total_fled": 0,
            "total_expired": 0,
            "total_shundos": 0,
            "total_hundos": 0,
            "total_non_target_hundos": 0,  # Hundos that appeared but weren't targets
            "total_shinies": 0,
            "caught_pokemon": {},
            "encountered_pokemon": {},
            "cooldown_until": 0,
            "last_catch_coords": None,  # {"lat": float, "lng": float}
            "last_catch_time": 0,
            "hundos_since_catch": 0,  # Resets on each catch
            "last_event": None,
            "events": [],
            "teleport_locations": [],
            # Timestamped events for rate calculations
            "teleport_timestamps": [],
            "hundo_timestamps": [],
            "shiny_timestamps": [],
            "shundo_timestamps": [],
            # Time-to-hundo tracking (seconds from teleport to hundo encounter)
            "hundo_intervals": [],
            # Process start/stop time for elapsed display
            "start_time": 0,
            "stop_time": 0,
        }

    def load(self):
        with self._lock:
            # Try config.json first (portable), then fall back to stats.json
            config_path = os.path.join(SCRIPT_DIR, "config.json")
            loaded = None
            if os.path.exists(config_path):
                try:
                    with open(config_path, "r") as f:
                        cfg = json.load(f)
                    if "stats_data" in cfg:
                        loaded = cfg["stats_data"]
                except Exception:
                    pass
            if loaded is None and os.path.exists(STATS_PATH):
                try:
                    with open(STATS_PATH, "r") as f:
                        loaded = json.load(f)
                except Exception:
                    pass
            if loaded:
                merged = self._default()
                merged.update(loaded)
                self._data = merged

    def save(self):
        with self._lock:
            # Save to stats.json
            with open(STATS_PATH, "w") as f:
                json.dump(self._data, f, indent=2)
            # Also save to config.json for portability
            config_path = os.path.join(SCRIPT_DIR, "config.json")
            try:
                if os.path.exists(config_path):
                    with open(config_path, "r") as f:
                        cfg = json.load(f)
                else:
                    cfg = {}
                cfg["stats_data"] = self._data
                with open(config_path, "w") as f:
                    json.dump(cfg, f, indent=4)
            except Exception as e:
                print(f"[Stats] Warning: could not save to config.json: {e}")

    def _add_event(self, event_type, message, details=None):
        event = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "type": event_type,
            "message": message,
            "details": details or {},
        }
        self._data["events"].append(event)
        if len(self._data["events"]) > MAX_EVENTS:
            self._data["events"] = self._data["events"][-MAX_EVENTS:]
        self._data["last_event"] = event

    def record_start(self):
        """Record that the bot has started — resume the elapsed timer without resetting."n        If this is a fresh start (no previous start_time), start at 0.
        If resuming after stop, adjust start_time so elapsed continues from where it left off."""
        with self._lock:
            prev_start = self._data.get("start_time", 0)
            prev_stop = self._data.get("stop_time", 0)
            if prev_start > 0 and prev_stop > 0:
                # Resuming after stop — shift start_time forward by the pause duration
                # so elapsed continues from where it was frozen
                pause_duration = time.time() - prev_stop
                self._data["start_time"] = prev_start + pause_duration
                self._data["stop_time"] = 0
                print("[Stats] Elapsed timer resumed (not reset)")
            elif prev_start > 0 and prev_stop == 0:
                # Recovering from crash — keep existing start_time, elapsed continues
                print("[Stats] Elapsed timer continued (crash recovery)")
            else:
                # Fresh start
                self._data["start_time"] = time.time()
                self._data["stop_time"] = 0
                print("[Stats] Elapsed timer started at 0")
            self._is_running = True
        self.save()

    def record_stop(self):
        """Record that the bot has stopped — freezes the elapsed timer."""
        with self._lock:
            self._is_running = False
            self._data["stop_time"] = time.time()
        self.save()

    def record_teleport(self, pokemon, coords):
        with self._lock:
            now = time.time()
            self._data["total_teleports"] += 1
            self._data["teleport_timestamps"].append(now)
            # Keep only last 1000
            if len(self._data["teleport_timestamps"]) > 1000:
                self._data["teleport_timestamps"] = self._data["teleport_timestamps"][-1000:]
            self._add_event("teleport", f"Teleported to {pokemon} at {coords}")
            # Parse coords string "lat, lng"
            try:
                parts = coords.replace(",", " ").split()
                lat = float(parts[0])
                lng = float(parts[1])
                self._data["teleport_locations"].append({
                    "lat": lat,
                    "lng": lng,
                    "pokemon": pokemon,
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                })
                # Keep only last 100 locations
                if len(self._data["teleport_locations"]) > 100:
                    self._data["teleport_locations"] = self._data["teleport_locations"][-100:]
            except (ValueError, IndexError):
                pass
        self.save()

    def record_encounter(self, pokemon, cp, iv, shiny=False, iv_percent=0, encounter_id=None, is_target=True):
        with self._lock:
            # Deduplicate by encounter_id — prevents double-counting
            if encounter_id:
                if encounter_id in self._logged_encounter_ids:
                    return  # Already logged this encounter
                self._logged_encounter_ids.add(encounter_id)
                if len(self._logged_encounter_ids) > 2000:
                    # Keep only recent IDs to prevent unbounded growth
                    self._logged_encounter_ids = set(list(self._logged_encounter_ids)[-1000:])
            now = time.time()
            name = pokemon.lower()
            self._data["encountered_pokemon"][name] = self._data["encountered_pokemon"].get(name, 0) + 1
            display = pokemon.replace("-", " ").title()
            
            # Count shinies on encounter (not just catch)
            if shiny:
                self._data["total_shinies"] += 1
                self._data["shiny_timestamps"].append(now)
                if len(self._data["shiny_timestamps"]) > 500:
                    self._data["shiny_timestamps"] = self._data["shiny_timestamps"][-500:]
            
            # Check for hundo (100% IV)
            is_hundo = iv_percent == 100 or (iv and "100%" in str(iv))
            if is_hundo:
                self._data["total_hundos"] += 1
                self._data["hundos_since_catch"] += 1
                self._data["hundo_timestamps"].append(now)
                if len(self._data["hundo_timestamps"]) > 500:
                    self._data["hundo_timestamps"] = self._data["hundo_timestamps"][-500:]
                # Track time-to-hundo (from last teleport)
                if self._data["teleport_timestamps"]:
                    interval = now - self._data["teleport_timestamps"][-1]
                    self._data["hundo_intervals"].append(int(interval))
                    if len(self._data["hundo_intervals"]) > 200:
                        self._data["hundo_intervals"] = self._data["hundo_intervals"][-200:]
                # Track non-target hundos
                if not is_target:
                    self._data["total_non_target_hundos"] += 1
            
            if shiny and is_hundo:
                self._data["total_shundos"] += 1
                self._data["shundo_timestamps"].append(now)
                if len(self._data["shundo_timestamps"]) > 100:
                    self._data["shundo_timestamps"] = self._data["shundo_timestamps"][-100:]
            
            iv_str = iv or "unknown"
            shiny_str = " shiny" if shiny else ""
            target_str = "" if is_target else " (non-target)"
            # Only log to operator log if it's a hundo, shiny, or shundo
            if shiny or is_hundo:
                self._add_event("encounter", f"Encountered {display}{shiny_str}{target_str} — {cp or '?'}cp, IV: {iv_str}, shiny={shiny}")
        self.save()

    def record_caught(self, pokemon, cp=None, iv=None, shiny=False, iv_percent=0, encounter_id=None, coords=None, is_target=True):
        with self._lock:
            now = time.time()
            # ALWAYS update last catch coords for cooldown — even if this encounter
            # was already counted in stats. The cooldown must reflect the most recent
            # catch location, regardless of stat dedup.
            if coords:
                try:
                    parts = coords.replace(",", " ").split()
                    lat = float(parts[0])
                    lng = float(parts[1])
                    self._data["last_catch_coords"] = {"lat": lat, "lng": lng}
                    self._data["last_catch_time"] = now
                except (ValueError, IndexError):
                    pass
            # Deduplicate catches — skip stat counting if already recorded,
            # but coords above were already updated.
            if encounter_id:
                if encounter_id in self._caught_encounter_ids:
                    self.save()
                    return  # Already counted in stats
                self._caught_encounter_ids.add(encounter_id)
            name = pokemon.lower()
            display = pokemon.replace("-", " ").title()
            self._data["total_caught"] += 1
            self._data["caught_pokemon"][name] = self._data["caught_pokemon"].get(name, 0) + 1
            self._data["last_caught"] = {
                "pokemon": name,
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "cp": cp,
                "iv": iv,
                "shiny": shiny,
            }
            # Check if this encounter was already logged by record_encounter
            # to avoid double-counting shinies/hundos/shundos
            already_encountered = encounter_id and encounter_id in self._logged_encounter_ids
            # Detect hundo more robustly: check iv_percent, iv string for "100%",
            # and also check for perfect IVs like "15/15/15"
            iv_str_full = str(iv) if iv else ""
            is_hundo = (
                iv_percent == 100
                or "100%" in iv_str_full
                or "15/15/15" in iv_str_full
                or (iv_percent == 100.0)
            )
            if is_hundo and not already_encountered:
                self._data["total_hundos"] += 1
                self._data["hundos_since_catch"] += 1
                self._data["hundo_timestamps"].append(now)
                if len(self._data["hundo_timestamps"]) > 500:
                    self._data["hundo_timestamps"] = self._data["hundo_timestamps"][-500:]
                if not is_target:
                    self._data["total_non_target_hundos"] += 1
            if shiny and not already_encountered:
                self._data["total_shinies"] += 1
                self._data["shiny_timestamps"].append(now)
                if len(self._data["shiny_timestamps"]) > 500:
                    self._data["shiny_timestamps"] = self._data["shiny_timestamps"][-500:]
            is_shundo = shiny and is_hundo
            if is_shundo and not already_encountered:
                self._data["total_shundos"] += 1
                self._data["shundo_timestamps"].append(now)
                if len(self._data["shundo_timestamps"]) > 100:
                    self._data["shundo_timestamps"] = self._data["shundo_timestamps"][-100:]
            # Reset cumulative hundo counter on catch — BUT NOT on shundo catches.
            # A shundo is the best outcome and shouldn't reset the shiny odds.
            if not is_shundo:
                self._data["hundos_since_catch"] = 0
            iv_str = iv or "unknown"
            shiny_str = " shiny" if shiny else ""
            target_str = "" if is_target else " (non-target)"
            self._add_event("catch", f"Caught {display}{shiny_str}{target_str} — {cp or '?'}cp, IV: {iv_str}, shiny={shiny}")
        self.save()

    def record_fled(self, pokemon):
        with self._lock:
            display = pokemon.replace("-", " ").title()
            self._data["total_fled"] += 1
            self._add_event("fled", f"{display} fled")
        self.save()

    def record_expired(self, pokemon):
        with self._lock:
            display = pokemon.replace("-", " ").title()
            self._data["total_expired"] += 1
            self._add_event("expired", f"{display} expired (DSP timer elapsed)")
        self.save()

    def start_cooldown(self, seconds):
        with self._lock:
            self._data["cooldown_until"] = time.time() + seconds
            mins = seconds // 60
            self._add_event("cooldown", f"Catch cooldown started ({mins}m)")
        self.save()

    def start_distance_cooldown(self, target_coords):
        """Legacy: kept for backward compat. Use per-target cooldown checking instead."""
        pass

    def get_catch_cooldown_for_target(self, target_coords):
        """Calculate required cooldown (seconds) based on distance from last catch to target.
        Returns 0 if no catch has been made or 2+ hours have passed since last catch.
        Accepts either a tuple (lat, lng) or a string 'lat,lng'."""
        with self._lock:
            last_coords = self._data.get("last_catch_coords")
            last_catch_time = self._data.get("last_catch_time", 0)
            if not last_coords or last_catch_time == 0:
                return 0  # No previous catch — no cooldown needed
            if not target_coords:
                return 0
            # Parse target_coords — accept tuple or string
            try:
                if isinstance(target_coords, str):
                    parts = target_coords.replace(",", " ").split()
                    target_lat = float(parts[0])
                    target_lng = float(parts[1])
                elif isinstance(target_coords, (list, tuple)):
                    target_lat = float(target_coords[0])
                    target_lng = float(target_coords[1])
                else:
                    return 0
            except (ValueError, IndexError, TypeError):
                return 0
            # After 2 hours, no cooldown regardless of distance
            elapsed = time.time() - last_catch_time
            if elapsed >= MAX_COOLDOWN:
                return 0
            try:
                distance = haversine_km(
                    last_coords["lat"], last_coords["lng"],
                    target_lat, target_lng
                )
                required = get_cooldown_for_distance(distance)
                # Return remaining time (required - elapsed)
                remaining = required - elapsed
                return max(0, int(remaining))
            except Exception as e:
                print(f"[Stats] Error calculating target cooldown: {e}")
                return 0

    def get_catch_cooldown_info(self, target_coords=None):
        """Get cooldown info for dashboard display.
        Returns dict with distance, required_cooldown, remaining, last_catch_coords.
        Accepts either a tuple (lat, lng) or a string 'lat,lng'."""
        with self._lock:
            last_coords = self._data.get("last_catch_coords")
            last_catch_time = self._data.get("last_catch_time", 0)
            if not last_coords or last_catch_time == 0:
                return {"active": False, "reason": "No previous catch"}
            elapsed = time.time() - last_catch_time
            if elapsed >= MAX_COOLDOWN:
                return {"active": False, "reason": "2+ hours since last catch", "elapsed_minutes": int(elapsed / 60)}
            if target_coords:
                try:
                    # Parse target_coords — accept tuple or string
                    if isinstance(target_coords, str):
                        parts = target_coords.replace(",", " ").split()
                        target_lat = float(parts[0])
                        target_lng = float(parts[1])
                    elif isinstance(target_coords, (list, tuple)):
                        target_lat = float(target_coords[0])
                        target_lng = float(target_coords[1])
                    else:
                        return {"active": False, "reason": "Invalid coords format", "last_catch_coords": last_coords}
                    distance = haversine_km(
                        last_coords["lat"], last_coords["lng"],
                        target_lat, target_lng
                    )
                    required = get_cooldown_for_distance(distance)
                    remaining = max(0, required - elapsed)
                    return {
                        "active": remaining > 0,
                        "distance_km": round(distance, 1),
                        "required_minutes": required // 60,
                        "remaining_seconds": int(remaining),
                        "elapsed_minutes": int(elapsed / 60),
                        "last_catch_coords": last_coords,
                    }
                except Exception:
                    pass
            return {"active": False, "reason": "No target coords", "last_catch_coords": last_coords}

    def clear_cooldown(self):
        with self._lock:
            self._data["cooldown_until"] = 0
            self._data["last_catch_coords"] = None
            self._data["last_catch_time"] = 0
            self._add_event("cooldown", "Catch cooldown cleared")
        self.save()

    def set_manual_catch(self, lat, lng, catch_time=None):
        """Manually set the last catch coordinates and time.
        Allows the bot to respect cooldown from a catch that happened before startup."""
        with self._lock:
            self._data["last_catch_coords"] = {"lat": lat, "lng": lng}
            if catch_time is not None:
                self._data["last_catch_time"] = catch_time
            else:
                self._data["last_catch_time"] = time.time()
            self._add_event("catch", f"Manual catch set: ({lat}, {lng})")
        self.save()

    def clear_manual_catch(self):
        """Clear the last catch coordinates and time."""
        with self._lock:
            self._data["last_catch_coords"] = None
            self._data["last_catch_time"] = 0
            self._add_event("catch", "Manual catch cleared")
        self.save()

    def get_last_catch(self):
        """Get last catch coords and time for display."""
        with self._lock:
            return {
                "coords": self._data.get("last_catch_coords"),
                "time": self._data.get("last_catch_time", 0),
            }

    def is_in_cooldown(self):
        with self._lock:
            return time.time() < self._data.get("cooldown_until", 0)

    def cooldown_remaining(self):
        with self._lock:
            remaining = self._data.get("cooldown_until", 0) - time.time()
            return max(0, int(remaining))

    def add_event(self, event_type, message, details=None):
        with self._lock:
            self._add_event(event_type, message, details)
        self.save()

    def get_stats(self):
        with self._lock:
            data = dict(self._data)
            data["cooldown_remaining"] = self.cooldown_remaining()
            data["in_cooldown"] = self.is_in_cooldown()
            
            # Calculate time-based rates — use AVERAGE across total elapsed time,
            # not a rolling 60-minute window. This prevents stats from "resetting"
            # during an hour with no encounters.
            now = time.time()
            day_ago = now - 86400
            
            tp_ts = self._data.get("teleport_timestamps", [])
            hundo_ts = self._data.get("hundo_timestamps", [])
            shiny_ts = self._data.get("shiny_timestamps", [])
            shundo_ts = self._data.get("shundo_timestamps", [])
            
            # Use elapsed time for per-hour averages
            elapsed_secs = data.get("elapsed_seconds", 0)
            elapsed_hours = max(elapsed_secs / 3600, 0.0167)  # min 1 minute
            
            data["teleports_hour"] = round(len(tp_ts) / elapsed_hours, 1)
            data["teleports_day"] = sum(1 for t in tp_ts if t >= day_ago)
            data["hundos_hour"] = round(len(hundo_ts) / elapsed_hours, 1)
            data["hundos_day"] = sum(1 for t in hundo_ts if t >= day_ago)
            data["total_non_target_hundos"] = self._data.get("total_non_target_hundos", 0)
            data["shinies_hour"] = round(len(shiny_ts) / elapsed_hours, 1)
            data["shinies_day"] = sum(1 for t in shiny_ts if t >= day_ago)
            data["shundos_hour"] = round(len(shundo_ts) / elapsed_hours, 1)
            data["shundos_day"] = sum(1 for t in shundo_ts if t >= day_ago)
            
            # Percentages
            total_tp = self._data.get("total_teleports", 0)
            data["hundo_rate"] = round(self._data.get("total_hundos", 0) / total_tp * 100, 1) if total_tp > 0 else 0
            data["shiny_rate"] = round(self._data.get("total_shinies", 0) / total_tp * 100, 1) if total_tp > 0 else 0
            data["shundo_rate"] = round(self._data.get("total_shundos", 0) / total_tp * 100, 1) if total_tp > 0 else 0
            
            # Shiny probability: 1/500 = 0.2% per encounter
            data["shiny_chance"] = 0.2  # 1/500 as percentage
            
            # Probability of getting a shiny by now since last catch (cumulative): 1 - (499/500)^(N+1)
            # N+1 because the current check also counts — starts at 0.2% for first check
            hundos_since = self._data.get("hundos_since_catch", 0)
            data["cumulative_shiny_chance"] = round((1 - (499/500) ** (hundos_since + 1)) * 100, 2)
            
            # Average time-to-hundo
            intervals = self._data.get("hundo_intervals", [])
            if intervals:
                avg = sum(intervals) / len(intervals)
                data["avg_time_to_hundo"] = int(avg)
            else:
                data["avg_time_to_hundo"] = 0
            
            # Elapsed time since bot started — freezes when stopped
            start = self._data.get("start_time", 0)
            if start > 0:
                if self._is_running:
                    elapsed = int(time.time() - start)
                else:
                    stop = self._data.get("stop_time", 0)
                    if stop > 0:
                        elapsed = int(stop - start)
                    else:
                        elapsed = int(time.time() - start)
                data["elapsed_seconds"] = elapsed
                data["elapsed_display"] = f"{elapsed // 3600}h {(elapsed % 3600) // 60}m {elapsed % 60}s"
            else:
                data["elapsed_seconds"] = 0
                data["elapsed_display"] = "Not started"
            
            return data

    def get_events(self, limit=50):
        with self._lock:
            return list(self._data.get("events", [])[-limit:])

    def get_teleport_locations(self):
        with self._lock:
            return list(self._data.get("teleport_locations", []))

    def reset(self):
        with self._lock:
            # Preserve cooldown data across reset — catch cooldown should not be
            # cleared by reset stats.
            saved_last_catch_coords = self._data.get("last_catch_coords")
            saved_last_catch_time = self._data.get("last_catch_time", 0)
            saved_cooldown_until = self._data.get("cooldown_until", 0)

            # Full reset of stats
            self._data = self._default()
            self._logged_encounter_ids.clear()
            self._caught_encounter_ids.clear()

            # Restore cooldown data
            self._data["last_catch_coords"] = saved_last_catch_coords
            self._data["last_catch_time"] = saved_last_catch_time
            self._data["cooldown_until"] = saved_cooldown_until

            # Restart elapsed timer at 0 and mark as running
            self._data["start_time"] = time.time()
            self._data["stop_time"] = 0
            self._is_running = True
        self.save()


stats = StatsTracker()
