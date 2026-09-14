"""FastAPI web dashboard server."""

import os
import json
import time
import asyncio
import secrets
import hashlib
from fastapi import FastAPI, HTTPException, Request, Response, Depends
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional
from pathlib import Path

from config import config
from stats import stats
from queue_manager import queue
from pokemon_data import is_valid_pokemon, get_sprite_url, get_all_pokemon

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")

app = FastAPI(title="PokeGo Teleport Bot Dashboard")

# --- Dashboard Auth ---
AUTH_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard_auth.json")


def _load_auth():
    """Load dashboard credentials from dashboard_auth.json.
    Returns (username, password) or None if no auth file / disabled."""
    if not os.path.exists(AUTH_FILE):
        return None
    try:
        with open(AUTH_FILE, "r") as f:
            data = json.load(f)
        username = data.get("username", "").strip()
        password = data.get("password", "")
        if not username or not password:
            return None
        return (username, password)
    except Exception:
        return None


_AUTH_CREDENTIALS = _load_auth()
_SESSION_SECRET = secrets.token_hex(32)


def _make_session_token(username: str) -> str:
    """Create a signed session token (HMAC of username + secret)."""
    msg = hashlib.sha256(f"{username}:{_SESSION_SECRET}".encode()).hexdigest()
    return f"{username}:{msg}"


def _verify_session_token(token: str) -> bool:
    """Verify a session token matches our secret."""
    if not token or ":" not in token:
        return False
    username, sig = token.split(":", 1)
    expected = _make_session_token(username)
    return secrets.compare_digest(token, expected)


def require_auth(request: Request):
    """FastAPI dependency: require a valid session cookie.
    If no auth file exists, all requests pass through (backward compatible)."""
    if _AUTH_CREDENTIALS is None:
        return  # No auth file — open access
    token = request.cookies.get("pokebot_session")
    if not _verify_session_token(token):
        raise HTTPException(status_code=401, detail="Unauthorized")


