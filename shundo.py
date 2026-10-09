"""Guaranteed-shundo intake from PokeX DMs + catch-order planner.

PokeX DMs one message per Pokémon:
    <a:850:..> **Sizzlipede** <:Fire:..> 💯 <:LVL:..> **9** <:CP:..> **268** ♀ 🇫🇷 __Les Touches__ <:DSP:..> <t:1791507672:R> ✨
with a "📝 Copy" button. Clicking Copy makes PokeX send an EPHEMERAL reply
"`47.4528049, -1.4465087`" in the same DM.

The planner picks the catch order that yields the MOST catches before each
spawn's DSP reaches 0, using the distance cooldown chart from stats.py.
It is an exact search (no cap on how many shundos are pending); a wall-clock
safety budget keeps it from ever stalling the bot.
"""

import json
import os
import re
import time
import unicodedata

from stats import haversine_km, get_cooldown_for_distance, MAX_COOLDOWN

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(SCRIPT_DIR, "shundo_state.json")
LOG_PATH = os.path.join(SCRIPT_DIR, "shundo_log.jsonl")

_RE_NAME = re.compile(r"\*\*([^*]+?)\*\*")
_RE_LVL = re.compile(r"<:LVL:\d+>\s*\*\*(\d+)\*\*")
_RE_CP = re.compile(r"<:CP:\d+>\s*\*\*(\d+)\*\*")
_RE_CITY = re.compile(r"__(.+?)__")
_RE_DSP = re.compile(r"<t:(\d+)(?::[a-zA-Z])?>")
_RE_COORDS = re.compile(r"(-?\d{1,3}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)")


def normalize_species(name):
    """'Flabébé' -> 'flabebe', 'Mr. Mime' -> 'mr-mime'."""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s


def parse_alert(content):
    """Parse one PokeX alert message. Returns dict or None if not an alert."""
    if not content or "<t:" not in content:
        return None
    dsp = _RE_DSP.search(content)
    name = _RE_NAME.search(content)
    if not dsp or not name:
        return None
    lvl = _RE_LVL.search(content)
    cp = _RE_CP.search(content)
    city = _RE_CITY.search(content)
    gender = "♀" if "♀" in content else ("♂" if "♂" in content else "")
    raw_name = name.group(1).strip()
    return {
        "name": raw_name,
        "species": normalize_species(raw_name),
        "level": int(lvl.group(1)) if lvl else None,
        "cp": int(cp.group(1)) if cp else None,
        "city": city.group(1).strip() if city else "",
        "gender": gender,
        "expires_at": float(dsp.group(1)),
    }


def parse_coords(content):
    m = _RE_COORDS.search(content or "")
    if not m:
        return None
    lat, lng = float(m.group(1)), float(m.group(2))
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None
    return f"{lat},{lng}"


def _latlng(coords):
    a, b = coords.replace(",", " ").split()[:2]
    return float(a), float(b)


def _species_in(species, names):
    sp = species.replace("-", " ")
    for t in names or []:
        tn = normalize_species(t).replace("-", " ")
        if not tn:
            continue
        if sp == tn or re.search(r"\b" + re.escape(tn) + r"\b", sp):
            return True
    return False


def species_matches_targets(species, targets, skipped=None):
    """Target list + skip (ignore) list rules:
    - a skipped species never matches
    - hunt list = targets minus skipped; if that's empty, everything else matches
    """
    skipped = skipped or []
    if _species_in(species, skipped):
        return False
    skip_norm = {normalize_species(x) for x in skipped}
    hunt = [t for t in (targets or []) if normalize_species(t) not in skip_norm]
    if not hunt:
        return True
    return _species_in(species, hunt)


# ─────────────────────────── planner ───────────────────────────

def _catch_time(free_at, last_pos, last_time, target_pos, overhead):
    """Earliest time a catch at target_pos can complete."""
    if last_pos is None or last_time is None:
        ready = free_at
    else:
        cd = get_cooldown_for_distance(haversine_km(last_pos[0], last_pos[1], target_pos[0], target_pos[1]))
        ready = max(free_at, last_time + cd) if (free_at - last_time) < MAX_COOLDOWN else free_at
    return ready + overhead


