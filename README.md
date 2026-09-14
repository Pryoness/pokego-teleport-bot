# PokeGo Teleport Bot v2.0

Automated Pokemon coordinate teleporting with keyword targeting, priority queue, DSP timer tracking, log monitoring, catch cooldowns, statistics, and a web dashboard.

## Prerequisites

- **Python 3.9 or higher**
- macOS, Linux (Ubuntu/Debian), or Windows

## Installation — macOS

1. Download and extract this folder.

2. Open Terminal and navigate to the folder:
   ```bash
   cd /path/to/pokego-teleport-bot
   ```

3. Double-click `start.command` to run, OR run manually:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   playwright install chromium
   python3 main.py
   ```

## Installation — Linux (Ubuntu/Debian)

1. Upload the folder to your server (using scp, rsync, or a file transfer tool).

2. SSH into the server and navigate to the folder:
   ```bash
   cd /path/to/pokego-teleport-bot
   ```

3. Run the setup script (installs system dependencies, Python packages, and Playwright browser):
   ```bash
   chmod +x setup.sh
   ./setup.sh
   ```

4. Start the bot:
   ```bash
   chmod +x start.sh
   ./start.sh
   ```

### Running as a background service (systemd)

To keep the bot running after you disconnect from SSH:

1. Edit the service file:
   ```bash
   nano pokebot.service
   ```
   Replace `REPLACE_WITH_USERNAME` with your Linux username and `REPLACE_WITH_BOT_PATH` with the full path to the bot folder.

2. Install the service:
   ```bash
   sudo cp pokebot.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable pokebot
   sudo systemctl start pokebot
   ```

3. Check status:
   ```bash
   sudo systemctl status pokebot
   ```

4. View logs:
   ```bash
   sudo journalctl -u pokebot -f
   ```

5. Stop/restart:
   ```bash
   sudo systemctl stop pokebot
   sudo systemctl restart pokebot
   ```

> **Ubuntu vs Debian**: Both work identically. Ubuntu is recommended since it's more common and has better community support. The setup script auto-detects either.
>
> **Server requirements**: 2GB RAM minimum, 10GB disk. The bot runs headless (no display needed).

## Configuration

Open **`config.json`** in any text editor and fill in:

| Field | What to put | Required |
|-------|-------------|----------|
| `discord_email` | Your Discord account email | Yes |
| `discord_password` | Your Discord account password | Yes |
| `discord_token` | Your Discord account token | Yes |
| `controller_user_id` | Your Discord user ID | Yes |
| `watch_channel_id` | Channel ID to monitor (can also set via Discord) | Recommended |
| `target_pokemon` | List of Pokemon names to hunt (can also set via Discord) | Recommended |
| `sx_dashboard_url` | Pre-filled with your SX dashboard ownspots URL | Pre-filled |
| `sx_logs_url` | Pre-filled with your SX dashboard logs URL | Pre-filled |
| `sx_login_url` | SX login page URL (default: `https://dashboard.sx-pokego.xyz/#`) | Pre-filled |
| `sx_username` | Your SX account username/email for auto-login | **New** |
| `sx_password` | Your SX account password for auto-login | **New** |
| `catch_cooldown_seconds` | Cooldown after catching (default: 7200 = 2hrs) | Pre-filled |
| `walk_after_teleport` | Walk 1m after teleporting (default: true) | Pre-filled |
| `auto_remove_caught` | Remove caught Pokemon from targets (default: true) | Pre-filled |
| `monitor_timeout_seconds` | How long to watch logs for catch/fled (default: 10) | Pre-filled |
| `catch_log_patterns` | Log text patterns indicating a catch | Adjustable |
| `fled_log_patterns` | Log text patterns indicating a fled | Adjustable |
| `shiny_log_patterns` | Log text patterns indicating shiny | Adjustable |
| `web_port` | Port for web dashboard (default: 8765) | Pre-filled |
| `headless` | Run browser in background with no window (default: true) | Pre-filled |
| `remove_evolution_line` | Remove entire evolution chain when a target is caught (default: false) | Optional |
| `additional_watch_channels` | Extra Discord channels to monitor (comma-separated IDs) | Optional |
| `dm_delay_min_seconds` | Min random delay before Discord messages (default: 2) | Pre-filled |
| `dm_delay_max_seconds` | Max random delay before Discord messages (default: 7) | Pre-filled |

### Distance-Based Cooldown

After catching a Pokemon, the cooldown is calculated based on the distance from the last catch location to the new target, using the standard Pokemon GO cooldown chart. The reference point is always the last catch's coordinates, not the teleport location. Cooldown ranges from ~1 minute (very close) to 2 hours (very far). After 2 hours, you can teleport anywhere.

### Headless Mode

The bot runs the SX browser **headless** (no visible window) by default. This means you can use your MacBook normally while the bot runs in the background.

