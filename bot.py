"""Discord self-bot with command handling, keyword targeting, and worker loop."""

import discord
import asyncio
import re
import shlex
import time
import random
import json
import os

from config import config
from stats import stats, haversine_km
from queue_manager import queue, TargetTask
from browser import SXBrowser
from shundo import ShundoManager, parse_alert, parse_coords, species_matches_targets, normalize_species, _species_in, base_species
from pokemon_data import is_valid_pokemon, get_sprite_url

# Path for channel success rate log
CHANNEL_STATS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "channel_stats.json")


def display_name(name):
    """Capitalize Pokemon name for display."""
    return name.replace("-", " ").title()


class PokeBot:
    """Main bot class managing Discord, queue processing, and browser automation."""

    def __init__(self):
        self.client = discord.Client()
        self.browser = None
        self.running = False
        self.skip_current = False
        self.paused = False
        self.worker_task = None
        self.current_activity = "Idle"
        self.loop_step = 0  # 0=idle, 1=watching, 2=extracting, 3=teleport, 4=walk, 5=monitor, 6=cooldown
        self.current_coords = None  # Current target coordinates for cooldown display
        # Initialize from last known catch coords (survives restarts)
        _last_cc = stats._data.get("last_catch_coords")
        if _last_cc:
            self.current_coords = f"{_last_cc['lat']},{_last_cc['lng']}"
        self._coord_response_future = None  # Future for receiving coord responses from button clicks
        self._reveal_coords_lock = asyncio.Lock()  # Serialize Reveal Coords clicks to prevent race conditions
        self._recent_coords = {}  # coords_str -> expiry timestamp, prevents re-teleporting to same location
        self._last_message_time = time.time()  # Track last Discord message for staleness detection
        self._bg_scanner_task = None  # Background log scanner
        self._bg_baseline_lines = set()  # Baseline for background scanner
        self._bg_scanned_encounter_ids = set()  # Dedup for background scanner
        self._queue_refilled = set()  # Pokemon names that have hit queue cap; only refill when empty
        self._channel_stats = {}  # channel_id -> {found, no_result, caught, fled, skipped, total}
        self._last_encounter_coords = None  # Coords from last BgScanner hundo encounter for catch cooldown
        self._consecutive_no_spawn = 0  # Counter for consecutive no-spawn teleports
        self._last_sx_restart_time = 0  # Timestamp of last SX game restart
        # Guaranteed-shundo (PokeX DM) state
        self.shundos = ShundoManager()
        self._copy_lock = asyncio.Lock()  # Serialize PokeX Copy clicks
        self._pokex_coord_future = None  # Resolved by the ephemeral coords reply
        self._shundo_task_counter = 0
        # Where the player actually was over time: [(epoch, "lat,lng"), ...]
        # Used to attribute logged catches/flees to the right spot (the background
        # scanner can see a catch 15 s+ late, after the bot already teleported elsewhere).
        self._pos_history = []
        if self.current_coords:
            self._pos_history.append((stats._data.get("last_catch_time") or time.time(), self.current_coords))
        self._load_channel_stats()
        self._setup_events()

    async def _send(self, channel, message):
        """Send a Discord message with a random 2-7s delay to appear human."""
        delay = random.randint(
            config.get("dm_delay_min_seconds", 2),
            config.get("dm_delay_max_seconds", 7)
        )
        await asyncio.sleep(delay)
        await channel.send(message)

    async def _dm(self, user, message):
        """Send a DM with a random 2-7s delay."""
        delay = random.randint(
            config.get("dm_delay_min_seconds", 2),
            config.get("dm_delay_max_seconds", 7)
        )
        await asyncio.sleep(delay)
        await user.send(message)

    def _setup_events(self):
        client = self.client

        @client.event
        async def on_ready():
            print(f"[Discord] Logged in as {client.user} (ID: {client.user.id})")
            self._last_message_time = time.time()
            if self.browser is not None:
                print("[Discord] Reconnected; browser already running.")
                return
            print("[Discord] Starting browser...")
            self.browser = SXBrowser(config)
            await self.browser.start()
            print("[Discord] Browser ready!")
            self._print_banner()
            # Auto-start background log scanner for passive hundo/shiny tracking
            if self._bg_scanner_task is None or self._bg_scanner_task.done():
                self._bg_scanner_task = asyncio.create_task(self._background_log_scanner())
            # Pick up any recent PokeX shundo alerts that arrived while we were offline
            asyncio.create_task(self._shundo_startup_backfill())

        @client.event
        async def on_disconnect():
            print("[Discord] Connection lost! Attempting reconnection...")

        @client.event
        async def on_resumed():
            print("[Discord] Connection resumed.")
            self._last_message_time = time.time()

        @client.event
        async def on_message(message):
            self._last_message_time = time.time()
            await self._on_message(message)

    def _print_banner(self):
        print()
        print("=" * 60)
        print("  PokeGo Teleport Bot v3.0")
        print("=" * 60)
        print("  Browser is open. Log in to SX dashboard if needed.")
        print()
        print("  DISCORD COMMANDS (mention your account):")
        print("    @bot add target <pokemon>       Add Pokemon to hunt")
        print("    @bot remove target <pokemon>    Remove Pokemon from hunt")
        print("    @bot targets                    Show target list")
        print("    @bot queue                      Show current queue")
        print("    @bot clear queue                Clear the queue")
        print("    @bot stats                      Show statistics")
        print("    @bot caught                     Show caught Pokemon")
        print("    @bot channels                   Show per-channel success rates")
        print("    @bot shundos                    Show PokeX shundo plan")
        print("    @bot skip <pokemon,...>         Ignore Pokémon (unskip to undo)")
        print("    @bot start                      Start monitoring")
        print("    @bot stop                       Stop monitoring")
        print("    @bot status                     Show current status")
        print("    @bot set <key> <value>          Update settings")
        print("    @bot help                       Show all commands")
        print()
        print("  Web Dashboard: http://127.0.0.1:%d" % config.get("web_port", 8765))
        print("=" * 60)
        print()

    async def _on_message(self, message):
        client = self.client

        # PokeX guaranteed-shundo DMs (alerts + ephemeral Copy replies) are handled
        # separately and never fall through to the channel/Reveal Coords logic.
        if self._is_pokex_message(message):
            waiting = self._pokex_coord_future is not None and not self._pokex_coord_future.done()
            ephemeral = bool(getattr(getattr(message, "flags", None), "ephemeral", False))
            if self._in_pokex_dm(message) or (waiting and ephemeral):
                await self._on_pokex_message(message)
                return

        # Check if this is a coordinate response from a "Reveal Coords" button click
        if self._coord_response_future and not self._coord_response_future.done():
            content = (message.content or "").strip()
            # Try exact match: "lat,lng"
            if re.match(r'^-?\d+\.\d+,\s*-?\d+\.\d+$', content):
                self._coord_response_future.set_result(content)
                return
            # Try to find coordinates anywhere in the message text
            coord_match = re.search(r'(-?\d+\.\d+),\s*(-?\d+\.\d+)', content)
            if coord_match:
                coords = f"{coord_match.group(1)},{coord_match.group(2)}"
                self._coord_response_future.set_result(coords)
                return
            # Check embeds for coordinates (some bots respond with embeds)
            for embed in message.embeds:
                embed_text = ((embed.description or "") + " " + (embed.title or ""))
                for field in embed.fields:
                    embed_text += " " + (field.name or "") + " " + (field.value or "")
                coord_match = re.search(r'(-?\d+\.\d+),\s*(-?\d+\.\d+)', embed_text)
                if coord_match:
                    coords = f"{coord_match.group(1)},{coord_match.group(2)}"
                    self._coord_response_future.set_result(coords)
                    return
            # Log what we received but couldn't match (for debugging)
            if content and len(content) > 0:
                print(f"[Monitor] Non-coord message while waiting: {content[:80]}")
            elif message.embeds:
                for embed in message.embeds:
                    embed_text = ((embed.description or "") + " " + (embed.title or ""))
                    for field in embed.fields:
                        embed_text += " " + (field.name or "") + " " + (field.value or "")
                    if embed_text.strip():
                        print(f"[Monitor] Non-coord embed while waiting: {embed_text[:80]}")
                        break

        # Self-bot fix: only ignore messages from our account that don't mention us
        if (
            message.author.id == client.user.id
            and client.user.id not in [m.id for m in message.mentions]
        ):
            return

        watch_channel_id = config.get("watch_channel_id")
        additional_channels = config.get("additional_watch_channels", [])
        watch_channels = set()
        if watch_channel_id:
            watch_channels.add(str(watch_channel_id))
        for ch in additional_channels:
            watch_channels.add(str(ch))
        is_watch_channel = str(message.channel.id) in watch_channels

        # Check if this is a command (mentions our account)
        if client.user and client.user.id in [m.id for m in message.mentions]:
            controller_id = config.get("controller_user_id")
            if controller_id and message.author.id == int(controller_id):
                content = re.sub(r'<@!?\d+>', '', message.content).strip()
                try:
                    parts = shlex.split(content)
                except ValueError:
                    parts = content.split()
                if parts and parts[0].lower() in (
                    "add", "remove", "targets", "queue", "clear", "stats",
                    "caught", "start", "stop", "status", "set", "help", "priority",
                    "channels", "shundos", "skip", "unskip",
                ):
                    await self._handle_command(message, parts)
                    return

        # If running and in the watch channel, check for target Pokemon keywords
        # (channel feeds can be switched off with channel_watch_enabled=false; the
        # channel list is kept so they can be turned back on later)
        if self.running and is_watch_channel and config.get("channel_watch_enabled", True):
            await self._check_for_targets(message)

    # ────────────────────── PokeX guaranteed shundos ──────────────────────

    def _is_pokex_message(self, message):
        if not config.get("shundo_dm_enabled", True):
            return False
        bot_id = str(config.get("pokex_bot_id", ""))
        return bool(bot_id) and str(message.author.id) == bot_id

    def _in_pokex_dm(self, message):
        dm_id = str(config.get("shundo_dm_channel_id", ""))
        return bool(dm_id) and str(message.channel.id) == dm_id

    async def _on_pokex_message(self, message):
        content = message.content or ""
        # 1) Ephemeral reply to a Copy click -> coordinates
        if self._pokex_coord_future and not self._pokex_coord_future.done():
            coords = parse_coords(content)
            if coords and "<t:" not in content:
                self._pokex_coord_future.set_result(coords)
                return
        # 2) A new alert in the PokeX DM
        if not self._in_pokex_dm(message):
            return
        alert = parse_alert(content)
        if alert:
            asyncio.create_task(self._handle_pokex_alert(message, alert))

    async def _handle_pokex_alert(self, message, alert):
        """Register a shundo alert and fetch its coordinates via the Copy button."""
        if self.shundos.is_processed(message.id):
            return
        self.shundos.mark_processed(message.id)
        now = time.time()
        left = int(alert["expires_at"] - now)
        if left <= 0:
            print(f"[Shundo] {alert['name']} already despawned — ignoring")
            return
        if not species_matches_targets(alert["species"], config.get_targets(), config.get_skipped()):
            why = "on skip list" if _species_in(alert["species"], config.get_skipped()) else "not in target list"
            print(f"[Shundo] {alert['name']} {why} — skipping")
            self.shundos.log("skipped_not_target", dict(alert, msg_id=str(message.id)))
            return
        entry = self.shundos.add(message.id, alert, message.channel.id)
        print(f"[Shundo] Alert: {alert['name']} L{alert['level']} CP{alert['cp']} {alert['city']} — DSP {left // 60}m {left % 60}s")
        stats.add_event("shundo", f"PokeX shundo alert: {alert['name']} ({alert['city']}, DSP {left // 60}m)")
        coords = None
        for attempt in range(2):
            coords = await self._click_pokex_copy(message)
            if coords:
                break
            await asyncio.sleep(3)
        if not coords:
            self.shundos.set_status(message.id, "no_coords")
            print(f"[Shundo] Could not get coordinates for {alert['name']}")
            return
        e = self.shundos.set_coords(message.id, coords)
        print(f"[Shundo] {alert['name']} coords {coords} ({e['status']})")

    async def _click_pokex_copy(self, message):
        """Click the PokeX 'Copy' button and wait for the ephemeral coordinates reply."""
        async with self._copy_lock:
            button = None
            for row in message.components or []:
                for comp in getattr(row, "children", []):
                    label = getattr(comp, "label", "") or ""
                    if "copy" in label.lower():
                        button = comp
                        break
                if button:
                    break
            if not button:
                print("[Shundo] No Copy button on alert message")
                return None
            loop = asyncio.get_event_loop()
            self._pokex_coord_future = loop.create_future()
            try:
                try:
                    await button.click()
                except Exception as e:
                    print(f"[Shundo] Copy click failed: {e}")
                    return None
                try:
                    return await asyncio.wait_for(self._pokex_coord_future, timeout=15.0)
                except asyncio.TimeoutError:
                    print("[Shundo] Timed out waiting for PokeX coordinates")
                    return None
            finally:
                self._pokex_coord_future = None
                await asyncio.sleep(2)  # be gentle with Discord interactions

    async def _shundo_startup_backfill(self):
        """On startup, process recent PokeX alerts that haven't despawned yet."""
        if not config.get("shundo_dm_enabled", True):
            return
        dm_id = config.get("shundo_dm_channel_id")
        if not dm_id:
            print("[Shundo] shundo_dm_channel_id not set — DM intake disabled")
            return
        await asyncio.sleep(5)
        try:
            channel = self.client.get_channel(int(dm_id)) or await self.client.fetch_channel(int(dm_id))
            found = []
            async for m in channel.history(limit=30):
                if not self._is_pokex_message(m) or self.shundos.is_processed(m.id):
                    continue
                alert = parse_alert(m.content or "")
                if alert and alert["expires_at"] > time.time() + 60:
                    found.append((m, alert))
            if found:
                print(f"[Shundo] Startup: {len(found)} unexpired PokeX alert(s) to process")
            for m, alert in reversed(found):  # oldest first
                await self._handle_pokex_alert(m, alert)
        except Exception as e:
            print(f"[Shundo] Startup backfill error: {e}")

    def _make_shundo_task(self, entry):
        self._shundo_task_counter += 1
        t = TargetTask(
            priority=0,
            expires_at=entry["expires_at"],
            created_at=entry.get("received_at", time.time()),
            pokemon=entry["species"],
            url="",
            message_id=0,
            channel_id=entry.get("channel_id", 0),
            dsp_minutes=max(0, int((entry["expires_at"] - time.time()) // 60)),
            coords=entry["coords"],
            status="processing",
            shiny=True,
            iv_info="100%",
            _counter_key=-1000000 - self._shundo_task_counter,  # never collides with queue keys
        )
        t.shundo_id = entry["msg_id"]
        return t

    def _shundo_select(self):
        """Pick the next shundo per the max-catch plan.
        Returns (task, coords, wait_info) — task None + wait_info when we must wait for cooldown."""
        self.shundos.expire()
        targets, skipped = config.get_targets(), config.get_skipped()
        for e in self.shundos.entries.values():
            ok = species_matches_targets(e["species"], targets, skipped)
            if e["status"] in ("pending", "pending_coords") and not ok:
                e["status_before_skip"] = e["status"]
                e["status"] = "skipped_list"
                self.shundos._plan_cache = None
                print(f"[Shundo] {e['name']} now excluded by target/skip list — dropped from plan")
            elif e["status"] == "skipped_list" and ok:
                e["status"] = e.pop("status_before_skip", "pending")
                self.shundos._plan_cache = None
                print(f"[Shundo] {e['name']} allowed again by target/skip list — back in plan")
        if not self.shundos.pending():
            return None, None, None
        plan = self.shundos.plan(
            stats._data.get("last_catch_coords"),
            stats._data.get("last_catch_time"),
            overhead=config.get("shundo_catch_overhead_seconds", 60),
            budget_s=config.get("shundo_plan_budget_seconds", 5),
            anchors=stats.get_cooldown_anchors(),
        )
        if not plan:
            return None, None, None
        nxt = plan[0]
        cd = stats.get_catch_cooldown_for_target(nxt["coords"])
        if cd > 0:
            dist = self._distance_from_last_catch(nxt["coords"]) or 0
            return None, None, (nxt, cd, dist, len(plan))
        nxt["status"] = "processing"
        nxt["attempts"] = nxt.get("attempts", 0) + 1
        return self._make_shundo_task(nxt), nxt["coords"], None

    def _shundo_finish(self, task, mon_status, caught):
        sid = getattr(task, "shundo_id", None)
        if not sid:
            return
        e = self.shundos.entries.get(sid)
        if not e:
            return
        if caught or e["status"] == "caught":
            self.shundos.mark_caught(sid)
            print(f"[Shundo] {e['name']} caught — re-planning from here")
            return
        # Not caught: one retry if the spawn simply didn't show up and DSP is left
        if (mon_status in ("no_result", "found") and e.get("attempts", 0) < 2
                and e["expires_at"] - time.time() > config.get("shundo_catch_overhead_seconds", 60)):
            e["status"] = "pending"
            self.shundos._plan_cache = None
            self.shundos.log("retry", e, result=mon_status)
            print(f"[Shundo] {e['name']}: {mon_status} — will retry once")
        else:
            self.shundos.set_status(sid, "missed", result=mon_status)
            print(f"[Shundo] {e['name']}: {mon_status} — marked missed")

    def _shundo_report(self):
        now = time.time()
        self.shundos.expire()
        lines = ["**PokeX Shundos**"]
        if not config.get("shundo_dm_enabled", True):
            lines.append("DM intake is disabled (`shundo_dm_enabled`).")
        plan = self.shundos.plan(stats._data.get("last_catch_coords"), stats._data.get("last_catch_time"),
                                 overhead=config.get("shundo_catch_overhead_seconds", 60),
                                 budget_s=config.get("shundo_plan_budget_seconds", 5),
                                 anchors=stats.get_cooldown_anchors())
        if plan:
            lines.append(f"Plan ({'exact' if self.shundos.last_plan_exhaustive else 'best found'}):")
            for i, e in enumerate(plan, 1):
                left = int(e["expires_at"] - now)
                cd = stats.get_catch_cooldown_for_target(e["coords"])
                dist = self._distance_from_last_catch(e["coords"]) or 0
                lines.append(f"{i}. **{e['name']}** — {e['city']} • DSP {left // 60}m • {dist:.0f}km • "
                             f"{'ready' if cd <= 0 else f'cooldown {cd // 60}m {cd % 60}s'}")
        else:
            lines.append("No shundos planned right now.")
        planned_ids = {e["msg_id"] for e in plan}
        for e in self.shundos.entries.values():
            if e["status"] in ("pending_coords", "processing"):
                lines.append(f"• {e['name']} — {e['status'].replace('_', ' ')}")
            elif e["status"] == "pending" and e["msg_id"] not in planned_ids:
                left = int(e["expires_at"] - now)
                why = "can't reach before DSP ends" if e.get("reachable") is False else "not in best plan"
                lines.append(f"• {e['name']} ({e['city']}) — {why} • DSP {left // 60}m")
        recent = sorted([e for e in self.shundos.entries.values()
                         if e["status"] in ("caught", "missed", "expired", "already_caught", "no_coords")],
                        key=lambda e: -e.get("received_at", 0))[:8]
        if recent:
            lines.append("Recent:")
            for e in recent:
                lines.append(f"• {e['name']} ({e['city']}) — {e['status'].replace('_', ' ')}")
        lines.append(f"Channel feeds: {'on' if config.get('channel_watch_enabled', True) else 'off'}")
        return "\n".join(lines)[:1900]

    # ───────────── position history + SX log timestamps ─────────────
    def _record_position(self, coords, t=None):
        if not coords:
            return
        self._pos_history.append((t or time.time(), coords))
        if len(self._pos_history) > 500:
            self._pos_history = self._pos_history[-300:]

    def _coords_at(self, t):
        """Where the player was at epoch t (last teleport at or before t)."""
        best = None
        for pt, c in self._pos_history:
            if pt <= t + 1:
                best = c
            else:
                break
        return best

    @staticmethod
    def _log_line_epoch(line, now=None):
        """SX log lines start with [HH:MM:SS] (server local time). Returns epoch or None."""
        m = re.match(r"\s*\[(\d{1,2}):(\d{2}):(\d{2})\]", line or "")
        if not m:
            return None
        import datetime as _dt
        now = now or time.time()
        n = _dt.datetime.fromtimestamp(now)
        t = n.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=int(m.group(3)), microsecond=0)
        ep = t.timestamp()
        if ep > now + 300:  # line from before midnight
            ep -= 86400
        return ep

    def _find_catch_outcome(self, lines, dex, species, since):
        """Scan SX log lines for this Pokémon's [CatchPokemon] outcome after `since`.
        Returns (outcome, epoch, encounter_id) with outcome 'caught' / 'fled' / None."""
        sp = base_species(species).replace("-", " ")
        tag = f"(#{dex})" if dex else None
        enc_id = None
        outcome = None
        when = None
        for line in sorted(lines, key=lambda l: self._log_line_epoch(l) or 0):
            ll = line.lower()
            t = self._log_line_epoch(line)
            if t is None or t < since - 2:
                continue
            hit = (tag and tag in ll) or (sp and sp in ll.replace("-", " ").replace(".", ""))
            if not hit:
                continue
            if "[normalencounter]" in ll and "encounterid:" in ll:
                m = re.search(r"EncounterId:\s*(\d+)", line)
                if m:
                    enc_id = m.group(1)
            if "[catchpokemon]" in ll:
                after = ll.split("[catchpokemon]", 1)[1].strip()
                if after.startswith("caught"):
                    outcome, when = "caught", t
                elif " fled" in after:
                    outcome, when = "fled", t
                # "escaped!" = ball broke out, SX keeps throwing — not final
        return outcome, when, enc_id

    async def _confirm_shundo_outcome(self, task, baseline_lines, since, wait_s=30):
        """After a shundo teleport, wait for SX's own [CatchPokemon] verdict."""
        from pokemon_data import get_pokemon_id
        dex = get_pokemon_id(base_species(task.pokemon))
        deadline = time.time() + wait_s
        while True:
            try:
                text = await self.browser.read_logs()
            except Exception:
                text = ""
            lines = [l for l in (text or "").splitlines() if l not in baseline_lines]
            outcome, when, enc_id = self._find_catch_outcome(lines, dex, task.pokemon, since)
            if outcome or time.time() > deadline:
                return outcome, when, enc_id
            self.current_activity = f"Confirming catch for {display_name(task.pokemon)}…"
            await asyncio.sleep(3)

    def _load_channel_stats(self):
        """Load per-channel success rate stats from disk."""
        try:
            if os.path.exists(CHANNEL_STATS_PATH):
                with open(CHANNEL_STATS_PATH, "r") as f:
                    self._channel_stats = json.load(f)
        except Exception as e:
            print(f"[ChannelStats] Error loading: {e}")
            self._channel_stats = {}

    def _save_channel_stats(self):
        """Save per-channel success rate stats to disk."""
        try:
            with open(CHANNEL_STATS_PATH, "w") as f:
                json.dump(self._channel_stats, f, indent=2)
        except Exception as e:
            print(f"[ChannelStats] Error saving: {e}")

    def _record_channel_result(self, channel_id, result):
        """Record a monitoring result for a channel."""
        ch_key = str(channel_id) if channel_id else "unknown"
        if ch_key not in self._channel_stats:
            self._channel_stats[ch_key] = {"found": 0, "no_result": 0, "caught": 0, "fled": 0, "skipped": 0, "total": 0}
        if result in self._channel_stats[ch_key]:
            self._channel_stats[ch_key][result] += 1
        self._channel_stats[ch_key]["total"] += 1
        self._save_channel_stats()

    def _purge_expired_tasks(self):
        """Remove expired tasks from the queue. Call before selecting a task."""
        all_tasks = queue.peek_all()
        purged = 0
        for task in all_tasks:
            if task.is_expired:
                stats.record_expired(task.pokemon)
                queue.mark_done(task)
                purged += 1
        if purged:
            print(f"[Worker] Purged {purged} expired task(s) from queue")
        return purged

    async def _handle_command(self, message, parts):
        """Parse and execute a Discord command."""
        cmd = parts[0].lower()
        args = parts[1:]

        if cmd == "help":
            await self._send(message.channel, 
                "**Commands:**\n"
                "`@bot add target <pokemon1, pokemon2, ...>` - Add Pokemon (comma-separated)\n"
                "`@bot add target <pokemon> high` - Add as high priority\n"
                "`@bot remove target <pokemon>` - Remove Pokemon from hunt\n"
                "`@bot priority <pokemon> high/low` - Set priority level\n"
                "`@bot targets` - Show target list with priorities\n"
                "`@bot queue` - Show current queue\n"
                "`@bot clear queue` - Clear the queue\n"
                "`@bot stats` - Show statistics\n"
                "`@bot caught` - Show caught Pokemon\n"
                "`@bot channels` - Show per-channel success rates\n"
                "`@bot shundos` - Show pending PokeX shundos and the catch plan\n"
                "`@bot skip <pokemon, ...>` / `@bot unskip <pokemon>` - Ignore list (all targets skipped = hunt everything else)\n"
                "`@bot start` - Start monitoring\n"
                "`@bot stop` - Stop monitoring\n"
                "`@bot status` - Show current status\n"
                "`@bot set <key> <value>` - Update settings\n"
                "  Keys: `watch`, `cooldown`, `walk_after_teleport`, `auto_remove`, "
                "`monitor_timeout`, `walk_distance`, `notify`, `skip_non_shiny`, "
                "`queue_limit`\n"
                "`@bot help` - Show this help"
            )

        elif cmd == "add":
            if len(args) >= 2 and args[0].lower() == "target":
                # Check for priority keyword at the end
                priority = 1  # default low
                raw_args = args[1:]
                if raw_args and raw_args[-1].lower() in ("high", "low"):
                    p = raw_args[-1].lower()
                    priority = 0 if p == "high" else 1
                    raw_args = raw_args[:-1]
                
                # Parse comma-separated Pokemon names
                raw_text = " ".join(raw_args)
                names = [n.strip().lower() for n in raw_text.split(",") if n.strip()]
                
                added = []
                failed = []
                for name in names:
                    if not is_valid_pokemon(name):
                        failed.append(name)
                        continue
                    config.add_target(name)
                    if priority == 0:
                        config.add_high_priority(name)
                    added.append(name)
                
                msg_parts = []
                if added:
                    prio_tag = " (HIGH)" if priority == 0 else ""
                    msg_parts.append(f"Added {', '.join(added)}{prio_tag} to target list.")
                    msg_parts.append(f"Current targets: {', '.join(config.get_targets()) or 'none'}")
                if failed:
                    msg_parts.append(f"Invalid: {', '.join(failed)}")
                await self._send(message.channel, "\n".join(msg_parts) if msg_parts else "No valid Pokemon names provided.")
            else:
                await self._send(message.channel, "Usage: `@bot add target <pokemon1, pokemon2, ...> [high/low]`")

        elif cmd == "remove":
            if len(args) >= 2 and args[0].lower() == "target":
                pokemon = " ".join(args[1:])
                config.remove_target(pokemon)
                await self._send(message.channel, 
                    f"Removed `{pokemon}` from target list. "
                    f"Current targets: {', '.join(config.get_targets()) or 'none'}"
                )
            else:
                await self._send(message.channel, "Usage: `@bot remove target <pokemon>`")

        elif cmd == "targets":
            targets = config.get_targets()
            high = config.get_high_priority()
            skipped = config.get_skipped()
            solo = config.get("target_only_pokemon", [])
            if targets:
                lines = ["**Target Pokemon:**"]
                for t in targets:
                    tag = " (SKIP)" if t in skipped else (" (HIGH)" if t in high else "")
                    if t in solo:
                        tag += " (SOLO)"
                    lines.append(f"• {t}{tag}")
                if not config.get_hunt_targets():
                    lines.append("_All targets are skipped — hunting every other Pokémon._")
                await self._send(message.channel, "\n".join(lines)[:1900])
            else:
                await self._send(message.channel, "No targets set — hunting every Pokémon. Use `@bot add target <pokemon>` or `@bot skip <pokemon>`")

        elif cmd in ("skip", "unskip"):
            if args:
                names = [n.strip().lower() for n in " ".join(args).split(",") if n.strip()]
                done, failed = [], []
                for n in names:
                    if not is_valid_pokemon(n):
                        failed.append(n)
                        continue
                    config.set_skip(n, cmd == "skip")
                    done.append(n)
                msg = []
                if done:
                    msg.append(f"{'Skipping' if cmd == 'skip' else 'No longer skipping'}: {', '.join(done)}")
                    hunt = config.get_hunt_targets()
                    msg.append(f"Hunting: {', '.join(hunt) if hunt else 'every Pokémon except skipped'}")
                if failed:
                    msg.append(f"Invalid: {', '.join(failed)}")
                await self._send(message.channel, "\n".join(msg))
            else:
                sk = config.get_skipped()
                await self._send(message.channel, f"Skip list: {', '.join(sk) or 'empty'}\nUsage: `@bot skip <pokemon1, pokemon2>` / `@bot unskip <pokemon>`")

        elif cmd == "queue":
            q = queue.get_queue()
            if q:
                lines = ["**Queue:**"]
                for i, item in enumerate(q, 1):
                    prio = "[HIGH] " if item.get("priority", 1) == 0 else ""
                    lines.append(
                        f"{i}. {prio}{item['pokemon']} — "
                        f"DSP: {item['remaining_minutes']}m "
                        f"({'expired' if item['is_expired'] else 'active'})"
                    )
                await self._send(message.channel, "\n".join(lines))
            else:
                await self._send(message.channel, "Queue is empty.")

        elif cmd == "clear":
            if len(args) >= 1 and args[0].lower() == "queue":
                queue.clear()
                await self._send(message.channel, "Queue cleared.")
            else:
                await self._send(message.channel, "Usage: `@bot clear queue`")

        elif cmd == "stats":
            s = stats.get_stats()
            lines = [
                "**Statistics:**",
                f"Teleports: {s['total_teleports']} ({s.get('teleports_hour', 0)}/hr, {s.get('teleports_day', 0)}/day)",
                f"Caught: {s['total_caught']}",
                f"Fled: {s['total_fled']}",
                f"Expired: {s['total_expired']}",
                f"Shundos (shiny+100%): {s['total_shundos']} ({s.get('shundos_hour', 0)}/hr)",
                f"Hundos (100% IV): {s['total_hundos']} ({s.get('hundos_hour', 0)}/hr, {s.get('hundo_rate', 0)}% rate)",
                f"Shinies: {s['total_shinies']} ({s.get('shinies_hour', 0)}/hr, {s.get('shiny_rate', 0)}% rate)",
            ]
            if s.get("avg_time_to_hundo", 0) > 0:
                avg = s["avg_time_to_hundo"]
                lines.append(f"Avg time to hundo: {avg // 60}m {avg % 60}s")
            if s.get("last_caught"):
                lc = s["last_caught"]
                lines.append(
                    f"Last caught: {display_name(lc['pokemon'])} "
                    f"({lc.get('cp', '?')}, {lc.get('iv', '?')})"
                )
            if s.get("cooldown_remaining", 0) > 0:
                mins = s["cooldown_remaining"] // 60
                lines.append(f"Catch cooldown: {mins}m remaining")
            await self._send(message.channel, "\n".join(lines))

        elif cmd == "caught":
            s = stats.get_stats()
            caught = s.get("caught_pokemon", {})
            if caught:
                lines = ["**Caught Pokemon:**"]
                for poke, count in sorted(caught.items(), key=lambda x: -x[1]):
                    lines.append(f"• {poke}: {count}")
                await self._send(message.channel, "\n".join(lines))
            else:
                await self._send(message.channel, "No Pokemon caught yet.")

        elif cmd == "start":
            if self.running:
                await self._send(message.channel, "Already running!")
            elif not config.get("watch_channel_id"):
                await self._send(message.channel, 
                    "No watch channel set! Use `@bot set watch <channel_id>` first."
                )
            elif not config.get_hunt_targets() and not config.get("shundo_dm_enabled", True):
                await self._send(message.channel, 
                    "No target Pokemon set! Use `@bot add target <pokemon>` first."
                )
            else:
                self.running = True
                stats.record_start()
                if self.worker_task is None or self.worker_task.done():
                    self.worker_task = asyncio.create_task(self._worker_loop())
                # Start background log scanner for passive hundo/shiny tracking
                if self._bg_scanner_task is None or self._bg_scanner_task.done():
                    self._bg_scanner_task = asyncio.create_task(self._background_log_scanner())
                targets = config.get_hunt_targets()
                await self._send(message.channel, 
                    f"Started monitoring channel `{config.get('watch_channel_id')}` "
                    f"for: {', '.join(targets) or 'every Pokémon except skipped'}\n"
                    f"Targets in queue: {queue.size()}"
                )

        elif cmd == "stop":
            self.running = False
            stats.record_stop()
            await self._send(message.channel, "Stopped monitoring.")

        elif cmd == "status":
            status = "running" if self.running else "stopped"
            cooldown = stats.cooldown_remaining()
            targets = config.get_targets()
            high = config.get_high_priority()
            lines = [
                f"**Status:** {status}",
                f"**Watch channel:** `{config.get('watch_channel_id', 'N/A')}`",
                f"**Targets:** {', '.join(targets) or 'none'}",
                f"**High priority:** {', '.join(high) or 'none'}",
                f"**Queue size:** {queue.size()}",
            ]
            if cooldown > 0:
                lines.append(f"**Catch cooldown:** {cooldown // 60}m {cooldown % 60}s remaining")
            lines.append(f"**Web dashboard:** http://127.0.0.1:{config.get('web_port', 8765)}")
            await self._send(message.channel, "\n".join(lines))

        elif cmd == "set":
            await self._handle_set(message, args)

        elif cmd == "priority":
            if len(args) >= 2:
                pokemon = args[0].lower()
                level = args[1].lower()
                if level == "high":
                    config.add_high_priority(pokemon)
                    await self._send(message.channel, f"`{pokemon}` set to HIGH priority.")
                elif level == "low":
                    config.remove_high_priority(pokemon)
                    await self._send(message.channel, f"`{pokemon}` set to LOW priority.")
                else:
                    await self._send(message.channel, "Usage: `@bot priority <pokemon> high/low`")
            else:
                await self._send(message.channel, "Usage: `@bot priority <pokemon> high/low`")

        elif cmd == "shundos":
            await self._send(message.channel, self._shundo_report())

        elif cmd == "channels":
            """Show per-channel encounter success rates."""
            if not self._channel_stats:
                await self._send(message.channel, "No channel stats recorded yet.")
            else:
                lines = ["**Channel Success Rates:**"]
                for ch_id, data in sorted(self._channel_stats.items(), key=lambda x: -x[1].get("total", 0)):
                    total = data.get("total", 0)
                    if total == 0:
                        continue
                    found = data.get("found", 0)
                    caught = data.get("caught", 0)
                    fled = data.get("fled", 0)
                    no_result = data.get("no_result", 0)
                    skipped = data.get("skipped", 0)
                    found_pct = (found + caught) / total * 100 if total else 0
                    lines.append(
                        f"Channel `{ch_id}`: {total} attempts | "
                        f"Found: {found + caught} ({found_pct:.0f}%) | "
                        f"No result: {no_result} | Fled: {fled} | Skipped: {skipped}"
                    )
                await self._send(message.channel, "\n".join(lines))

    async def _handle_set(self, message, args):
        """Handle the 'set' command for updating settings."""
        # Filter out "channel" keyword
        args = [a for a in args if a.lower() != "channel"]
        if len(args) < 2:
            await self._send(message.channel, 
                "Usage: `@bot set <key> <value>`\n"
                "Keys: `watch`, `cooldown`, `walk_after_teleport`, `auto_remove`, "
                "`monitor_timeout`, `walk_distance`"
            )
            return

        key = args[0].lower()
        value = " ".join(args[1:])

        if key in ("watch", "watch_channel"):
            if not value.isdigit():
                await self._send(message.channel, "Channel ID must be a number")
                return
            config.set("watch_channel_id", value)
            await self._send(message.channel, f"Watch channel set to `{value}`")

        elif key == "cooldown":
            # Cooldown cap is hard-coded based on the distance chart (max 2 hours).
            # It is no longer adjustable.
            await self._send(message.channel, "Catch cooldown is hard-coded based on the distance chart (max 2 hours) and cannot be changed.")

        elif key in ("walk_after_teleport", "walk"):
            val = value.lower() in ("on", "true", "yes", "1")
            config.set("walk_after_teleport", val)
            await self._send(message.channel, f"Walk after teleport: {'ON' if val else 'OFF'}")

        elif key in ("auto_remove", "auto_remove_caught"):
            val = value.lower() in ("on", "true", "yes", "1")
            config.set("auto_remove_caught", val)
            await self._send(message.channel, f"Auto-remove caught: {'ON' if val else 'OFF'}")

        elif key in ("monitor_timeout", "timeout"):
            try:
                config.set("monitor_timeout_seconds", int(value))
                await self._send(message.channel, f"Monitor timeout set to {value} seconds")
            except ValueError:
                await self._send(message.channel, "Timeout must be a number")

        elif key in ("walk_distance", "distance"):
            try:
                config.set("walk_distance_meters", int(value))
                await self._send(message.channel, f"Walk distance set to {value} meters")
            except ValueError:
                await self._send(message.channel, "Distance must be a number")

        elif key in ("notify", "notify_user"):
            if not value.isdigit():
                await self._send(message.channel, "User ID must be a number")
                return
            config.set("notify_user_id", value)
            await self._send(message.channel, f"Shundo notifications will be sent to user ID `{value}`")

        elif key in ("skip_non_shiny", "skip_nonshiny", "skip_hundo_non_shiny"):
            val = value.lower() in ("on", "true", "yes", "1")
            config.set("skip_non_shiny", val)
            await self._send(message.channel, f"Skip hundo non-shiny: {'ON' if val else 'OFF'}")

        elif key in ("queue_limit", "queue_limit_per_pokemon"):
            try:
                config.set("queue_limit_per_pokemon", int(value))
                await self._send(message.channel, f"Queue limit per Pokemon set to {value}")
            except ValueError:
                await self._send(message.channel, "Queue limit must be a number")

        else:
            await self._send(message.channel, 
                f"Unknown key: `{key}`. Available: watch, cooldown, "
                "walk_after_teleport, auto_remove, monitor_timeout, walk_distance, "
                "notify, skip_non_shiny, queue_limit"
            )

    def _remove_evolution_line(self, pokemon, queue=None):
        """Remove a Pokemon and its entire evolution line from targets.
        Uses PokeAPI to fetch the evolution chain."""
        import urllib.request
        import json as _json
        name = pokemon.lower().strip()
        to_remove = {name}
        try:
            # Get species data to find evolution chain URL
            url = f"https://pokeapi.co/api/v2/pokemon-species/{name}/"
            with urllib.request.urlopen(url, timeout=10) as resp:
                species = _json.loads(resp.read())
            evo_url = species.get("evolution_chain", {}).get("url")
            if not evo_url:
                config.remove_target(name)
                return
            # Get evolution chain
            with urllib.request.urlopen(evo_url, timeout=10) as resp:
                chain_data = _json.loads(resp.read())
            # Walk the chain recursively
            def walk_chain(node):
                if node.get("species", {}).get("name"):
                    to_remove.add(node["species"]["name"].lower())
                for evo in node.get("evolves_to", []):
                    walk_chain(evo)
            walk_chain(chain_data.get("chain", {}))
            print(f"[Worker] Evolution line to remove: {to_remove}")
        except Exception as e:
            print(f"[Worker] Error fetching evolution chain for {name}: {e}")
        # Remove all from targets, high priority, and queue
        for p in to_remove:
            if config.is_skipped(p):
                continue  # keep skip entries on the list
            config.remove_target(p)
            removed = queue.remove_pokemon(p)
            if removed:
                print(f"[Worker] Removed {removed} queued {p} (evolution line)")

    def _extract_urls(self, message):
        """Extract all URLs from a Discord message (content + embeds)."""
        urls = []
        found = re.findall(r'https?://[^\s<>\]\)]+', message.content)
        urls.extend(found)
        for embed in message.embeds:
            if embed.url:
                urls.append(str(embed.url))
            if embed.description:
                urls.extend(re.findall(r'https?://[^\s<>\]\)]+', embed.description))
            if embed.fields:
                for field in embed.fields:
                    if field.value:
                        urls.extend(re.findall(r'https?://[^\s<>\]\)]+', field.value))
        # Deduplicate, strip trailing punctuation
        seen = set()
        unique = []
        for u in urls:
            u = u.rstrip(".,!?")
            if u not in seen:
                seen.add(u)
                unique.append(u)
        return unique

    def _parse_dsp(self, text):
        """Extract DSP minutes from message text. Returns int or None.

        Pokemon spawns despawn within 60 minutes, so any parsed value
        above 60 is likely a parsing error (e.g. CEA HH:MM format where
        the despawn time is far in the future due to timezone mismatch
        or the message is stale). Cap at 60 to be safe.
        """
        from datetime import datetime, timedelta
        # Try various DSP/despawn formats:
        # "DSP in 13 minutes", "DSP in 31m", "DSP: 13m", "despawn in 13m"
        for pattern in [
            r'DSP\s+in\s+(\d+)\s*m',       # DSP in 13m / DSP in 13 minutes (matches 'm' in 'minutes')
            r'DSP\s+in\s+(\d+)\s*min',      # DSP in 13 min
            r'DSP[:\s]+(\d+)\s*m',           # DSP: 13m / DSP 13m
            r'despawn\s+in\s+(\d+)\s*m',     # despawn in 13m
            r'despawn\s+in\s+(\d+)\s*min',  # despawn in 13 min
            r'DSP\s+(\d+)\s*min',            # DSP 13 min
            r'⏰\s*(\d+)\s*m',              # ⏰ 13m (emoji prefix)
            r'(\d+)\s*m\s*\d+s',           # 13m 34s (time remaining format)
        ]:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                val = int(match.group(1))
                if 1 <= val <= 60:
                    return val
                # If > 60, it's likely a parse error — skip and try CEA format
        # CEA bot format: <:dsp:EMOJI_ID> HH:MM (24-hour clock time)
        match = re.search(r'<:dsp:\d+>\s*(\d{1,2}):(\d{2})', text, re.IGNORECASE)
        if match:
            hour = int(match.group(1))
            minute = int(match.group(2))
            now = datetime.now()
            despawn = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            diff = (despawn - now).total_seconds() / 60
            if diff < 0:
                despawn += timedelta(days=1)
                diff = (despawn - now).total_seconds() / 60
            # Cap at 60 minutes — Pokemon spawns don't last longer than that
            return max(1, min(60, int(diff)))
        return None

    def _is_shiny_in_message(self, text):
        """Check if the Discord message indicates a shiny Pokemon.
        Looks for custom emoji like <:shiny:123> or <a:shiny:456>."""
        return ':shiny:' in text.lower()

    def _parse_iv_from_message(self, text):
        """Parse IV from Discord message.
        Formats: IV100 (A15/D15/S15), IV 100.00 (15/15/15), IV100 (15/15/15)
        """
        # Format 1: IV100 (A15/D15/S15)
        match = re.search(r'IV(\d+)\s*\(A(\d+)/D(\d+)/S(\d+)\)', text, re.IGNORECASE)
        if match:
            pct = int(match.group(1))
            a, d, s = match.group(2), match.group(3), match.group(4)
            return f"{a}/{d}/{s} IV ({pct}%)"
        # Format 2: IV 100.00 (15/15/15) — CEA format
        match = re.search(r'IV\s*(\d+(?:\.\d+)?)\s*\((\d+)/(\d+)/(\d+)\)', text, re.IGNORECASE)
        if match:
            pct = int(float(match.group(1)))
            a, d, s = match.group(2), match.group(3), match.group(4)
            return f"{a}/{d}/{s} IV ({pct}%)"
        return None

    # --- Recently-teleported coordinates cache ---
    # Prevents teleporting to the same location twice when channels share feeds
    _RECENT_COORDS_TTL = 180  # 3 minutes

    def _is_recent_coords(self, coords):
        """Check if we've recently teleported to these coordinates."""
        import time
        now = time.time()
        # Clean up expired entries
        expired = [k for k, v in self._recent_coords.items() if v < now]
        for k in expired:
            del self._recent_coords[k]
        return coords in self._recent_coords

    def _mark_recent_coords(self, coords):
        """Mark coordinates as recently teleported."""
        import time
        self._recent_coords[coords] = time.time() + self._RECENT_COORDS_TTL

    async def _click_reveal_coords(self, message):
        """Click the 'Reveal Coords' button on a Discord message and wait for coordinates.
        Returns coords_str on success, None on failure.
        Serialized with a lock to prevent concurrent clicks from racing on _coord_response_future.
        Adds a delay between clicks to avoid Discord rate limiting."""
        # Serialize: only one Reveal Coords click at a time
        async with self._reveal_coords_lock:
            result = await self._do_reveal_coords_click(message)
            # Delay between clicks to avoid Discord interaction rate limiting
            # Discord throttles rapid successive button interactions
            await asyncio.sleep(2)
            return result

    async def _do_reveal_coords_click(self, message):
        """Actual Reveal Coords click logic — called under the lock."""
        import uuid

        # Find the Reveal button — match any button whose label contains 'reveal'
        button_custom_id = None
        application_id = None

        if not message.components:
            return None

        for row in message.components:
            for component in row.children:
                if hasattr(component, 'label') and component.label and 'reveal' in component.label.lower():
                    button_custom_id = component.custom_id
                    # Use message.application_id if available (correct for interaction),
                    # otherwise fall back to message.author.id
                    application_id = getattr(message, 'application_id', None) or message.author.id
                    break
            if button_custom_id:
                break

        if not button_custom_id:
            # Log what buttons ARE present for debugging
            if message.components:
                for row in message.components:
                    for component in row.children:
                        if hasattr(component, 'label') and component.label:
                            print(f"[Monitor] No 'Reveal' button found — message has button: '{component.label}'")
            return None

        chan_name = message.channel.name if hasattr(message.channel, 'name') and message.channel.name else 'DM'
        chan_id = message.channel.id
        print(f"[Monitor] Clicking '{component.label}' button… (channel: #{chan_name} {chan_id})")
        print(f"[Monitor]   application_id={application_id}, custom_id={button_custom_id[:80]}")

        # Set up future to receive the coordinate response
        loop = asyncio.get_event_loop()
        self._coord_response_future = loop.create_future()

        try:
            import aiohttp

            session_id = str(uuid.uuid4())

            data = {
                'type': 3,  # MESSAGE_COMPONENT
                'message_id': str(message.id),
                'application_id': str(application_id),
                'channel_id': str(message.channel.id),
                'data': {
                    'component_type': 2,  # BUTTON
                    'custom_id': button_custom_id,
                },
                'session_id': session_id,
            }

            if message.guild:
                data['guild_id'] = str(message.guild.id)

            headers = {
                'Authorization': self.client.http.token,
                'Content-Type': 'application/json',
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    'https://discord.com/api/v9/interactions',
                    headers=headers,
                    json=data
                ) as resp:
                    print(f"[Monitor]   Interaction response: {resp.status}")
                    if resp.status not in (200, 204):
                        body = await resp.text()
                        print(f"[Monitor] Interaction failed: {resp.status} — {body[:200]}")
                        return None

            # Wait for the ephemeral coordinate response message
            try:
                coords = await asyncio.wait_for(self._coord_response_future, timeout=15.0)
                print(f"[Monitor] Got coordinates: {coords}")
                return coords
            except asyncio.TimeoutError:
                print(f"[Monitor] Timed out waiting for coordinate response")
                return None
        finally:
            self._coord_response_future = None

    async def _check_for_targets(self, message):
        """Check if a message in the watch channel contains target Pokemon keywords."""
        targets = config.get_hunt_targets()  # target list minus skipped
        if not targets:
            return

        # Search for Pokemon names in embed TITLE + message content ONLY
        # (NOT embed fields/description — those contain PvP rankings with other Pokemon names)
        name_text = message.content
        for embed in message.embeds:
            if embed.title:
                name_text += " " + embed.title

        name_lower = name_text.lower()

        # Assemble ALL text for DSP/IV parsing (fields included)
        full_text = name_text
        for embed in message.embeds:
            if embed.description:
                full_text += " " + embed.description
            if embed.fields:
                for field in embed.fields:
                    if field.name:
                        full_text += " " + field.name
                    if field.value:
                        full_text += " " + field.value

        text_lower = name_lower

        # Check each target Pokemon — only match in title/content, not PvP fields
        for pokemon in targets:
            if pokemon.lower() in name_lower:
                # Solo mode: if any Pokémon has solo enabled, only extract coords for those
                solo_names = config.get("target_only_pokemon", [])
                if solo_names and pokemon.lower() not in solo_names:
                    continue  # Skip — not the solo target, don't click anything

                # Queue cap check: don't click coords if we already have enough of this Pokemon
                queue_limit = config.get("queue_limit_per_pokemon", 5)
                current_count = queue.get_pokemon_count(pokemon)
                pkmn_lower = pokemon.lower()

                if current_count >= queue_limit:
                    # Mark as refilled — won't click again until queue empties to 0
                    self._queue_refilled.add(pkmn_lower)
                    continue
                # If we previously filled the queue, only click again once it's fully empty
                if pkmn_lower in self._queue_refilled and current_count > 0:
                    continue
                # Queue is empty — clear the refilled flag and allow filling again
                self._queue_refilled.discard(pkmn_lower)

                # Found a target! Try "Reveal Coords" button first, then fall back to URL extraction
                print(f"[Monitor] Need coords for {pokemon} ({current_count}/{queue_limit}); extracting")
                coords = await self._click_reveal_coords(message)
                coord_url = ""
                all_urls = []

                if coords:
                    # Got coords directly from button click — no URL needed
                    chan_id = message.channel.id
                    chan_name = message.channel.name if hasattr(message.channel, 'name') and message.channel.name else 'DM'
                    print(f"[Monitor] Coordinates from Reveal Coords: {coords} (channel: #{chan_name} {chan_id})")
                    # Check if another task already has these exact coordinates
                    # (different channels may share the same feed/spawn)
                    existing = any(t.coords and t.coords == coords for t in queue.peek_all())
                    if existing:
                        print(f"[Monitor] Skipping {pokemon} — duplicate coordinates {coords} (shared feed, channel: #{chan_name} {chan_id})")
                        break
                    # Check if we recently teleported to these exact coordinates
                    if self._is_recent_coords(coords):
                        print(f"[Monitor] Skipping {pokemon} — recently teleported to {coords} (channel: #{chan_name} {chan_id})")
                        break
                else:
                    # Fall back to URL extraction (pokedex100 channel format only)
                    urls = self._extract_urls(message)
                    # Only keep pokedex100 coordinate URLs — skip bot website links
                    coord_urls = [u for u in urls if 'coord.pokedex100.com' in u]
                    if not coord_urls:
                        print(f"[Monitor] Found '{pokemon}' but no Reveal Coords button and no pokedex100 URLs — skipping")
                        continue

                    coord_url = coord_urls[0]
                    all_urls = coord_urls

                dsp_minutes = self._parse_dsp(full_text) or 30
                # Never set shiny from Discord message — shininess is only known
                # after encountering the Pokemon in-game
                iv_info = self._parse_iv_from_message(full_text)

                # Add to queue with per-Pokemon limit
                priority = 0 if config.is_high_priority(pokemon) else 1
                added = queue.add(
                    pokemon=pokemon,
                    url=coord_url,
                    dsp_minutes=dsp_minutes,
                    message_id=message.id,
                    channel_id=message.channel.id,
                    shiny=False,
                    iv_info=iv_info,
                    priority=priority,
                    queue_limit=queue_limit,
                    all_urls=all_urls,
                    coords=coords,  # Pass coords for dedup at queue level
                )
                # If we got coords from button click, cache them on the task
                if added and coords:
                    # Find the task we just added and set its coords
                    for t in queue.peek_all():
                        if t.message_id == message.id:
                            t.coords = coords
                            break
                if added:
                    print(f"[Monitor] Queued {pokemon:12s} coords={coords:25s} DSP={dsp_minutes}m (#{chan_name} {chan_id})")
                    stats.add_event("queue", f"Queued {display_name(pokemon)} at {coords} (DSP: {dsp_minutes}m)")
                else:
                    print(f"[Monitor] Duplicate {pokemon}, already in queue (#{chan_name} {chan_id})")
                break  # Only process the first matching target per message

    async def _get_coords_cached(self, task):
        """Get coordinates for a task, using cached value if available.
        Tries all URLs from the original message in order until one works."""
        if task.coords:
            return task.coords

        # Build list of URLs to try: primary URL first, then any alternates
        urls_to_try = [task.url]
        for alt in getattr(task, 'all_urls', []):
            if alt != task.url and alt not in urls_to_try:
                urls_to_try.append(alt)

        for try_url in urls_to_try:
            try:
                coords = await self.browser.get_coordinates(try_url)
            except Exception as e:
                print(f"[Worker] Error fetching coords from {try_url}: {e}")
                coords = None
            if coords and coords != "DONOR_REQUIRED":
                task.coords = coords
                if try_url != task.url:
                    task.url = try_url  # Update to working URL
                    print(f"[Worker] Using alternate URL: {try_url}")
                return coords
            elif coords == "DONOR_REQUIRED":
                print(f"[Worker] URL requires donor role, trying next: {try_url}")
            else:
                print(f"[Worker] No coords from {try_url}, trying next")

        return None

    def _parse_coords(self, coords_str):
        """Parse 'lat,lng' string into (lat, lng) floats. Returns None on failure."""
        if not coords_str:
            return None
        try:
            parts = coords_str.replace(",", " ").split()
            return (float(parts[0]), float(parts[1]))
        except (ValueError, IndexError, TypeError):
            return None

    def _distance_from_last_catch(self, coords_str):
        """Calculate distance in km from last catch to these coords.
        Returns None if no last catch or coords invalid."""
        last = stats._data.get("last_catch_coords")
        if not last:
            return None
        target = self._parse_coords(coords_str)
        if not target:
            return None
        return haversine_km(last["lat"], last["lng"], target[0], target[1])

    async def _background_log_scanner(self):
        """Background task that scans game logs for hundos/shinies/catches
        even when the bot is idle (no targets in queue).

        This ensures encounters are always tracked, not just during active monitoring.
        Runs every 15 seconds, independent of the main worker loop."""
        from pokemon_data import get_name_by_id

        print("[BgScanner] Background log scanner started")
        # Initialize baseline with current log contents
        try:
            if self.browser:
                text = await self.browser.read_logs()
                if text:
                    self._bg_baseline_lines = set(text.splitlines())
        except Exception as e:
            print(f"[BgScanner] Error initializing baseline: {e}")

        while True:
            try:
                await asyncio.sleep(15)
                if not self.browser:
                    continue

                text = await self.browser.read_logs()
                if not text:
                    continue

                current_lines = set(text.splitlines())
                new_lines = current_lines - self._bg_baseline_lines
                self._bg_baseline_lines = current_lines

                if not new_lines:
                    continue

                # Parse encounter lines for hundos and shinies
                for line in new_lines:
                    line_lower = line.lower()

                    # Only process encounter or catch lines
                    if "[normalencounter]" not in line_lower and "normal-encounter" not in line_lower:
                        # Also check for catch lines
                        if "[catchpokemon]" not in line_lower and "caught" not in line_lower:
                            continue

                    # Check for CATCH lines FIRST — catch lines contain "Normal-Encounter"
                    # so they'd match the encounter check below, but the encounter regex
                    # would fail (no CP/IV data) and skip the catch detection via continue.
                    # Parse catch lines for cooldown tracking
                    if ("[catchpokemon]" in line_lower or ("caught" in line_lower and "pokemon" in line_lower)):
                        catch_match = re.search(
                            r'Caught.*?Pokemon\s+(.+?)\s+\(#(\d+)\)',
                            line, re.IGNORECASE
                        )
                        flee_match = None if catch_match else re.search(
                            r'\[CatchPokemon\].*?Pokemon\s+(.+?)\s+\(#(\d+)\)\s+fled',
                            line, re.IGNORECASE
                        )
                        if flee_match:
                            ft = self._log_line_epoch(line) or time.time()
                            fc = self._coords_at(ft) or self.current_coords
                            if fc:
                                stats.record_flee_anchor(fc, at_time=ft, reason="fled")
                                print(f"[BgScanner] Flee detected: {flee_match.group(1)} at {fc} — cooldown reset")
                        if catch_match:
                            dex_num = int(catch_match.group(2))
                            caught_name = get_name_by_id(dex_num) or catch_match.group(1).strip()
                            # Try to find the matching encounter line for CP/IV/EncounterId
                            nt_cp = None
                            nt_iv = None
                            nt_iv_pct = 0
                            nt_shiny = False
                            nt_enc_id = None
                            for enc_line in new_lines:
                                enc_lower = enc_line.lower()
                                if caught_name.lower() not in enc_lower:
                                    continue
                                # Must be an encounter line with EncounterId (not a catch line)
                                if "encounterid:" not in enc_lower:
                                    continue
                                if "[normalencounter]" not in enc_lower and "normal-encounter" not in enc_lower:
                                    continue
                                enc_match = re.search(
                                    rf'{re.escape(caught_name)}.*?(\d+)cp.*?(\d+/\d+/\d+)\s*IV\s*\((\d+)%\)',
                                    enc_line, re.IGNORECASE
                                )
                                if enc_match:
                                    nt_cp = enc_match.group(1)
                                    nt_iv = f"{enc_match.group(2)} IV ({enc_match.group(3)}%)"
                                    nt_iv_pct = int(enc_match.group(3))
                                nt_shiny = any(p.lower() in enc_lower for p in config.get("shiny_log_patterns", []))
                                enc_id_match = re.search(r'EncounterId:\s*(\d+)', enc_line)
                                if enc_id_match:
                                    nt_enc_id = enc_id_match.group(1)
                                break
                            # Attribute the catch to where the player WAS at the logged time
                            # (this scanner can run 15 s+ after the catch, by which point the
                            # worker may already have teleported somewhere else).
                            line_t = self._log_line_epoch(line)
                            coords = self._coords_at(line_t) if line_t else None
                            if not coords:
                                coords = self.current_coords or self._last_encounter_coords
                            if not coords:
                                last_coords = stats._data.get("last_catch_coords")
                                if last_coords:
                                    coords = f"{last_coords['lat']},{last_coords['lng']}"
                            stats.record_caught(
                                caught_name,
                                cp=nt_cp,
                                iv=nt_iv,
                                shiny=nt_shiny,
                                iv_percent=nt_iv_pct,
                                encounter_id=nt_enc_id,
                                coords=coords,
                                is_target=False,
                                at_time=line_t,
                            )
                            # Start visible cooldown on dashboard (2hr max from distance chart)
                            # This is display-only; worker uses per-target distance cooldowns.
                            stats.start_cooldown(7200)
                            # Remove caught Pokemon from queue and targets if applicable
                            try:
                                purged = queue.remove_pokemon(caught_name)
                                if purged:
                                    print(f"[BgScanner] Removed {purged} queued {caught_name} (caught by SX CatchRules)")
                                # If caught Pokemon is a hundo/shundo and in target list, remove from targets
                                is_hundo_or_shundo = nt_iv_pct == 100 or (nt_shiny and nt_iv_pct == 100)
                                if is_hundo_or_shundo and caught_name.lower() in [t.lower() for t in config.get_hunt_targets()]:
                                    if config.get("auto_remove_caught", True):
                                        if config.get("remove_evolution_line", False):
                                            self._remove_evolution_line(caught_name, queue)
                                        else:
                                            config.remove_target(caught_name)
                                        print(f"[BgScanner] Auto-removed {caught_name} from targets (caught)")
                            except Exception as e:
                                print(f"[BgScanner] Error removing caught target: {e}")
                            # Cross-reference with pending PokeX shundos (marks caught + triggers re-plan)
                            try:
                                hit = self.shundos.mark_caught_by_name(caught_name, coords)
                                if hit:
                                    print(f"[Shundo] Catch matched PokeX alert: {hit['name']} ({hit['city']})")
                            except Exception as e:
                                print(f"[Shundo] Catch cross-reference error: {e}")
                            print(f"[BgScanner] Catch detected: {caught_name} (cp={nt_cp}, iv={nt_iv}, shiny={nt_shiny}, coords={coords}) — cooldown started")
                        continue  # Don't also process as encounter

                    # Parse encounter line for hundo/shiny
                    if "normal-encounter" in line_lower or "[normalencounter]" in line_lower:
                        # Dedup by encounter ID
                        enc_id_match = re.search(r'EncounterId:\s*(\d+)', line)
                        enc_id = enc_id_match.group(1) if enc_id_match else None
                        if enc_id and enc_id in self._bg_scanned_encounter_ids:
                            continue
                        if enc_id:
                            self._bg_scanned_encounter_ids.add(enc_id)
                            # Keep set bounded
                            if len(self._bg_scanned_encounter_ids) > 500:
                                self._bg_scanned_encounter_ids = set(list(self._bg_scanned_encounter_ids)[-250:])

                        # Parse dex number and IV
                        enc_match = re.search(
                            r'\(#(\d+)\)\s.*?(\d+)cp.*?(\d+/\d+/\d+)\s*IV\s*\((\d+)%\)',
                            line, re.IGNORECASE
                        )
                        if not enc_match:
                            continue

                        dex_num = int(enc_match.group(1))
                        pokemon_name = get_name_by_id(dex_num) or f"pokemon#{dex_num}"
                        cp = enc_match.group(2)
                        iv_str = f"{enc_match.group(3)} IV ({enc_match.group(4)}%)"
                        iv_pct = int(enc_match.group(4))

                        # Check for shiny indicators
                        shiny_patterns = config.get("shiny_log_patterns", [])
                        is_shiny = any(p.lower() in line_lower for p in shiny_patterns)

                        # Only log hundos and shinies
                        if iv_pct == 100 or is_shiny:
                            # Store encounter coords for catch cooldown tracking
                            if self.current_coords:
                                self._last_encounter_coords = self.current_coords
                            stats.record_encounter(
                                pokemon_name,
                                cp=cp,
                                iv=iv_str,
                                shiny=is_shiny,
                                iv_percent=iv_pct,
                                encounter_id=enc_id,
                                is_target=False,
                            )
                            if iv_pct == 100 and is_shiny:
                                print(f"[BgScanner] SHUNDO: {pokemon_name} — {cp}cp, {iv_str}")
                                # Send DM notification for shundo
                                notify_id = config.get("notify_user_id")
                                if notify_id:
                                    try:
                                        user = await self.client.fetch_user(int(notify_id))
                                        if user:
                                            await self._dm(user, f"SHUNDO FOUND! {display_name(pokemon_name)} — {cp}cp, {iv_str}")
                                            print(f"[BgScanner] Notified user about shundo {pokemon_name}")
                                    except Exception as e:
                                        print(f"[BgScanner] Failed to send shundo DM: {e}")
                            elif iv_pct == 100:
                                print(f"[BgScanner] Hundo: {pokemon_name} — {cp}cp, {iv_str}")
                            elif is_shiny:
                                print(f"[BgScanner] Shiny: {pokemon_name} — {cp}cp, {iv_str}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[BgScanner] Error: {e}")

        print("[BgScanner] Background log scanner stopped")

    async def _select_best_task(self):
        """Select the best task from the queue using proximity-based ordering.

        Sorting priority (when last_catch_coords exists):
          1. Catchable (cooldown=0) before in-cooldown — catchable wins above all
          2. Priority (0=high first)
          3. Distance from last catch (closer = shorter cooldown)
          4. DSP remaining (earliest expiry first)

        When no last_catch_coords (no catches yet):
          1. Priority
          2. DSP remaining (original behavior)
          Coords are NOT fetched here — only after selection.

        Targets with DSP remaining below min_dsp_seconds are skipped
        (spawn likely already gone by the time we teleport).

        Returns (task, coords) or (None, None).
        """
        # Guaranteed shundos (PokeX DMs) always come first, following the max-catch plan.
        self._shundo_wait = None
        if config.get("shundo_dm_enabled", True):
            s_task, s_coords, s_wait = self._shundo_select()
            if s_task:
                return s_task, s_coords
            self._shundo_wait = s_wait
            # While any shundo is pending, keep the regular queue paused so a
            # regular catch can't move the catch location and break the plan.
            if config.get("shundo_pause_queue", True) and self.shundos.has_active():
                return None, None

        for _sk in config.get_skipped():
            if queue.get_pokemon_count(_sk):
                n = queue.remove_pokemon(_sk)
                print(f"[Worker] Removed {n} queued {_sk} (on skip list)")

        all_tasks = queue.peek_all()
        if not all_tasks:
            return None, None

        # Target-only mode: if any Pokémon with target-only enabled has tasks
        # in the queue, only hunt those — ignore everything else.
        target_only_names = config.get("target_only_pokemon", [])
        if target_only_names:
            to_tasks = [t for t in all_tasks if t.pokemon.lower() in target_only_names]
            if to_tasks:
                print(f"[Worker] Target-only active for: {', '.join(target_only_names)} — filtering queue")
                all_tasks = to_tasks

        # Minimum DSP remaining in seconds — skip targets about to expire
        min_dsp_seconds = config.get("min_dsp_seconds", 120)

        has_last_catch = bool(stats._data.get("last_catch_coords"))

        if not has_last_catch:
            # No catches yet — sort by priority + DSP only, no coords needed
            sorted_tasks = sorted(all_tasks, key=lambda t: (t.priority, t.remaining_seconds))
            for task in sorted_tasks:
                # Skip targets with DSP below minimum threshold
                if task.remaining_seconds < min_dsp_seconds:
                    print(f"[Worker] Skipping {task.pokemon} — DSP {task.remaining_minutes}m below minimum ({min_dsp_seconds}s)")
                    stats.record_expired(task.pokemon)
                    queue.mark_done(task)
                    continue
                coords = await self._get_coords_cached(task)
                if coords:
                    if queue.claim(task):
                        print(f"[Worker] Selected {task.pokemon} — DSP={task.remaining_minutes}m")
                        return task, coords
                    else:
                        print(f"[Worker] Could not claim {task.pokemon} — trying next")
                else:
                    print(f"[Worker] No coords for {task.pokemon} — skipping")
            return None, None

        # Has last catch — need coords for proximity sorting
        # Fetch coords with error handling, skip failures
        candidates = []
        for task in all_tasks:
            # Skip targets with DSP below minimum threshold
            if task.remaining_seconds < min_dsp_seconds:
                print(f"[Worker] Skipping {task.pokemon} — DSP {task.remaining_minutes}m below minimum ({min_dsp_seconds}s)")
                stats.record_expired(task.pokemon)
                queue.mark_done(task)
                continue
            try:
                coords = await self._get_coords_cached(task)
            except Exception as e:
                print(f"[Worker] Error fetching coords for {task.pokemon}: {e} — skipping")
                continue
            if not coords:
                print(f"[Worker] No coords for {task.pokemon} — skipping")
                continue
            cd_remaining = stats.get_catch_cooldown_for_target(coords)
            distance = self._distance_from_last_catch(coords) if has_last_catch else 0

            # Auto-remove: if DSP remaining < cooldown remaining, the target will
            # expire before we can catch it — remove from queue and skip.
            if cd_remaining > 0 and task.remaining_seconds < cd_remaining:
                print(f"[Worker] Removing {task.pokemon} from queue — DSP {task.remaining_minutes}m < cooldown {cd_remaining // 60}m {cd_remaining % 60}s")
                stats.record_expired(task.pokemon)
                queue.mark_done(task)
                continue
            candidates.append({
                "task": task,
                "coords": coords,
                "cd_remaining": cd_remaining,
                "distance": distance if distance is not None else 999999,
                "priority": task.priority,
                "dsp_remaining": task.remaining_seconds,
            })

        if not candidates:
            return None, None

        # Sort: catchable (cd=0) first, then priority, then distance, then DSP
        if has_last_catch:
            candidates.sort(key=lambda c: (
                1 if c["cd_remaining"] > 0 else 0,  # catchable first — above all else
                c["priority"],        # then high-priority targets
                c["distance"],        # then closer (shorter cooldown)
                c["dsp_remaining"],   # then earliest expiry
            ))
        else:
            # No catches yet — original behavior
            candidates.sort(key=lambda c: (
                c["priority"],
                c["dsp_remaining"],
            ))

        best = candidates[0]
        task = best["task"]
        coords = best["coords"]

        # Claim the task safely
        if not queue.claim(task):
            # Task was already claimed or removed — try next
            print(f"[Worker] Could not claim {task.pokemon} — trying next")
            for c in candidates[1:]:
                if queue.claim(c["task"]):
                    return c["task"], c["coords"]
            return None, None

        cd = best["cd_remaining"]
        dist = best["distance"]
        if has_last_catch:
            print(f"[Worker] Selected {task.pokemon} — {dist:.0f}km from last catch, cooldown={cd}s, DSP={task.remaining_minutes}m")
        else:
            print(f"[Worker] Selected {task.pokemon} — DSP={task.remaining_minutes}m")

        return task, coords

    async def _backfill_queue_from_channels(self):
        """Fetch recent messages from watch channels and process them for targets.
        Called when the queue has been empty for a while to catch messages
        that were missed during processing or restarts.
        
        Runs as a background task so it doesn't block the worker loop.
        The worker can process targets as they're added to the queue.
        """
        if getattr(self, '_backfill_running', False):
            print("[Worker] Backfill already in progress — skipping")
            return
        self._backfill_running = True
        try:
            await self._do_backfill()
        finally:
            self._backfill_running = False

    async def _do_backfill(self):
        """Actual backfill logic — processes messages from watch channels."""
        watch_channel_id = config.get("watch_channel_id")
        additional_channels = config.get("additional_watch_channels", [])
        channel_ids = set()
        if watch_channel_id:
            channel_ids.add(str(watch_channel_id))
        for ch in additional_channels:
            channel_ids.add(str(ch))
        
        if not channel_ids:
            return
        
        # Track processed message IDs to avoid double-processing
        if not hasattr(self, '_backfilled_msg_ids'):
            self._backfilled_msg_ids = set()
        
        total_found = 0
        targets_queued = 0
        backfill_start = time.time()
        max_backfill_seconds = 60  # Don't run for more than 1 minute
        
        for ch_id in channel_ids:
            # Stop if we've been running too long
            if time.time() - backfill_start > max_backfill_seconds:
                print(f"[Worker] Backfill time limit ({max_backfill_seconds}s) reached — stopping")
                break
            try:
                channel = self.client.get_channel(int(ch_id))
                if not channel:
                    continue
                # Fetch last 15 messages from each channel
                async for message in channel.history(limit=15):
                    # Stop if we've been running too long
                    if time.time() - backfill_start > max_backfill_seconds:
                        break
                    # Skip already-processed messages
                    if message.id in self._backfilled_msg_ids:
                        continue
                    self._backfilled_msg_ids.add(message.id)
                    # Keep the set bounded
                    if len(self._backfilled_msg_ids) > 500:
                        self._backfilled_msg_ids = set(list(self._backfilled_msg_ids)[-250:])
                    # Process through the same target checker
                    was_running = self.running
                    self.running = True
                    try:
                        queue_before = queue.size()
                        await self._check_for_targets(message)
                        queue_after = queue.size()
                        if queue_after > queue_before:
                            targets_queued += (queue_after - queue_before)
                    finally:
                        self.running = was_running
                    total_found += 1
            except Exception as e:
                print(f"[Worker] Error backfilling from channel {ch_id}: {e}")
        
        # Keep the backfilled IDs set from growing unbounded
        if len(self._backfilled_msg_ids) > 500:
            self._backfilled_msg_ids = set(list(self._backfilled_msg_ids)[-250:])
        
        elapsed = int(time.time() - backfill_start)
        if targets_queued > 0:
            print(f"[Worker] Channel backfill: scanned {total_found} messages in {elapsed}s, queued {targets_queued} target(s)")
        else:
            print(f"[Worker] Channel backfill: scanned {total_found} messages in {elapsed}s, no targets found")

    async def _worker_loop(self):
        """Background worker that processes the queue: teleport, walk, monitor logs."""
        print(f"[Worker] Started (running={self.running})")
        heartbeat_counter = 0
        idle_since = None  # Track when the queue first went empty
        backfill_interval = 300  # Backfill every 5 minutes when idle
        last_backfill = 0
        while self.running:
            # Check if paused
            if self.paused:
                self.current_activity = "Paused"
                self.loop_step = 0
                await asyncio.sleep(1)
                continue
            task = None
            try:
                # Cleanup expired targets
                expired = queue.cleanup_expired()
                for exp_task in expired:
                    print(f"[Worker] Expired: {exp_task.pokemon}")
                    stats.record_expired(exp_task.pokemon)

                # Cleanup targets below min DSP — remove before they're selected
                min_dsp = config.get("min_dsp_seconds", 120)
                below_dsp = queue.cleanup_below_dsp(min_dsp)
                for bd_task in below_dsp:
                    print(f"[Worker] Removed {bd_task.pokemon} from queue — DSP {bd_task.remaining_minutes}m below min ({min_dsp}s)")
                    stats.record_expired(bd_task.pokemon)

                # Shundo housekeeping: nothing is mid-processing at the top of the loop,
                # so any 'processing' entry was left by an early exit (skip/error) — requeue it.
                for _e in self.shundos.entries.values():
                    if _e["status"] == "processing":
                        _e["status"] = "pending" if _e.get("attempts", 0) < 3 else "missed"
                        self.shundos._plan_cache = None
                self.shundos.expire()

                # Purge expired tasks before selecting
                self._purge_expired_tasks()
                # Select best task using proximity-based ordering
                # This handles cooldown checking and picks the nearest catchable target
                task, coords = await self._select_best_task()
                if task is None:
                    # Track when we first went idle
                    if idle_since is None:
                        idle_since = time.time()
                    idle_secs = int(time.time() - idle_since)
                    
                    sw = getattr(self, "_shundo_wait", None)
                    if sw:
                        nxt, cd, dist, n_plan = sw
                        self.current_activity = (f"Shundo cooldown — {nxt['name']}: {cd // 60}m {cd % 60}s "
                                                 f"({dist:.0f}km) • {n_plan} planned")
                        self.loop_step = 6
                    elif self.shundos.has_active():
                        self.current_activity = "Getting shundo coordinates from PokeX…"
                        self.loop_step = 2
                    elif not config.get("channel_watch_enabled", True):
                        self.current_activity = "Waiting for PokeX shundo alerts…"
                        self.loop_step = 1
                    else:
                        self.current_activity = "Waiting for targets in channel…"
                        self.loop_step = 1
                    # Heartbeat every 30 seconds so we know the bot is alive
                    heartbeat_counter += 1
                    if heartbeat_counter % 30 == 0:
                        stale_secs = int(time.time() - self._last_message_time)
                        stale_mins = stale_secs // 60
                        target_count = len(config.get_targets())
                        print(f"[Worker] Heartbeat — watching for targets… ({heartbeat_counter}s, last message {stale_mins}m ago, {target_count} targets in list)")
                        # Watchdog: if no Discord messages for 5+ minutes, the connection may have dropped
                        if stale_secs > 300:
                            print(f"[Worker] WARNING: No Discord messages for {stale_mins} minutes — connection may be dead")
                            # Check if the Discord client is still connected
                            if not self.client.is_ready():
                                print("[Worker] Discord client disconnected — attempting manual reconnect…")
                                try:
                                    await self.client.close()
                                    await asyncio.sleep(2)
                                    await self.client.login(config.get("discord_token"))
                                    await self.client.connect()
                                    print("[Worker] Discord reconnection attempt completed")
                                except Exception as e:
                                    print(f"[Worker] Discord reconnection failed: {e}")
                            self._last_message_time = time.time()  # Reset to avoid spamming reconnect attempts
                        
                        # Channel backfill: if idle for 5+ minutes, scan recent channel messages
                        # for missed targets (messages posted while bot was busy or restarting)
                        if (config.get("channel_watch_enabled", True)
                                and idle_secs >= backfill_interval
                                and (time.time() - last_backfill) >= backfill_interval):
                            print(f"[Worker] Queue empty for {idle_secs//60}m — backfilling from recent channel messages…")
                            last_backfill = time.time()
                            # Run backfill as a background task so the worker loop
                            # can process targets as they're added to the queue
                            asyncio.create_task(self._backfill_queue_from_channels())
                    await asyncio.sleep(1)
                    continue

                # We have a task — reset idle tracking
                idle_since = None
                heartbeat_counter = 0

                self.current_coords = coords
                self.current_activity = f"Processing {task.pokemon} (DSP: {task.remaining_minutes}m)"
                self.loop_step = 1
                print(f"[Worker] Processing: {task.pokemon} (DSP: {task.remaining_minutes}m)")

                # Check if user requested skip
                if self.skip_current:
                    self.skip_current = False
                    self.current_activity = f"Skipped {display_name(task.pokemon)} manually"
                    queue.mark_done(task)
                    print(f"[Worker] {task.pokemon} skipped manually")
                    continue

                # Check if expired
                if task.is_expired:
                    print(f"[Worker] {task.pokemon} expired, skipping")
                    stats.record_expired(task.pokemon)
                    queue.mark_expired(task)
                    continue

                # Check cooldown — _select_best_task already prefers catchable targets,
                # but if ALL targets are in cooldown, we should NOT claim one and wait.
                # Instead, release the task and wait briefly for new catchable targets.
                cooldown_remaining = stats.get_catch_cooldown_for_target(coords)
                if cooldown_remaining > 0 and getattr(task, "shundo_id", None):
                    # Shundo tasks are only handed out at cooldown 0; if a catch just
                    # landed in between, put it back and let the planner re-decide.
                    e = self.shundos.entries.get(task.shundo_id)
                    if e and e["status"] == "processing":
                        e["status"] = "pending"
                        e["attempts"] = max(0, e.get("attempts", 1) - 1)
                        self.shundos._plan_cache = None
                    await asyncio.sleep(1)
                    continue
                if cooldown_remaining > 0:
                    # Release the task — don't hold it while waiting
                    queue.release(task)
                    dist = self._distance_from_last_catch(coords) or 0
                    mins = cooldown_remaining // 60
                    secs = cooldown_remaining % 60
                    print(f"[Worker] {task.pokemon} in cooldown ({mins}m {secs}s, {dist:.0f}km) — waiting for catchable targets…")

                    cooldown_logged = False
                    check_interval = 0
                    min_dsp_seconds = config.get("min_dsp_seconds", 120)
                    while self.running:
                        # Check if any target in the queue is now catchable
                        queue.cleanup_expired()
                        all_tasks = queue.peek_all()
                        catchable = None
                        for t in all_tasks:
                            if t.remaining_seconds < min_dsp_seconds:
                                continue
                            try:
                                t_coords = await self._get_coords_cached(t)
                            except Exception:
                                continue
                            if not t_coords:
                                continue
                            if stats.get_catch_cooldown_for_target(t_coords) <= 0:
                                catchable = (t, t_coords)
                                break

                        if catchable:
                            t, t_coords = catchable
                            if queue.claim(t):
                                task = t
                                coords = t_coords
                                self.current_coords = coords
                                self.current_activity = f"Processing {task.pokemon} (DSP: {task.remaining_minutes}m)"
                                self.loop_step = 1
                                print(f"[Worker] {task.pokemon} is catchable — proceeding")
                                break
                            # Could not claim — try next loop iteration

                        # No catchable target — show cooldown status
                        # Use the shortest cooldown target for display
                        best_cd = cooldown_remaining
                        best_name = task.pokemon
                        best_dist = dist
                        for t in all_tasks:
                            if t.remaining_seconds < min_dsp_seconds:
                                continue
                            try:
                                t_coords = await self._get_coords_cached(t)
                            except Exception:
                                continue
                            if not t_coords:
                                continue
                            cd = stats.get_catch_cooldown_for_target(t_coords)
                            if cd < best_cd:
                                best_cd = cd
                                best_name = t.pokemon
                                best_dist = self._distance_from_last_catch(t_coords) or 0

                        bmins = best_cd // 60
                        bsecs = best_cd % 60
                        self.current_activity = f"Catch cooldown — {best_name}: {bmins}m {bsecs}s ({best_dist:.0f}km)"
                        self.loop_step = 6
                        if not cooldown_logged:
                            print(f"[Worker] Waiting for catchable target — {best_name}: {bmins}m {bsecs}s ({best_dist:.0f}km)")
                            cooldown_logged = True
                        await asyncio.sleep(5)
                        check_interval += 5

                self.current_activity = f"Processing {task.pokemon} (DSP: {task.remaining_minutes}m)"
                self.loop_step = 1
                print(f"[Worker] Processing: {task.pokemon} (DSP: {task.remaining_minutes}m)")

                # Take baseline log snapshot BEFORE teleporting — this captures
                # all existing log lines so we only detect NEW encounters after teleport.
                # This baseline is NOT refreshed after walking — encounters that appear
                # during teleport/walk must be detected as new lines during monitoring.
                self.current_activity = f"Reading coordinates for {display_name(task.pokemon)}…"
                self.loop_step = 2
                # Reload the logs page to force a fresh WebSocket connection.
                # The SX dashboard WebSocket can go stale between monitoring cycles,
                # causing the page to stop receiving new encounter logs.
                await self.browser.reload_logs_page()
                baseline_logs = await self.browser.snapshot_logs()

                # Teleport
                print(f"[Worker] Teleporting to {task.pokemon:12s} at {coords}")
                self.current_activity = f"Teleporting to {display_name(task.pokemon)} at {coords}…"
                self.loop_step = 3
                teleport_started_at = time.time()
                await self.browser.teleport(coords)
                self._record_position(coords, teleport_started_at)
                stats.record_teleport(task.pokemon, coords)
                self._mark_recent_coords(coords)  # Prevent re-teleporting to same location
                print(f"[Worker] Teleport complete")

                # Walk after teleport (skip if disabled or distance is 0)
                walk_enabled = config.get("walk_after_teleport", True)
                walk_dist = config.get("walk_distance_meters", 10)
                if walk_enabled and walk_dist > 0:
                    self.current_activity = f"Walking {walk_dist}m from teleport spot…"
                    self.loop_step = 4
                    await self.browser.walk(coords)
                    await asyncio.sleep(2)
                else:
                    print(f"[Worker] Walk skipped (enabled={walk_enabled}, distance={walk_dist}m)")

                # Check if browser needs restart before monitoring
                await self.browser.restart_if_needed()
                # NOTE: Do NOT take a fresh baseline here. The baseline from before
                # teleport (line 1109) already captures old logs. Taking a new one
                # would swallow encounters that appeared during teleport/walk.
                # The original baseline is sufficient — new lines after it are new.

                # Track processed encounter IDs for this task (dedup)
                processed_encounter_ids = set()

                # Monitor logs for catch/fled (only NEW lines since fresh baseline)
                monitor_timeout = config.get("monitor_timeout_seconds", 35)
                if getattr(task, "shundo_id", None):
                    monitor_timeout = config.get("shundo_monitor_timeout_seconds", 45)
                print(f"[Worker] Monitoring logs for {task.pokemon} (timeout: {monitor_timeout}s)")
                self.current_activity = f"Monitoring logs for {display_name(task.pokemon)}… (0s/{monitor_timeout}s)"
                self.loop_step = 5
                catch_patterns = config.get("catch_log_patterns", [])
                fled_patterns = config.get("fled_log_patterns", [])
                shiny_patterns = config.get("shiny_log_patterns", [])
                skip_hundo_non_shiny = config.get("skip_non_shiny", True)

                # Progress callback for real-time timer updates + skip check
                def on_progress(elapsed, timeout):
                    self.current_activity = f"Monitoring logs for {display_name(task.pokemon)}… ({elapsed}s/{timeout}s)"
                    return self.skip_current

                result = await self.browser.check_logs_for(
                    pokemon_name=base_species(task.pokemon) if getattr(task, "shundo_id", None) else task.pokemon,
                    timeout=monitor_timeout,
                    catch_patterns=catch_patterns,
                    fled_patterns=fled_patterns,
                    shiny_patterns=shiny_patterns,
                    baseline_lines=baseline_logs,
                    on_progress=on_progress,
                    skip_hundo_non_shiny=skip_hundo_non_shiny,
                    processed_encounter_ids=processed_encounter_ids,
                    cluster_skip_threshold=config.get("cluster_skip_threshold", 5),
                )

                # Log monitoring result
                mon_status = "caught" if result["caught"] else ("fled" if result["fled"] else ("skipped_hundo" if result.get("skipped_hundo_non_shiny") else ("cluster_skip" if result.get("cluster_skip") else ("found" if result["found"] else "no_result"))))
                print(f"[Worker] Monitoring result for {task.pokemon}: {mon_status}")
                self._record_channel_result(task.channel_id, mon_status)

                # Check for encounter limit — start a 10-minute cooldown.
                # During cooldown: don't teleport to new targets, but keep the
                # logs page fresh so BgScanner can still record encounters that
                # appear as old ones expire and free up encounter slots.
                if result.get("encounter_limit_reached"):
                    print("[Worker] Encounter limit reached — starting 10-min cooldown (tracking continues)")
                    stats.add_event("system", "Encounter limit reached — 10min cooldown")
                    self._consecutive_no_spawn = 0
                    break_seconds = 600  # 10 minutes
                    for remaining in range(break_seconds, 0, -15):
                        self.current_activity = f"Encounter limit cooldown — {remaining}s remaining…"
                        self.loop_step = 0
                        # Reload logs page to keep WebSocket fresh — encounters
                        # may still appear as expired ones free up slots
                        try:
                            await self.browser.reload_logs_page()
                        except Exception:
                            pass
                        await asyncio.sleep(15)
                    print("[Worker] Encounter limit cooldown over — resuming")
                    continue

                # Track consecutive no-spawn teleports for auto-restart
                # Only count as no-spawn if NO encounters happened at all (game frozen)
                no_spawn = (
                    mon_status == "no_result"
                    and not result.get("new_encounters")
                    and not result.get("non_target_catches")
                    and not result.get("cluster_skip")
                    and not result.get("manual_skip")
                )
                # Don't count no-spawn teleports for 3 minutes after a restart
                # — the game needs time to stabilize and start spawning
                restart_cooldown = 180  # 3 minutes
                time_since_restart = asyncio.get_event_loop().time() - self._last_sx_restart_time
                if time_since_restart < restart_cooldown:
                    if no_spawn:
                        print(f"[Worker] No-spawn (restart cooldown: {int(restart_cooldown - time_since_restart)}s remaining)")
                    # Reset streak during cooldown to prevent restart loops
                    self._consecutive_no_spawn = 0
                elif no_spawn:
                    self._consecutive_no_spawn += 1
                    print(f"[Worker] No-spawn streak: {self._consecutive_no_spawn}/5")
                else:
                    self._consecutive_no_spawn = 0

                # Auto-restart SX game after 5 consecutive no-spawn teleports
                if self._consecutive_no_spawn >= 5:
                    print("[Worker] 5 no-spawn teleports — restarting SX game")
                    stats.add_event("system", "Auto-restarting SX game after 5 no-spawn teleports")
                    self._consecutive_no_spawn = 0
                    self._last_sx_restart_time = asyncio.get_event_loop().time()
                    try:
                        ok = await self.browser.restart_game()
                        if ok:
                            print("[Browser] SX game restarted successfully")
                        else:
                            print("[Browser] SX game restart failed")
                    except Exception as restart_err:
                        print(f"[Browser] SX restart error: {restart_err}")
                    self._bg_baseline_lines = set()
                    await asyncio.sleep(60)  # Wait for game to stabilize after restart

                # Process result
                # First, handle any non-target catches (wild hundos/shundos that were caught)
                for nt_catch in result.get("non_target_catches", []):
                    nt_name = nt_catch.get("pokemon", "unknown")
                    nt_shiny = nt_catch.get("shiny", False)
                    nt_iv_pct = nt_catch.get("iv_percent", 0)
                    print(f"[Worker] Non-target catch: {nt_name} (shiny={nt_shiny}, IV={nt_iv_pct}%)")
                    stats.record_caught(
                        nt_name,
                        cp=nt_catch.get("cp"),
                        iv=nt_catch.get("iv"),
                        shiny=nt_shiny,
                        iv_percent=nt_iv_pct,
                        encounter_id=nt_catch.get("encounter_id"),
                        coords=coords,
                        is_target=False,
                    )
                    # Start visible cooldown on dashboard (2hr max from distance chart)
                    stats.start_cooldown(7200)
                    print(f"[Worker] Non-target catch cooldown started (2h)")
                    # Notify if it's a shundo
                    if nt_shiny and nt_iv_pct == 100:
                        notify_id = config.get("notify_user_id")
                        if notify_id:
                            try:
                                user = await self.client.fetch_user(int(notify_id))
                                if user:
                                    await self._dm(
                                        user,
                                        f"SHUNDO FOUND! {display_name(nt_name)} (non-target) — "
                                        f"{nt_catch.get('cp', '?')}cp, {nt_catch.get('iv', '?')}"
                                    )
                                    print(f"[Worker] Notified user about non-target shundo {nt_name}")
                            except Exception as e:
                                print(f"[Worker] Failed to send shundo DM: {e}")
                    # If the caught Pokemon is a hundo or shundo and is in the
                    # target list, remove it — we already got what we needed.
                    is_hundo_or_shundo = nt_iv_pct == 100 or (nt_shiny and nt_iv_pct == 100)
                    if is_hundo_or_shundo and nt_name.lower() in [t.lower() for t in config.get_hunt_targets()]:
                        print(f"[Worker] Non-target catch {nt_name} is in target list (IV={nt_iv_pct}%, shiny={nt_shiny}) — removing from targets and queue")
                        if config.get("auto_remove_caught", True):
                            if config.get("remove_evolution_line", False):
                                self._remove_evolution_line(nt_name, queue)
                            else:
                                config.remove_target(nt_name)
                            removed = queue.remove_pokemon(nt_name)
                            print(f"[Worker] Auto-removed {nt_name} from targets, purged {removed} queue entries")

                # Target-only mode: if any Pokémon has target-only enabled,
                # the bot only hunts those and ignores everything else in the list.
                # They are still caught normally.
                
                if result.get("manual_skip"):
                    # User manually skipped during monitoring
                    self.skip_current = False
                    self.current_activity = f"Skipped {display_name(task.pokemon)} manually"
                    # Log any encounters that were seen
                    for enc in result.get("new_encounters", []):
                        enc_is_target = enc.get("pokemon", task.pokemon).lower() == task.pokemon.lower()
                        stats.record_encounter(
                            enc.get("pokemon", task.pokemon),
                            cp=enc.get("cp"),
                            iv=enc.get("iv"),
                            shiny=enc.get("shiny", False),
                            iv_percent=enc.get("iv_percent", 0),
                            encounter_id=enc.get("encounter_id"),
                            is_target=enc_is_target,
                        )
                    queue.mark_done(task)
                    print(f"[Worker] {task.pokemon} skipped manually during monitoring")

                elif result["caught"]:
                    self.current_activity = f"Caught {display_name(task.pokemon)}! Starting cooldown…"
                    self.loop_step = 6
                    # Log ALL non-target encounters (shinies/hundos) that appeared
                    caught_enc_id = result.get("encounter_id")
                    for enc in result.get("new_encounters", []):
                        if enc.get("encounter_id") != caught_enc_id:
                            enc_is_target = enc.get("pokemon", task.pokemon).lower() == task.pokemon.lower()
                            stats.record_encounter(
                                enc.get("pokemon", task.pokemon),
                                cp=enc.get("cp"),
                                iv=enc.get("iv"),
                                shiny=enc.get("shiny", False),
                                iv_percent=enc.get("iv_percent", 0),
                                encounter_id=enc.get("encounter_id"),
                                is_target=enc_is_target,
                            )
                    stats.record_caught(
                        task.pokemon,
                        cp=result.get("cp"),
                        iv=result.get("iv"),
                        shiny=result.get("shiny", False),
                        iv_percent=result.get("iv_percent", 0),
                        encounter_id=result.get("encounter_id"),
                        coords=coords,
                        is_target=True,
                    )
                    # Check if it's a shundo (shiny + 100% IV) and notify
                    is_shiny = result.get("shiny", False)
                    iv_percent = result.get("iv_percent", 0)
                    iv_str = str(result.get("iv", ""))
                    is_shundo = is_shiny and (iv_percent == 100 or "100%" in iv_str)
                    if is_shundo:
                        notify_id = config.get("notify_user_id")
                        if notify_id:
                            try:
                                user = await self.client.fetch_user(int(notify_id))
                                if user:
                                    await self._dm(
                                        user,
                                        f"SHUNDO FOUND! {display_name(task.pokemon)} — "
                                        f"{result.get('cp', '?')}cp, {result.get('iv', '?')}"
                                    )
                                    print(f"[Worker] Notified user {notify_id} about shundo {task.pokemon}")
                            except Exception as e:
                                print(f"[Worker] Failed to send shundo DM: {e}")
                    # Cooldown is now per-target (distance from last catch)
                    # Also start a visible global cooldown for dashboard display.
                    stats.start_cooldown(7200)
                    # Auto-remove from targets
                    if config.get("auto_remove_caught", True):
                        if config.get("remove_evolution_line", False):
                            self._remove_evolution_line(task.pokemon, queue)
                        else:
                            config.remove_target(task.pokemon)
                        print(f"[Worker] Auto-removed {task.pokemon} from targets")
                    # Remove from queue
                    queue.mark_done(task)
                    # Also purge any other queue entries for the same Pokemon
                    # (e.g., duplicate spawns from shared feeds)
                    purged = queue.remove_pokemon(task.pokemon)
                    if purged:
                        print(f"[Worker] Purged {purged} additional queue entries for {task.pokemon}")
                    print(f"[Worker] {task.pokemon} caught! Cooldown will apply to next target based on distance.")

                elif result["fled"]:
                    self.current_activity = f"{display_name(task.pokemon)} fled. Moving to next target…"
                    stats.record_fled(task.pokemon)
                    if not getattr(task, "shundo_id", None):  # shundo path records it from the log time
                        stats.record_flee_anchor(coords, reason="fled")
                    queue.mark_done(task)
                    print(f"[Worker] {task.pokemon} fled.")

                elif result.get("skipped_hundo_non_shiny"):
                    # 100% IV but not shiny — skip to next target
                    self.current_activity = f"{display_name(task.pokemon)} is 100% IV but not shiny — moving on…"
                    # Log ALL encounters (target + non-target shinies/hundos)
                    # The target hundo is already in new_encounters from the browser's second pass
                    for enc in result.get("new_encounters", []):
                        enc_is_target = enc.get("pokemon", task.pokemon).lower() == task.pokemon.lower()
                        stats.record_encounter(
                            enc.get("pokemon", task.pokemon),
                            cp=enc.get("cp"),
                            iv=enc.get("iv"),
                            shiny=enc.get("shiny", False),
                            iv_percent=enc.get("iv_percent", 0),
                            encounter_id=enc.get("encounter_id"),
                            is_target=enc_is_target,
                        )
                    queue.mark_done(task)
                    print(f"[Worker] {task.pokemon} is 100% IV but not shiny — skipping")

                elif result["found"]:
                    # Encountered but no catch/fled result
                    # Log ALL encounters including non-target shinies/hundos
                    for enc in result.get("new_encounters", []):
                        enc_is_target = enc.get("pokemon", task.pokemon).lower() == task.pokemon.lower()
                        stats.record_encounter(
                            enc.get("pokemon", task.pokemon),
                            cp=enc.get("cp"),
                            iv=enc.get("iv"),
                            shiny=enc.get("shiny", False),
                            iv_percent=enc.get("iv_percent", 0),
                            encounter_id=enc.get("encounter_id"),
                            is_target=enc_is_target,
                        )
                    
                    # Check if we found a shundo — if so, record as caught immediately
                    # since the user will always catch a shundo. This ensures cooldown
                    # is set correctly even if the catch line scrolls off the SX dashboard
                    # before BgScanner can read it.
                    found_shundo = False
                    for enc in result.get("new_encounters", []):
                        if enc.get("shiny", False) and enc.get("iv_percent", 0) == 100:
                            found_shundo = True
                            shundo_name = enc.get("pokemon", task.pokemon)
                            shundo_cp = enc.get("cp")
                            shundo_iv = enc.get("iv")
                            break
                    
                    if found_shundo:
                        self.current_activity = f"SHUNDO {display_name(shundo_name)} found! Recording catch for cooldown…"
                        self.loop_step = 6
                        stats.record_caught(
                            shundo_name,
                            cp=shundo_cp,
                            iv=shundo_iv,
                            shiny=True,
                            iv_percent=100,
                            encounter_id=None,
                            coords=coords,
                            is_target=True,
                        )
                        # Notify user about the shundo
                        notify_id = config.get("notify_user_id")
                        if notify_id:
                            try:
                                user = await self.client.fetch_user(int(notify_id))
                                if user:
                                    await self._dm(
                                        user,
                                        f"SHUNDO FOUND! {display_name(shundo_name)} — "
                                        f"{shundo_cp}cp, {shundo_iv}"
                                    )
                                    print(f"[Worker] Notified user about shundo {shundo_name}")
                            except Exception as e:
                                print(f"[Worker] Failed to send shundo DM: {e}")
                        # Auto-remove from targets
                        if config.get("auto_remove_caught", True):
                            if config.get("remove_evolution_line", False):
                                self._remove_evolution_line(shundo_name, queue)
                            else:
                                config.remove_target(shundo_name)
                            print(f"[Worker] Auto-removed {shundo_name} from targets (shundo found)")
                        queue.mark_done(task)
                        purged = queue.remove_pokemon(shundo_name)
                        if purged:
                            print(f"[Worker] Purged {purged} additional queue entries for {shundo_name}")
                        print(f"[Worker] SHUNDO {shundo_name} recorded as caught! Cooldown will apply to next target.")
                    else:
                        queue.mark_done(task)
                        print(f"[Worker] {task.pokemon} encountered but result unknown.")

                else:
                    # No result — target didn't appear, but log any shinies/hundos
                    # that were encountered during monitoring
                    self.current_activity = f"No log result for {display_name(task.pokemon)} after {monitor_timeout}s. Moving on…"
                    for enc in result.get("new_encounters", []):
                        enc_is_target = enc.get("pokemon", task.pokemon).lower() == task.pokemon.lower()
                        stats.record_encounter(
                            enc.get("pokemon", task.pokemon),
                            cp=enc.get("cp"),
                            iv=enc.get("iv"),
                            shiny=enc.get("shiny", False),
                            iv_percent=enc.get("iv_percent", 0),
                            encounter_id=enc.get("encounter_id"),
                            is_target=enc_is_target,
                        )
                    queue.mark_done(task)
                    print(f"[Worker] No log result for {task.pokemon} after {monitor_timeout}s — moving to next target")

                if getattr(task, "shundo_id", None):
                    # Use SX's own [CatchPokemon] verdict — an encounter is NOT a catch.
                    outcome, when, enc_id = await self._confirm_shundo_outcome(
                        task, baseline_logs, teleport_started_at,
                        wait_s=0 if mon_status == "no_result" else 30)
                    if outcome == "caught":
                        stats.record_caught(task.pokemon, cp=result.get("cp"), iv=result.get("iv"),
                                            shiny=True, iv_percent=100, encounter_id=enc_id,
                                            coords=coords, is_target=True, at_time=when)
                        print(f"[Shundo] Confirmed catch of {task.pokemon} at {coords} "
                              f"({time.strftime('%H:%M:%S', time.localtime(when))}) — cooldown from here")
                    elif outcome == "fled":
                        stats.record_flee_anchor(coords, at_time=when, reason="fled")
                        if mon_status != "fled":
                            stats.record_fled(task.pokemon)
                        print(f"[Shundo] {task.pokemon} FLED at {coords} — treating as cooldown reset")
                        mon_status = "fled"
                    elif mon_status != "no_result":
                        # Saw the encounter but no verdict: assume a catch may have happened here
                        stats.record_flee_anchor(coords, at_time=time.time(), reason="unconfirmed", reset=False)
                        print(f"[Shundo] {task.pokemon}: no catch/flee line — adding a safety cooldown from {coords}")
                    self._shundo_finish(task, mon_status, outcome == "caught")
                print(f"[Worker] Target {task.pokemon} done — checking queue for next catchable target")

            except Exception as e:
                print(f"[Worker] Error: {e}")
                import traceback
                traceback.print_exc()
                # Mark the task as done to prevent it from being stuck in 'processing'
                if task is not None:
                    try:
                        queue.mark_done(task)
                    except Exception:
                        pass
                await asyncio.sleep(5)
            except BaseException as e:
                # Catches CancelledError, KeyboardInterrupt, etc.
                print(f"[Worker] BaseException (worker exiting): {type(e).__name__}: {e}")
                import traceback
                traceback.print_exc()
                raise

        print(f"[Worker] Stopped (running={self.running})")

    def run(self, token):
        """Start the Discord bot (blocking)."""
        self.client.run(token)

    async def start(self, token):
        """Start the Discord bot (async, non-blocking)."""
        await self.client.start(token)

    async def close(self):
        """Clean up and close the bot."""
        self.running = False
        if self._bg_scanner_task and not self._bg_scanner_task.done():
            self._bg_scanner_task.cancel()
        if self.browser:
            await self.browser.close()
        if not self.client.is_closed():
            await self.client.close()