def plan_route(items, last_catch, last_catch_time, now, overhead=60, budget_s=5.0):
    """Exact max-catch ordering.

    items: list of (key, (lat,lng), expires_at)
    last_catch: (lat,lng) or None;  last_catch_time: epoch or None
    Returns (order_keys, finish_time, exhaustive: bool)
    """
    n = len(items)
    if n == 0:
        return [], now, True
    pos = [it[1] for it in items]
    exp = [it[2] for it in items]
    deadline = time.time() + budget_s

    # Seed with greedy (nearest catchable next) so a budget cut-off still has a good answer
    def greedy():
        order, free, lp, lt = [], now, last_catch, last_catch_time
        left = set(range(n))
        while True:
            best = None
            for j in left:
                ct = _catch_time(free, lp, lt, pos[j], overhead)
                if ct <= exp[j] and (best is None or ct < best[0]):
                    best = (ct, j)
            if not best:
                return order, free
            ct, j = best
            order.append(j); left.discard(j)
            free, lp, lt = ct, pos[j], ct

    g_order, g_finish = greedy()
    best = {"count": len(g_order), "finish": g_finish, "order": list(g_order)}
    seen = {}  # (last_idx, frozenset(done)) -> earliest time reached
    exhaustive = [True]

    def dfs(order, done, free, lp, lt, last_idx):
        if time.time() > deadline:
            exhaustive[0] = False
            return
        key = (last_idx, done)
        prev = seen.get(key)
        if prev is not None and prev <= free:
            return
        seen[key] = free
        cnt = len(order)
        if cnt > best["count"] or (cnt == best["count"] and free < best["finish"]):
            best.update(count=cnt, finish=free, order=list(order))
        # Upper bound: every remaining item that hasn't despawned by the soonest possible catch
        ub = cnt + sum(1 for j in range(n) if j not in done and exp[j] >= free + overhead)
        if ub < best["count"] or (ub == best["count"] and ub == cnt):
            return
        cands = []
        for j in range(n):
            if j in done:
                continue
            ct = _catch_time(free, lp, lt, pos[j], overhead)
            if ct <= exp[j]:
                cands.append((ct, j))
        cands.sort()
        for ct, j in cands:
            order.append(j)
            dfs(order, done | {j}, ct, pos[j], ct, j)
            order.pop()
            if not exhaustive[0]:
                return

    dfs([], frozenset(), now, last_catch, last_catch_time, -1)
    return [items[j][0] for j in best["order"]], best["finish"], exhaustive[0]


# ─────────────────────────── manager ───────────────────────────