# Apply require_auth to all /api/* routes via middleware
@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Check auth on all /api/ routes except /api/login and /api/monitor-login.
    Non-API routes (dashboard HTML/JS) are always served — the frontend
    handles the redirect to a login page when the API returns 401.
    Monitor mode: GET requests allowed with monitor cookie, all mutations blocked."""
    path = request.url.path
    method = request.method
    # Skip auth for login/monitor-login endpoints and non-API routes
    if path in ("/api/login", "/api/monitor-login") or not path.startswith("/api/"):
        return await call_next(request)
    # If no auth configured, allow everything
    if _AUTH_CREDENTIALS is None:
        return await call_next(request)
    # Check admin session first — full access
    token = request.cookies.get("pokebot_session")
    if _verify_session_token(token):
        return await call_next(request)
    # No admin session — check for monitor token (read-only)
    monitor_token = request.cookies.get("pokebot_monitor")
    if monitor_token and _verify_session_token(monitor_token):
        if method == "GET":
            return await call_next(request)
        return JSONResponse(status_code=403, content={"detail": "Monitor mode is read-only"})
    return JSONResponse(status_code=401, content={"detail": "Unauthorized"})


class LoginRequest(BaseModel):
    username: str
    password: str


@app.post("/api/login")
async def api_login(req: LoginRequest, response: Response):
    """Login and set a session cookie."""
    if _AUTH_CREDENTIALS is None:
        return {"status": "ok", "message": "No auth configured"}
    expected_user, expected_pass = _AUTH_CREDENTIALS
    if req.username == expected_user and req.password == expected_pass:
        token = _make_session_token(req.username)
        response.set_cookie(
            key="pokebot_session",
            value=token,
            httponly=True,
            samesite="lax",
            max_age=86400 * 7,  # 7 days
            path="/",
        )
        return {"status": "ok", "message": "Logged in"}
    raise HTTPException(status_code=401, detail="Invalid credentials")


@app.post("/api/logout")
async def api_logout(response: Response):
    """Clear the session cookie."""
    response.delete_cookie("pokebot_session", path="/")
    return {"status": "ok", "message": "Logged out"}


@app.get("/api/auth-check")
async def api_auth_check(request: Request):
    """Check if the current session is valid."""
    if _AUTH_CREDENTIALS is None:
        return {"authenticated": True, "auth_required": False}
    token = request.cookies.get("pokebot_session")
    if _verify_session_token(token):
        return {"authenticated": True, "auth_required": True}
    # Also check for monitor cookie
    monitor_token = request.cookies.get("pokebot_monitor")
    if monitor_token:
        return {"authenticated": True, "auth_required": True, "monitor": True}
    return {"authenticated": False, "auth_required": True}


@app.post("/api/monitor-login")
async def api_monitor_login(response: Response):
    """Set a monitor-only cookie for read-only access. No credentials needed —
    the monitor link is meant to be shared with trusted people."""
    token = _make_session_token("monitor")
    response.set_cookie(
        key="pokebot_monitor",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=86400 * 7,  # 7 days
        path="/",
    )
    return {"status": "ok", "message": "Monitor access granted"}


class SettingsUpdate(BaseModel):
    watch_channel_id: Optional[str] = None
    walk_after_teleport: Optional[bool] = None
    walk_distance_meters: Optional[int] = None
    auto_remove_caught: Optional[bool] = None
    monitor_timeout_seconds: Optional[int] = None
    notify_user_id: Optional[str] = None
    skip_non_shiny: Optional[bool] = None
    queue_limit_per_pokemon: Optional[int] = None
    sx_login_url: Optional[str] = None
    sx_username: Optional[str] = None
    sx_password: Optional[str] = None
    remove_evolution_line: Optional[bool] = None
    additional_watch_channels: Optional[str] = None  # Comma-separated channel IDs
    cluster_skip_threshold: Optional[int] = None
    min_dsp_seconds: Optional[int] = None  # Skip targets with DSP below this (default 120)
    background_image_url: Optional[str] = None  # Custom dashboard background image (desktop)
    background_image_url_mobile: Optional[str] = None  # Custom dashboard background image (mobile)


class TargetAdd(BaseModel):
    name: str
    priority: Optional[str] = None  # "high" or "low"


# --- Routes ---

# --- Static file serving for React build ---
# Serve assets (JS, CSS, images) from web/assets/
_web_assets = Path(WEB_DIR) / "assets"
if _web_assets.exists():
    app.mount("/assets", StaticFiles(directory=str(_web_assets)), name="assets")

# Serve other static files (favicon, etc.)
_web_static = Path(WEB_DIR)
if _web_static.exists():
    for _f in _web_static.iterdir():
        if _f.is_file() and _f.name != "index.html":
            _name = _f.name
            
            def _make_static(path: str):
                async def _serve():
                    return FileResponse(path)
                return _serve
            
            app.add_api_route(f"/{_name}", _make_static(str(_f)), methods=["GET"])


@app.get("/")
async def index():
    return FileResponse(os.path.join(WEB_DIR, "index.html"))


# --- API Routes (must come before SPA fallback) ---

@app.get("/api/status")
async def api_status():
    bot = getattr(app.state, "bot", None)
    running = getattr(bot, "running", False) if bot else False
    activity = getattr(bot, "current_activity", "Idle") if bot else "Idle"
    loop_step = getattr(bot, "loop_step", 0) if bot else 0
    paused = getattr(bot, "paused", False) if bot else False
    current_coords = getattr(bot, "current_coords", None) if bot else None
    return {
        "running": running,
        "in_cooldown": stats.is_in_cooldown(),
        "cooldown_remaining": stats.cooldown_remaining(),
        "catch_cooldown_info": stats.get_catch_cooldown_info(current_coords),
        "current_coords": current_coords,
        "queue_size": queue.size(),
        "targets": config.get_targets(),
        "current_activity": activity,
        "loop_step": loop_step,
        "paused": paused,
    }


@app.get("/api/last_catch")
async def api_last_catch():
    return stats.get_last_catch()


@app.post("/api/last_catch")
async def api_set_last_catch(payload: dict):
    lat = payload.get("lat")
    lng = payload.get("lng")
    minutes_ago = payload.get("minutes_ago", 0)
    if lat is None or lng is None:
        return {"error": "lat and lng required"}
    try:
        catch_time = time.time() - (float(minutes_ago) * 60)
        stats.set_manual_catch(float(lat), float(lng), catch_time)
        return {"ok": True, "message": f"Last catch set to ({lat}, {lng}), {minutes_ago}m ago"}
    except (ValueError, TypeError) as e:
        return {"error": str(e)}


@app.delete("/api/last_catch")
async def api_clear_last_catch():
    stats.clear_manual_catch()
    return {"ok": True, "message": "Last catch cleared"}


@app.get("/api/settings")
async def api_settings():
    data = config.get_all()
    # Don't expose the Discord token
    data.pop("discord_token", None)
    return data


@app.post("/api/settings")
async def api_update_settings(settings: SettingsUpdate):
    updates = {k: v for k, v in settings.dict().items() if v is not None}
    # Parse additional_watch_channels from comma-separated string to list
    if "additional_watch_channels" in updates:
        raw = updates["additional_watch_channels"]
        if isinstance(raw, str):
            channels = [c.strip() for c in raw.split(",") if c.strip()]
            updates["additional_watch_channels"] = channels
    if updates:
        config.update(updates)
    return {"status": "ok", "updated": updates}


@app.get("/api/targets")
async def api_targets():
    targets = config.get_targets()
    high = config.get_high_priority()
    result = []
    for t in targets:
        result.append({
            "name": t,
            "sprite": get_sprite_url(t),
            "priority": "high" if t in high else "low",
            "target_only": config.is_target_only(t),
        })
    return {"targets": result}


@app.post("/api/targets")
async def api_add_target(target: TargetAdd):
    # Support comma-separated multiple Pokemon
    names = [n.strip().lower() for n in target.name.split(",") if n.strip()]
    if not names:
        return JSONResponse(status_code=400, content={"status": "error", "message": "No Pokemon names provided"})
    
    priority = target.priority or "low"
    added = []
    failed = []
    for name in names:
        if not is_valid_pokemon(name):
            failed.append(name)
            continue
        config.add_target(name)
        if priority == "high":
            config.add_high_priority(name)
        added.append(name)
    
    if not added:
        return JSONResponse(status_code=400, content={"status": "error", "message": f"Invalid: {', '.join(failed)}"})
    return {"status": "ok", "targets": config.get_targets(), "added": added, "failed": failed}


@app.post("/api/targets/{name}/priority")
async def api_set_priority(name: str, priority: str):
    if priority == "high":
        config.add_high_priority(name)
    elif priority == "low":
        config.remove_high_priority(name)
    else:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Priority must be 'high' or 'low'"})
    return {"status": "ok", "targets": config.get_targets()}


@app.delete("/api/targets/{name}")
async def api_remove_target(name: str):
    config.remove_target(name)
    return {"status": "ok", "targets": config.get_targets()}


@app.post("/api/targets/{name}/target-only")
async def api_toggle_target_only(name: str):
    config.toggle_target_only(name)
    return {"status": "ok", "targets": config.get_targets()}


@app.get("/api/queue")
async def api_queue():
    return {"queue": queue.get_queue(), "size": queue.size()}


@app.delete("/api/queue")
async def api_clear_queue():
    queue.clear()
    return {"status": "ok", "message": "Queue cleared"}


@app.get("/api/stats")
async def api_stats():
    data = stats.get_stats()
    data["teleport_locations"] = stats.get_teleport_locations()
    return data


@app.get("/api/map")
async def api_map():
    return {"locations": stats.get_teleport_locations()}


@app.get("/api/events")
async def api_events(limit: int = 50):
    return {"events": stats.get_events(limit)}


@app.post("/api/start")
async def api_start():
    # Start or resume the bot
    app.state.running = True
    if hasattr(app.state, "bot"):
        app.state.bot.running = True
        app.state.bot.paused = False  # Unpause if was paused
        stats.record_start()  # Resumes timer without resetting
        # Only create worker task if it doesn't exist or is done
        if app.state.bot.worker_task is None or app.state.bot.worker_task.done():
            app.state.bot.worker_task = asyncio.create_task(app.state.bot._worker_loop())
    return {"status": "ok", "message": "Started"}


@app.post("/api/stop")
async def api_stop():
    # Stop = pause everything (worker loop stays alive but pauses, timer freezes)
    if hasattr(app.state, "bot"):
        app.state.bot.paused = True  # Pause the worker loop
        app.state.bot.running = True   # Keep loop alive so it can resume
        stats.record_stop()  # Freeze the timer
    app.state.running = True  # Keep flag so loop stays alive
    return {"status": "ok", "message": "Paused"}


@app.post("/api/clear-cooldown")
async def api_clear_cooldown():
    stats.clear_cooldown()
    stats.clear_manual_catch()
    return {"status": "ok", "message": "Cooldown and last catch cleared"}


@app.get("/api/pokemon-list")
async def api_pokemon_list():
    return {"pokemon": get_all_pokemon()}


@app.post("/api/reset-stats")
async def api_reset_stats():
    stats.reset()
    return {"status": "ok", "message": "Stats reset"}


@app.post("/api/sx-login")
async def api_sx_login():
    """Open a visible browser window for manual SX login.
    After login is detected, automatically switches back to headless mode.
    Use this when the session expires and you need to re-authenticate.
    """
    if hasattr(app.state, "bot") and app.state.bot.browser:
        browser = app.state.bot.browser
        try:
            # Run login in a background task so it doesn't block the API response
            asyncio.create_task(browser.open_login_window())
            return {"status": "ok", "message": "Opening login window — log in and it will continue automatically"}
        except Exception as e:
            return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})
    return JSONResponse(status_code=400, content={"status": "error", "message": "Browser not started. Start the bot first."})


@app.post("/api/sx-login-back")
async def api_sx_login_back():
    """Navigate back to the SX dashboard after login."""
    if hasattr(app.state, "bot") and app.state.bot.browser:
        browser = app.state.bot.browser
        if browser.ownspots_page:
            try:
                await browser.ownspots_page.goto(
                    config.get("sx_dashboard_url"), wait_until="domcontentloaded"
                )
                return {"status": "ok", "message": "Navigated back to SX dashboard"}
            except Exception as e:
                return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})
    return JSONResponse(status_code=400, content={"status": "error", "message": "Browser not started."})


@app.post("/api/skip")
async def api_skip():
    """Skip the current target and move to the next one."""
    if hasattr(app.state, "bot"):
        app.state.bot.skip_current = True
        return {"status": "ok", "message": "Skipping current target"}
    return JSONResponse(status_code=400, content={"status": "error", "message": "Bot not initialized"})


@app.post("/api/pause")
async def api_pause():
    """Pause or resume the bot's worker loop."""
    if hasattr(app.state, "bot"):
        bot = app.state.bot
        bot.paused = not getattr(bot, "paused", False)
        state = "paused" if bot.paused else "resumed"
        print(f"[API] Bot {state}")
        return {"status": "ok", "paused": bot.paused, "message": f"Bot {state}"}
    return JSONResponse(status_code=400, content={"status": "error", "message": "Bot not initialized"})


@app.delete("/api/queue-item/{message_id}")
async def api_remove_queue_item(message_id: str):
    """Remove a single queue item by message_id."""
    removed = queue.remove_by_message_id(message_id)
    return {"status": "ok", "removed": removed}


@app.delete("/api/queue/{pokemon}")
async def api_remove_from_queue(pokemon: str):
    """Remove a specific Pokemon from the queue."""
    removed = queue.remove_pokemon(pokemon.lower())
    return {"status": "ok", "removed": removed, "message": f"Removed {removed} target(s) from queue"}


# --- SPA Fallback (must be LAST, after all API routes) ---
@app.get("/{path:path}")
async def spa_fallback(path: str):
    """Serve index.html for any non-API, non-static route (SPA client-side routing)."""
    if path.startswith("api/"):
        raise HTTPException(status_code=404, detail="API endpoint not found")
    file_path = Path(WEB_DIR) / path
    if file_path.is_file():
        return FileResponse(str(file_path))
    return FileResponse(os.path.join(WEB_DIR, "index.html"))
