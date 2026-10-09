"""Focused regression tests; mocks do not click Discord or change persisted state."""
import asyncio
import threading
import time
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from bot import PokeBot
from shundo import parse_alert, species_matches_targets
from stats import StatsTracker, get_cooldown_for_distance

LINK = "https://discord.com/channels/111111111111111111/222222222222222222/333333333333333333"


def content(name="Wooloo", linked=True):
    label = f"[**{name}**]({LINK})" if linked else f"**{name}**"
    return f"{label} <:LVL:1> **12** <:CP:2> **242** __France__ <t:{int(time.time()+600)}:R>"


class AlertTests(unittest.IsolatedAsyncioTestCase):
    def test_formats(self):
        for text in [content(), content(linked=False),
                     content().replace(f"[**Wooloo**]({LINK})", f"**[Wooloo]({LINK})**")]:
            parsed = parse_alert(text)
            self.assertEqual(parsed["species"], "wooloo")
            self.assertEqual(parsed["level"], 12)
            self.assertEqual(parsed["cp"], 242)
            if "](" in text:
                self.assertEqual(parsed["source_message_url"], LINK)
        self.assertEqual(parse_alert(content())["source_message_url"], LINK)

    def test_nested_button(self):
        button = NS(label="Copy", click=AsyncMock())
        self.assertIs(PokeBot._find_copy_button([NS(children=[NS(components=[NS(accessory=button)])])]), button)
        self.assertIsNone(PokeBot._find_copy_button([NS(label="Other")]))

    def test_skip_only(self):
        self.assertTrue(species_matches_targets("wooloo", ["surskit"], ["surskit"]))
        self.assertFalse(species_matches_targets("surskit", ["surskit"], ["surskit"]))

    async def test_resolver_fetches_only_linked_message(self):
        bot = object.__new__(PokeBot)
        button = NS(label="Copy", click=AsyncMock())
        source = NS(id=333333333333333333, author=NS(id=99), components=[button])
        server = NS(id=222222222222222222, guild=NS(id=111111111111111111),
                    fetch_message=AsyncMock(return_value=source))
        fresh = NS(id=1, content=content(), author=NS(id=99), components=[])
        dm = NS(id=10, fetch_message=AsyncMock(return_value=fresh))
        fresh.channel = dm
        bot.client = NS(get_channel=lambda _: server)
        bot._is_pokex_message = lambda m: m.author.id == 99
        bot._in_pokex_dm = lambda m: m.channel.id == 10
        resolved = await bot._resolve_pokex_copy_message(fresh)
        self.assertIs(resolved, source)
        server.fetch_message.assert_awaited_once_with(333333333333333333)
        source.author.id = 98
        with self.assertRaises(ValueError):
            await bot._resolve_pokex_copy_message(fresh)
        source.author.id = 1015820911179481138  # the "Coords" bot that posts linked Copy messages
        self.assertIs(await bot._resolve_pokex_copy_message(fresh), source)
        server.guild.id = 123
        with self.assertRaises(ValueError):
            await bot._resolve_pokex_copy_message(fresh)


class CooldownTests(unittest.TestCase):
    def tracker(self):
        tracker = object.__new__(StatsTracker)
        tracker._lock = threading.RLock()
        tracker._data = tracker._default()
        tracker._data["cooldown_anchors"] = [
            dict(lat=45.8, lng=5.0, t=time.time()-60, why="fled")]
        tracker._logged_encounter_ids = set()
        tracker._caught_encounter_ids = set()
        tracker.save = lambda: None
        return tracker

    def test_flee_only_anchor(self):
        tracker = self.tracker()
        required = tracker.get_catch_cooldown_for_target("52.99,-0.42")
        self.assertGreater(required, 5000)
        info = tracker.get_catch_cooldown_info("52.99,-0.42")
        self.assertLessEqual(abs(info["remaining_seconds"]-required), 1)
        self.assertTrue(info["active"])
        self.assertEqual(info["controlling_anchor"]["why"], "fled")
        self.assertGreater(info["anywhere_remaining_seconds"], 7000)

    def test_reset_keeps_anchors(self):
        tracker = self.tracker()
        anchors = list(tracker._data["cooldown_anchors"])
        tracker.reset()
        self.assertEqual(tracker._data["cooldown_anchors"], anchors)

    def test_expired(self):
        tracker = self.tracker()
        tracker._data["cooldown_anchors"][0]["t"] = time.time()-7300
        self.assertEqual(tracker.get_catch_cooldown_for_target("52.99,-0.42"), 0)
        self.assertFalse(tracker.get_catch_cooldown_info("52.99,-0.42")["active"])
        self.assertEqual(tracker.get_catch_cooldown_info()["anywhere_remaining_seconds"], 0)

    def test_interpolated(self):
        self.assertGreater(get_cooldown_for_distance(890.7), 5400)


if __name__ == "__main__":
    unittest.main()