class ShundoManager:
    """Tracks PokeX shundo alerts and decides which one to teleport to next."""

    def __init__(self):
        self.entries = {}          # msg_id(str) -> dict
        self.processed_ids = []    # message ids already handled (persisted)
        self.caught_keys = []      # species@lat4,lng4 already caught (persisted)
        self._plan_cache = None    # (signature, order, exhaustive, computed_at)
        self.last_plan = []
        self.last_plan_exhaustive = True
        self._load()

    # ── persistence ──
    def _load(self):
        try:
            if os.path.exists(STATE_PATH):
                with open(STATE_PATH) as f:
                    d = json.load(f)
                self.processed_ids = d.get("processed_ids", [])[-2000:]
                self.caught_keys = d.get("caught_keys", [])[-2000:]
        except Exception as e:
            print(f"[Shundo] Could not load state: {e}")

    def _save(self):
        try:
            tmp = STATE_PATH + ".tmp"
            with open(tmp, "w") as f:
                json.dump({"processed_ids": self.processed_ids[-2000:],
                           "caught_keys": self.caught_keys[-2000:]}, f)
            os.replace(tmp, STATE_PATH)
        except Exception as e:
            print(f"[Shundo] Could not save state: {e}")

    def log(self, event, entry=None, **extra):
        rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "event": event}
        if entry:
            rec.update({k: entry.get(k) for k in ("msg_id", "name", "species", "cp", "level", "city", "coords", "expires_at", "status")})
        rec.update(extra)
        try:
            with open(LOG_PATH, "a") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            pass

    # ── keys ──
    @staticmethod
    def catch_key(species, coords):
        try:
            lat, lng = _latlng(coords)
            return f"{species}@{lat:.4f},{lng:.4f}"
        except Exception:
            return f"{species}@?"

    def is_processed(self, msg_id):
        return str(msg_id) in self.processed_ids

    def mark_processed(self, msg_id):
        sid = str(msg_id)
        if sid not in self.processed_ids:
            self.processed_ids.append(sid)
            self._save()

    # ── intake ──
    def add(self, msg_id, alert, channel_id):
        sid = str(msg_id)
        if sid in self.entries:
            return self.entries[sid]
        e = dict(alert)
        e.update(msg_id=sid, channel_id=channel_id, coords=None, status="pending_coords",
                 attempts=0, received_at=time.time())
        self.entries[sid] = e
        self._plan_cache = None
        self.log("received", e)
        return e

    def set_coords(self, msg_id, coords):
        e = self.entries.get(str(msg_id))
        if not e:
            return None
        e["coords"] = coords
        if self.catch_key(e["species"], coords) in self.caught_keys:
            e["status"] = "already_caught"
            self.log("skipped_already_caught", e)
        else:
            e["status"] = "pending"
            self.log("coords", e)
        self._plan_cache = None
        return e

    def set_status(self, msg_id, status, **extra):
        e = self.entries.get(str(msg_id))
        if not e:
            return
        e["status"] = status
        self._plan_cache = None
        self.log(status, e, **extra)

    def mark_caught(self, msg_id):
        e = self.entries.get(str(msg_id))
        if not e:
            return
        e["status"] = "caught"
        if e.get("coords"):
            k = self.catch_key(e["species"], e["coords"])
            if k not in self.caught_keys:
                self.caught_keys.append(k)
                self._save()
        self._plan_cache = None
        self.log("caught", e)

    def mark_caught_by_name(self, species, coords):
        """Called when the background scanner sees a catch. Matches a pending/processing
        shundo of the same species within ~2 km of the catch coords."""
        sp = normalize_species(species)
        best = None
        for e in self.entries.values():
            if e["status"] not in ("pending", "processing") or not e.get("coords") or e["species"] != sp:
                continue
            d = 0.0
            if coords:
                try:
                    a, b = _latlng(coords); c, d2 = _latlng(e["coords"])
                    d = haversine_km(a, b, c, d2)
                except Exception:
                    d = 0.0
            if d <= 2.0 and (best is None or d < best[0]):
                best = (d, e)
        if best:
            self.mark_caught(best[1]["msg_id"])
            return best[1]
        return None

    # ── housekeeping ──
    def expire(self, now=None):
        """Drop anything whose DSP has reached 0."""
        now = now or time.time()
        dropped = []
        for e in self.entries.values():
            if e["status"] in ("pending", "pending_coords") and e["expires_at"] <= now:
                e["status"] = "expired"
                self._plan_cache = None
                self.log("expired", e)
                dropped.append(e)
        # Forget finished entries after 2h so memory stays small
        for k in [k for k, e in self.entries.items()
                  if e["status"] not in ("pending", "pending_coords", "processing") and e["expires_at"] < now - 7200]:
            del self.entries[k]
        return dropped

    def pending(self):
        return [e for e in self.entries.values() if e["status"] == "pending" and e.get("coords")]

    def has_active(self):
        now = time.time()
        return any(e["status"] in ("pending", "pending_coords", "processing") and e["expires_at"] > now
                   for e in self.entries.values())

    # ── planning ──
    def plan(self, last_catch_coords, last_catch_time, overhead=60, budget_s=5.0, force=False):
        """Return ordered list of pending entries (best catch order). Marks unreachable ones."""
        now = time.time()
        pend = self.pending()
        lc = (last_catch_coords["lat"], last_catch_coords["lng"]) if last_catch_coords else None
        lct = last_catch_time or None
        sig = (tuple(sorted(e["msg_id"] for e in pend)), lc, lct)
        if (not force and self._plan_cache and self._plan_cache[0] == sig
                and now - self._plan_cache[3] < 30):
            order_ids, exhaustive = self._plan_cache[1], self._plan_cache[2]
        else:
            items = [(e["msg_id"], _latlng(e["coords"]), e["expires_at"]) for e in pend]
            order_ids, _finish, exhaustive = plan_route(items, lc, lct, now, overhead, budget_s)
            self._plan_cache = (sig, order_ids, exhaustive, now)
            # Items left out of the plan stay pending (they're only removed when DSP hits 0);
            # log once when one can't be reached in time from the current catch spot.
            for e in pend:
                e["in_plan"] = e["msg_id"] in order_ids
                if e["in_plan"]:
                    continue
                ct = _catch_time(now, lc, lct, _latlng(e["coords"]), overhead)
                e["reachable"] = ct <= e["expires_at"]
                if not e["reachable"] and not e.get("_unreach_logged"):
                    e["_unreach_logged"] = True
                    self.log("out_of_reach", e, needed_at=int(ct), dsp_left=int(e["expires_at"] - now))
            if order_ids:
                names = [self.entries[i]["name"] for i in order_ids]
                print(f"[Shundo] Plan ({'exact' if exhaustive else 'best-found'}): {' → '.join(names)}")
                self.log("plan", None, order=names, exact=exhaustive, pending=len(pend))
        self.last_plan = [self.entries[i] for i in order_ids if i in self.entries and self.entries[i]["status"] == "pending"]
        self.last_plan_exhaustive = exhaustive
        return self.last_plan