- **With SX credentials set** (`sx_username` + `sx_password`): The bot auto-logs in headlessly — no window ever appears.
- **Without credentials**: A visible browser window opens for manual login, then automatically switches to headless after you log in.
- **SX Login button**: If your session expires mid-run, click "SX Login" on the dashboard to open a visible window for re-authentication. It switches back to headless automatically after login.

### Finding Your Discord Token
1. Open Discord in your web browser
2. Open Developer Tools (F12), go to Network tab
3. Click any channel in Discord
4. Find a request to `discord.com`, look at Request Headers
5. Copy the `Authorization` header value

### Finding Your Discord User ID
1. Settings > Advanced > enable Developer Mode
2. Right-click your username > Copy User ID

### Finding a Channel ID
1. Right-click the channel name > Copy Channel ID

## Running

```bash
python3 main.py
```

### First Run
1. A browser window opens to the SX dashboard — log in if needed (session is saved).
2. The logs page opens in a second tab automatically.
3. The web dashboard is available at `http://127.0.0.1:8765`.

### Quick Start
1. Open the web dashboard or use Discord commands:
   - `@bot add target Charmander` — add Pokemon to hunt
   - `@bot set watch <channel_id>` — set the channel to monitor
   - `@bot start` — begin monitoring
2. The bot watches the channel for messages containing your target Pokemon names.
3. When found, it extracts the coordinate URL and DSP timer, adds to a priority queue.
4. The worker processes the queue: teleport, walk 1m, monitor logs for catch/fled.
5. If caught: 2hr cooldown starts, Pokemon removed from targets (if auto-remove is on).
6. Stats are tracked: teleports, catches, shundos, hundos, shinies, and more.

## Discord Commands

| Command | Description |
|---------|-------------|
| `@bot add target <pokemon>` | Add a Pokemon to hunt |
| `@bot remove target <pokemon>` | Remove a Pokemon from hunt list |
| `@bot targets` | Show target list |
| `@bot queue` | Show current queue |
| `@bot clear queue` | Clear the queue |
| `@bot stats` | Show statistics |
| `@bot caught` | Show caught Pokemon |
| `@bot start` | Start monitoring |
| `@bot stop` | Stop monitoring |
| `@bot status` | Show current status |
| `@bot set watch <channel_id>` | Set watch channel |
| `@bot set cooldown <seconds>` | Set catch cooldown |
| `@bot set walk_after_teleport on/off` | Toggle walk after teleport |
| `@bot set auto_remove on/off` | Toggle auto-remove caught |
| `@bot set monitor_timeout <seconds>` | Set log monitor timeout |
| `@bot set walk_distance <meters>` | Set walk distance |
| `@bot help` | Show all commands |

## Web Dashboard

Open `http://127.0.0.1:8765` in your browser to:
- View live stats (teleports, catches, shundos, hundos, shinies)
- See and manage the queue
- Add/remove target Pokemon
- Toggle settings (walk after teleport, auto-remove, etc.)
- View event log
- Start/stop the bot
- Clear cooldown or reset stats

## How It Works

```
Discord Channel (watch_channel_id)
    │
    ├── Message contains target Pokemon keyword
    │   ├── Extract second URL (coordinate page)
    │   ├── Parse DSP timer ("DSP in XXm")
    │   └── Add to priority queue (sorted by expiry)
    │
    ▼
Worker Loop
    │
    ├── Check catch cooldown (2hrs after catch)
    ├── Get highest priority target (soonest DSP expiry)
    ├── Open coordinate URL → extract lat,lng
    ├── Teleport to coordinates
    ├── Walk 1m (offset coordinates + Walk button)
    ├── Monitor SX logs for catch/fled (max 10s)
    │   ├── Caught → record stats, start 2hr cooldown, remove from targets
    │   ├── Fled → record stats, move on
    │   └── No result → move on after timeout
    └── Repeat
```

## Log Patterns

The bot monitors the SX dashboard logs page for catch/fled indicators. The default patterns are configurable in `config.json`:

- **Catch patterns**: `[Catch]`, `caught`, `Catch success`
- **Fled patterns**: `[Fled]`, `fled`, `Catch failed`
- **Shiny patterns**: `shiny`, `Shiny`

If the actual log format differs, update these patterns to match what you see in the SX logs page.

## File Structure

```
pokego-teleport-bot/
├── main.py              # Entry point
├── bot.py               # Discord bot, commands, worker loop
├── browser.py           # Playwright browser automation
├── config.py            # Config management
├── queue_manager.py     # Priority queue with DSP timers
├── stats.py             # Statistics tracking
├── web_server.py        # FastAPI web dashboard
├── web/index.html       # Dashboard frontend
├── config.json          # Your configuration
├── requirements.txt     # Dependencies
└── README.md            # This file
```

## Warning

Using a personal Discord account token (self-bot) violates Discord's Terms of Service. Use at your own risk. Consider using a secondary account.
