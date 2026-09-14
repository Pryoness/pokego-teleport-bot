"""Entry point - starts Discord bot and web dashboard concurrently."""

import asyncio
import socket
import sys
import os
import time
import io

from config import config
from stats import stats
from queue_manager import queue
from bot import PokeBot
from web_server import app
import pokemon_data  # Pre-loads Pokemon list on import
import uvicorn


# Set up terminal log file — captures all stdout/stderr output
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'terminal.log')
MAX_LOG_SIZE = 10 * 1024 * 1024  # 10 MB — rotate if larger

# Rotate old log if it's too big, then start fresh
if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > MAX_LOG_SIZE:
    old_log = LOG_PATH + '.old'
    # Remove previous .old if it exists, then rotate
    if os.path.exists(old_log):
        os.remove(old_log)
    os.rename(LOG_PATH, old_log)

# Truncate at start of each run
with open(LOG_PATH, 'w') as f:
    f.write(f"=== PokeGo Teleport Bot — Session started {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")

class TeeLogger:
    """Writes output to both terminal and a log file simultaneously.
    Auto-rotates when the log exceeds MAX_LOG_SIZE."""
    def __init__(self, log_path):
        self.log_path = log_path
        self.log_file = open(log_path, 'a', buffering=1)  # Line-buffered
        self.terminal = sys.stdout
        self._written = 0

    def _maybe_rotate(self):
        """Check if rotation is needed and do it."""
        try:
            if os.path.getsize(self.log_path) > MAX_LOG_SIZE:
                self.log_file.close()
                old_log = self.log_path + '.old'
                if os.path.exists(old_log):
                    os.remove(old_log)
                os.rename(self.log_path, old_log)
                self.log_file = open(self.log_path, 'a', buffering=1)
        except Exception:
            pass  # Don't crash the bot over log rotation

    def write(self, message):
        self.terminal.write(message)
        self.log_file.write(message)
        self._written += len(message)
        # Check rotation every ~1KB of writes
        if self._written > 1024:
            self._written = 0
            self._maybe_rotate()

    def flush(self):
        self.terminal.flush()
        self.log_file.flush()

    def isatty(self):
        return self.terminal.isatty()

    def fileno(self):
        return self.terminal.fileno()

    def close(self):
        self.log_file.close()

tee = TeeLogger(LOG_PATH)
sys.stdout = tee
sys.stderr = tee


def main():
    # Validate required config
    if not config.get("discord_token") or config.get("discord_token") == "YOUR_DISCORD_ACCOUNT_TOKEN_HERE":
        print("ERROR: Please set your Discord account token in config.json")
        print("File location: config.json in the script directory")
        sys.exit(1)

    if not config.get("controller_user_id") or config.get("controller_user_id") == "YOUR_DISCORD_USER_ID_HERE":
        print("ERROR: Please set your Discord user ID in config.json")
        print("File location: config.json in the script directory")
        sys.exit(1)

    print("=" * 60)
    print("  PokeGo Teleport Bot v3.0")
    print("=" * 60)

    # Find local IP for iPhone access
    local_ip = "127.0.0.1"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        pass

    web_port = config.get("web_port", 8765)
    print(f"  Web Dashboard (Mac):   http://127.0.0.1:{web_port}")
    print(f"  Web Dashboard (iPhone): http://{local_ip}:{web_port}")
    print()

    bot = PokeBot()

    # Set up the web server to reference the bot
    app.state.bot = bot
    app.state.running = False

    # Configure uvicorn to run in the same event loop as the Discord bot
    # Bind to 0.0.0.0 so iPhone on the same WiFi can access the dashboard
    server_config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=web_port,
        log_level="warning",
    )
    server = uvicorn.Server(server_config)

    async def run_both():
        """Run Discord bot and web server concurrently."""
        # Start web server in background
        web_task = asyncio.create_task(server.serve())
        # Give the web server a moment to start
        await asyncio.sleep(1)
        print(f"[Main] Web dashboard running at http://{local_ip}:{web_port}")

        # Start Discord bot (non-blocking, runs in same event loop)
        bot_task = asyncio.create_task(bot.start(config.get("discord_token")))

        try:
            await asyncio.gather(web_task, bot_task)
        except asyncio.CancelledError:
            pass
        finally:
            server.should_exit = True
            await bot.close()

    try:
        asyncio.run(run_both())
    except KeyboardInterrupt:
        print("\n[Main] Shutting down (KeyboardInterrupt)...")
    except Exception as e:
        print(f"[Main] Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Ensure the log file is flushed and closed
        try:
            tee.flush()
            tee.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
