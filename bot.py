"""Discord self-bot with command handling, keyword targeting, and worker loop."""

import discord
import asyncio
import re
import shlex
import time
import random

from config import config
from stats import stats, haversine_km
from queue_manager import queue
from browser import SXBrowser
from pokemon_data import is_valid_pokemon, get_sprite_url


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
        self._coord_response_future = None  # Future for receiving coord responses from button clicks
        self._reveal_coords_lock = asyncio.Lock()  # Serialize Reveal Coords clicks to prevent race conditions
        self._recent_coords = {}  # coords_str -> expiry timestamp, prevents re-teleporting to same location
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
            if self.browser is not None:
                print("[Discord] Reconnected; browser already running.")
                return
            print("[Discord] Starting browser...")
            self.browser = SXBrowser(config)
            await self.browser.start()
            print("[Discord] Browser ready!")
            self._print_banner()

        @client.event
        async def on_message(message):
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
                ):
                    await self._handle_command(message, parts)
                    return

        # If running and in the watch channel, check for target Pokemon keywords
        if self.running and is_watch_channel:
            await self._check_for_targets(message)

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
            if targets:
                lines = ["**Target Pokemon:**"]
                for t in targets:
                    tag = " (HIGH)" if t in high else ""
                    lines.append(f"• {t}{tag}")
                await self._send(message.channel, "\n".join(lines))
            else:
                await self._send(message.channel, "No targets set. Use `@bot add target <pokemon>`")

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
            elif not config.get_targets():
                await self._send(message.channel, 
                    "No target Pokemon set! Use `@bot add target <pokemon>` first."
                )
            else:
                self.running = True
                stats.record_start()
                if self.worker_task is None or self.worker_task.done():
                    self.worker_task = asyncio.create_task(self._worker_loop())
                targets = config.get_targets()
                await self._send(message.channel, 
                    f"Started monitoring channel `{config.get('watch_channel_id')}` "
                    f"for: {', '.join(targets)}\n"
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
        targets = config.get_targets()
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
                # Found a target! Try "Reveal Coords" button first, then fall back to URL extraction
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

                # Solo mode: if any Pokémon has solo enabled, only queue those
                solo_names = config.get("target_only_pokemon", [])
                if solo_names and pokemon.lower() not in solo_names:
                    continue  # Skip — not the solo target

                # Add to queue with per-Pokemon limit
                priority = 0 if config.is_high_priority(pokemon) else 1
                queue_limit = config.get("queue_limit_per_pokemon", 5)
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

    async def _worker_loop(self):
        """Background worker that processes the queue: teleport, walk, monitor logs."""
        print(f"[Worker] Started (running={self.running})")
        heartbeat_counter = 0
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

                # Select best task using proximity-based ordering
                # This handles cooldown checking and picks the nearest catchable target
                task, coords = await self._select_best_task()
                if task is None:
                    self.current_activity = "Waiting for targets in channel…"
                    self.loop_step = 1
                    # Heartbeat every 30 seconds so we know the bot is alive
                    heartbeat_counter += 1
                    if heartbeat_counter % 30 == 0:
                        print(f"[Worker] Heartbeat — watching for targets… ({heartbeat_counter}s)")
                    await asyncio.sleep(1)
                    continue

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
                baseline_logs = await self.browser.snapshot_logs()

                # Teleport
                print(f"[Worker] Teleporting to {task.pokemon:12s} at {coords}")
                self.current_activity = f"Teleporting to {display_name(task.pokemon)} at {coords}…"
                self.loop_step = 3
                await self.browser.teleport(coords)
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
                    pokemon_name=task.pokemon,
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
                    if is_hundo_or_shundo and nt_name.lower() in [t.lower() for t in config.get_targets()]:
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
                    # No need to start a global cooldown — next target will check
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
        if self.browser:
            await self.browser.close()
        if not self.client.is_closed():
            await self.client.close()
