"""Playwright browser automation for SX Dashboard and coordinate pages."""

import re
import math
import asyncio
from playwright.async_api import async_playwright


def offset_coordinate(coords_str, meters=80):
    """Calculate a coordinate offset by the given distance in meters (north-east).

    Returns a 'lat,lng' string for the offset position.
    """
    parts = coords_str.split(",")
    lat = float(parts[0].strip())
    lng = float(parts[1].strip()) if len(parts) > 1 else 0.0
    lat_delta = meters / 111320
    lng_delta = meters / (111320 * math.cos(math.radians(lat)))
    return f"{lat + lat_delta:.6f},{lng + lng_delta:.6f}"


class SXBrowser:
    """Manages Playwright browser with persistent profile for SX Dashboard."""

    def __init__(self, config):
        self.config = config
        self.playwright = None
        self.browser_context = None
        self.ownspots_page = None
        self.logs_page = None
        self._last_log_text = ""
        self._needs_restart = False
        self._restarting = False

    async def start(self):
        """Launch headless browser, auto-login to SX, then open dashboard and logs pages.

        Runs entirely in the background (no visible window).
        If no credentials are set, opens a visible window for manual login.
        """
        import os
        self.playwright = await async_playwright().start()
        profile_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            self.config.get("browser_profile_dir", "browser_profile"),
        )
        username = self.config.get("sx_username", "")
        password = self.config.get("sx_password", "")
        login_url = self.config.get("sx_login_url", "https://dashboard.sx-pokego.xyz/#")

        if not username or not password:
            # No credentials — need a visible window for manual login
            print("[Browser] No SX credentials — opening visible window for manual login")
            self.browser_context = await self.playwright.chromium.launch_persistent_context(
                user_data_dir=profile_dir,
                headless=False,
                viewport={"width": 1280, "height": 800},
            )
            if self.browser_context.pages:
                self.ownspots_page = self.browser_context.pages[0]
            else:
                self.ownspots_page = await self.browser_context.new_page()

            await self.ownspots_page.goto(login_url, wait_until="domcontentloaded")
            print("[Browser] Login page opened — please log in manually")
            print("[Browser] After logging in, the bot will continue automatically")

            # Wait for the user to log in — detect when we're on the dashboard
            for _ in range(120):  # Wait up to 2 minutes
                await asyncio.sleep(2)
                current_url = self.ownspots_page.url
                if "dashboard" in current_url and "login" not in current_url.lower():
                    print("[Browser] Detected dashboard — login successful")
                    break
            else:
                print("[Browser] Login wait timeout — continuing anyway")

            # Close the visible browser and reopen headless
            await self.browser_context.close()
            print("[Browser] Switching to headless mode")
            self.browser_context = await self.playwright.chromium.launch_persistent_context(
                user_data_dir=profile_dir,
                headless=True,
                viewport={"width": 1280, "height": 800},
            )
            if self.browser_context.pages:
                self.ownspots_page = self.browser_context.pages[0]
            else:
                self.ownspots_page = await self.browser_context.new_page()
        else:
            # Credentials available — run headless with auto-login
            print("[Browser] Starting headless browser with auto-login")
            self.browser_context = await self.playwright.chromium.launch_persistent_context(
                user_data_dir=profile_dir,
                headless=True,
                viewport={"width": 1280, "height": 800},
            )
            if self.browser_context.pages:
                self.ownspots_page = self.browser_context.pages[0]
            else:
                self.ownspots_page = await self.browser_context.new_page()

            # Auto-login flow
            await self._do_login(self.ownspots_page, username, password, login_url)

        # Open ownspots (teleport) page
        await self.ownspots_page.goto(
            self.config.get("sx_dashboard_url"), wait_until="domcontentloaded"
        )
        print("[Browser] Opened SX dashboard ownspots page (headless)")

        # Open logs page in a separate tab
        self.logs_page = await self.browser_context.new_page()
        await self.logs_page.goto(
            self.config.get("sx_logs_url"), wait_until="domcontentloaded"
        )
        print("[Browser] Opened SX dashboard logs page (headless)")

        # Step 4: Skip coordinate page auth at startup (not needed for Reveal Coords channel)
        # The logic is kept and will run lazily if a pokedex100 URL is encountered
        print("[Browser] Skipping pokedex100 auth at startup (will auth lazily if needed)")

    async def restart(self):
        """Restart the browser after a crash. Closes old context and starts fresh."""
        if self._restarting:
            return
        self._restarting = True
        print("[Browser] Browser crash detected — restarting...")
        # Close old resources safely
        try:
            if self.browser_context:
                await self.browser_context.close()
        except Exception:
            pass
        try:
            if self.playwright:
                await self.playwright.stop()
        except Exception:
            pass
        self.browser_context = None
        self.ownspots_page = None
        self.logs_page = None
        self._needs_restart = False
        # Re-start with a fresh browser
        try:
            await self.start()
            print("[Browser] Restart successful — logs page ready")
        except Exception as e:
            print(f"[Browser] Restart failed: {e}")
            self._needs_restart = True
        finally:
            self._restarting = False

    async def restart_if_needed(self):
        """Check if browser needs restart and do it. Called by worker before monitoring."""
        if self._needs_restart and not self._restarting:
            await self.restart()

    async def _do_login(self, page, username, password, login_url):
        """Auto-login to SX: navigate to login page, click 'Login with SX', fill credentials."""
        await page.goto(login_url, wait_until="domcontentloaded")
        await asyncio.sleep(2)
        print("[Browser] Navigated to SX login page (headless)")

        # Click 'Login with SX' button (not Discord)
        try:
            sx_login_btn = await page.wait_for_selector(
                "button:has-text('SX'), a:has-text('SX'), "
                "button:has-text('sx'), a:has-text('sx')",
                timeout=10000
            )
            if sx_login_btn:
                await sx_login_btn.click()
                print("[Browser] Clicked 'Login with SX'")
                await asyncio.sleep(2)
        except Exception as e:
            print(f"[Browser] Could not find 'Login with SX' button: {e}")
            # Try looking for any login button that's not Discord
            try:
                buttons = await page.query_selector_all("button, a")
                for btn in buttons:
                    text = await btn.inner_text()
                    if "login" in text.lower() and "discord" not in text.lower():
                        await btn.click()
                        print(f"[Browser] Clicked login button: {text}")
                        await asyncio.sleep(2)
                        break
            except Exception:
                pass

        # Fill username/email
        user_input = None
        for selector in [
            "input[type='email']",
            "input[type='text']",
            "input[name='username']",
            "input[name='email']",
            "input[placeholder*='email']",
            "input[placeholder*='user']",
            "input[placeholder*='Email']",
            "input[placeholder*='User']",
        ]:
            try:
                user_input = await page.wait_for_selector(selector, timeout=3000)
                if user_input:
                    break
            except Exception:
                continue

        if user_input:
            await user_input.click()
            await user_input.fill(username)
            print("[Browser] Filled username")
        else:
            print("[Browser] No username field — maybe already logged in")
            return

        # Fill password
        pass_input = None
        try:
            pass_input = await page.wait_for_selector("input[type='password']", timeout=5000)
            if pass_input:
                await pass_input.click()
                await pass_input.fill(password)
                print("[Browser] Filled password")
        except Exception:
            print("[Browser] No password field — maybe already logged in")
            return

        # Submit login: press Enter on password field immediately,
        # then also try clicking a submit button
        if pass_input:
            await pass_input.press("Enter")
            print("[Browser] Pressed Enter on password field")
            await asyncio.sleep(1)

            # Also try clicking a submit button if Enter didn't work
            for selector in [
                "button[type='submit']",
                "button:has-text('Login')",
                "button:has-text('Log In')",
                "button:has-text('Sign In')",
                "button:has-text('login')",
                "input[type='submit']",
                "button.btn-primary",
            ]:
                try:
                    btn = await page.wait_for_selector(selector, timeout=1000)
                    if btn:
                        await btn.click()
                        print(f"[Browser] Clicked login button: {selector}")
                        break
                except Exception:
                    continue

            await asyncio.sleep(3)
            print("[Browser] Login submitted — session saved")

    async def open_login_window(self):
        """Open a visible browser window for manual login (when session expires).
        Closes after detecting successful login, then reopens headless.
        """
        import os
        profile_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            self.config.get("browser_profile_dir", "browser_profile"),
        )
        login_url = self.config.get("sx_login_url", "https://dashboard.sx-pokego.xyz/#")

        # Close current headless context
        if self.browser_context:
            await self.browser_context.close()

        # Open visible browser
        self.browser_context = await self.playwright.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            headless=False,
            viewport={"width": 1280, "height": 800},
        )
        if self.browser_context.pages:
            self.ownspots_page = self.browser_context.pages[0]
        else:
            self.ownspots_page = await self.browser_context.new_page()

        await self.ownspots_page.goto(login_url, wait_until="domcontentloaded")
        print("[Browser] Visible login window opened — please log in")

        # Wait for login (up to 2 minutes)
        for _ in range(120):
            await asyncio.sleep(2)
            current_url = self.ownspots_page.url
            if "dashboard" in current_url and "login" not in current_url.lower():
                print("[Browser] Login detected — switching back to headless")
                break
        else:
            print("[Browser] Login timeout — continuing anyway")

        # Close visible and reopen headless
        await self.browser_context.close()
        self.browser_context = await self.playwright.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            headless=True,
            viewport={"width": 1280, "height": 800},
        )
        if self.browser_context.pages:
            self.ownspots_page = self.browser_context.pages[0]
        else:
            self.ownspots_page = await self.browser_context.new_page()

        # Reopen dashboard and logs pages
        await self.ownspots_page.goto(
            self.config.get("sx_dashboard_url"), wait_until="domcontentloaded"
        )
        self.logs_page = await self.browser_context.new_page()
        await self.logs_page.goto(
            self.config.get("sx_logs_url"), wait_until="domcontentloaded"
        )
        print("[Browser] Back to headless mode — dashboard and logs reopened")

    async def ensure_coord_auth(self):
        """Check if coordinate page needs Discord login. If so, automate it using
        the Discord account token. Auto-authorizes the OAuth app.

        Called at startup to handle the first-time Discord OAuth for coord pages.
        After login, the session is saved in the persistent profile.
        """
        import os
        profile_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            self.config.get("browser_profile_dir", "browser_profile"),
        )

        token = self.config.get("discord_token", "")

        # Try opening coordinate page in headless mode
        test_page = await self.browser_context.new_page()
        needs_login = False
        try:
            await test_page.goto("https://coord.pokedex100.com", wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(2)
            url = test_page.url
            if "discord.com" in url or "login" in url.lower() or "authorize" in url.lower():
                needs_login = True
                print("[Browser] Coordinate page requires Discord login")
            else:
                print("[Browser] Coordinate page auth OK — no login needed")
        except Exception as e:
            print(f"[Browser] Error checking coord auth: {e}")
            needs_login = True  # Assume login needed on error
        finally:
            await test_page.close()

        if not needs_login:
            return

        email = self.config.get("discord_email", "")
        password = self.config.get("discord_password", "")

        if not email or not password:
            # Fall back to manual login if no credentials
            print("[Browser] No Discord email/password set — falling back to manual login")
            await self._manual_coord_auth(profile_dir)
            return

        # Automated Discord login using email/password
        print("[Browser] Automating Discord login using email/password")
        auth_page = await self.browser_context.new_page()
        try:
            await auth_page.goto("https://coord.pokedex100.com", wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(2)
            current_url = auth_page.url
            print(f"[Browser] Startup auth — URL: {current_url}")

            # If already authenticated (no redirect to login), session is valid
            # Use urlparse to check hostname properly (avoids matching redirect_uri in query params)
            from urllib.parse import urlparse
            parsed = urlparse(current_url)
            if parsed.hostname == "coord.pokedex100.com" and "/accounts/" not in parsed.path:
                print("[Browser] Coordinate page already authenticated")
            else:
                # Need full pokedex100 login flow: Discord button → Continue → OAuth → Authorize
                print("[Browser] Login required — handling pokedex100 Discord login flow")
                success = await self._handle_pokedex_login(auth_page)
                if success:
                    print("[Browser] Coordinate page auth successful (automated)")
                else:
                    print(f"[Browser] Automated login failed — URL: {auth_page.url}")
                    await auth_page.close()
                    await self._manual_coord_auth(profile_dir)
                    return

        except Exception as e:
            print(f"[Browser] Error during automated Discord login: {e}")
            try:
                await auth_page.close()
            except Exception:
                pass
            await self._manual_coord_auth(profile_dir)
            return
        finally:
            try:
                if auth_page and not auth_page.is_closed():
                    await auth_page.close()
            except Exception:
                pass

    async def _manual_coord_auth(self, profile_dir):
        """Fall back to manual Discord login on coordinate page."""
        print("[Browser] Opening visible window for Discord login on coordinate page")
        if self.browser_context:
            await self.browser_context.close()

        self.browser_context = await self.playwright.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            headless=False,
            viewport={"width": 1280, "height": 800},
        )
        if self.browser_context.pages:
            page = self.browser_context.pages[0]
        else:
            page = await self.browser_context.new_page()
        self.ownspots_page = page

        await page.goto("https://coord.pokedex100.com", wait_until="domcontentloaded")
        print("[Browser] Coordinate page opened — please complete Discord login")

        # Wait for login (up to 3 minutes)
        for _ in range(180):
            await asyncio.sleep(2)
            url = page.url
            if "coord.pokedex100.com" in url and "discord.com" not in url and "login" not in url.lower() and "authorize" not in url.lower():
                print("[Browser] Discord login successful on coordinate page")
                break
        else:
            print("[Browser] Coordinate login timeout — continuing anyway")

        # Close headed, reopen headless
        await self.browser_context.close()
        self.browser_context = await self.playwright.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            headless=True,
            viewport={"width": 1280, "height": 800},
        )
        if self.browser_context.pages:
            self.ownspots_page = self.browser_context.pages[0]
        else:
            self.ownspots_page = await self.browser_context.new_page()
        await self.ownspots_page.goto(
            self.config.get("sx_dashboard_url"), wait_until="domcontentloaded"
        )
        self.logs_page = await self.browser_context.new_page()
        await self.logs_page.goto(
            self.config.get("sx_logs_url"), wait_until="domcontentloaded"
        )
        print("[Browser] Back to headless — dashboard and logs reopened")

    async def get_coordinates(self, url):
        """Open a coordinate page and extract 'lat,lng'. Returns None on failure.
        Handles pokedex100 login page and Discord OAuth automatically."""
        print(f"[Browser] Opening coordinate page: {url}")
        page = await self.browser_context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            print(f"[Browser] Coordinate page loaded — URL: {page.url}")

            # Check if redirected to pokedex100 login page
            if "/accounts/login/" in page.url or "Sign In" in (await page.inner_text("body"))[:200]:
                print("[Browser] Pokedex100 login page detected — attempting Discord login")
                logged_in = await self._handle_pokedex_login(page)
                if not logged_in:
                    print("[Browser] Failed to log in to pokedex100")
                    return None
                # After login, page should be on the coordinate page
                print(f"[Browser] After login — URL: {page.url}")

            # Check for donor role restriction
            try:
                body_text = await page.inner_text("body")
                if "requires a Bronze role" in body_text or "Not a donor" in body_text:
                    print(f"[Browser] Coordinate page requires donor role — trying alternate link")
                    return "DONOR_REQUIRED"
            except Exception:
                pass

            # Method 1: #community-coord input (pokedex100.com /6/ URLs)
            # Skip for /7/ URLs — they don't have #community-coord
            is_v7_url = '/7/' in url
            if not is_v7_url:
                try:
                    coord_input = await page.wait_for_selector("#community-coord", timeout=3000)
                    value = await coord_input.get_attribute("value")
                    if value:
                        coords = value.strip()
                        print(f"[Browser] Found coordinates (method 1): {coords}")
                        return coords
                    print("[Browser] #community-coord found but empty value")
                except Exception as e:
                    print(f"[Browser] Method 1 failed (#community-coord): {e}")
            else:
                # /7/ URLs: try page text regex first (fast, always works)
                try:
                    text = await page.inner_text("body")
                    match = re.search(r'(-?\d+\.\d+),\s*(-?\d+\.\d+)', text)
                    if match:
                        coords = f"{match.group(1)},{match.group(2)}"
                        if re.match(r'^-?\d+\.\d+,-?\d+\.\d+$', coords):
                            print(f"[Browser] Found coordinates (/7/ page text): {coords}")
                            return coords
                except Exception as e:
                    print(f"[Browser] /7/ page text failed: {e}")
                print("[Browser] No coordinates found in /7/ page text")

            # Method 2: any text input with coordinate-like value
            try:
                inputs = await page.query_selector_all("input[type='text']")
                print(f"[Browser] Found {len(inputs)} text inputs on page")
                for i, inp in enumerate(inputs):
                    value = await inp.get_attribute("value")
                    if value and re.match(r'^-?\d+\.\d+,\s*-?\d+\.\d+$', value.strip()):
                        print(f"[Browser] Found coordinates (method 2, input {i}): {value.strip()}")
                        return value.strip()
                print("[Browser] No coordinate-like values in text inputs")
            except Exception as e:
                print(f"[Browser] Method 2 failed (text inputs): {e}")

            # Method 3: regex on page body text
            try:
                text = await page.inner_text("body")
                match = re.search(r'(-?\d+\.\d+),\s*(-?\d+\.\d+)', text)
                if match:
                    coords = f"{match.group(1)},{match.group(2)}"
                    if re.match(r'^-?\d+\.\d+,-?\d+\.\d+$', coords):
                        print(f"[Browser] Found coordinates (method 3, page text): {coords}")
                        return coords
                print("[Browser] No coordinate pattern found in page body text")
            except Exception as e:
                print(f"[Browser] Method 3 failed (page text): {e}")

            # Dump page title and first 500 chars of text for debugging
            try:
                title = await page.title()
                text = await page.inner_text("body")
                print(f"[Browser] Page title: {title}")
                print(f"[Browser] Page content (first 500 chars): {text[:500]}")
            except Exception:
                pass
            print("[Browser] All coordinate extraction methods failed")
            return None
        finally:
            await page.close()

    async def _handle_pokedex_login(self, page):
        """Handle the full pokedex100 → Discord OAuth login flow using email/password.
        Flow: pokedex100 login → Discord btn → Continue → Discord login → email/password → Authorize → coord page.
        Returns True if login succeeded, False otherwise."""
        from urllib.parse import urlparse
        email = self.config.get("discord_email", "")
        password = self.config.get("discord_password", "")
        try:
            # Step 1: Click "Discord" button on pokedex100 login page
            print("[Browser] Step 1: Looking for Discord button on pokedex100 login page")
            discord_btn = None
            for selector in [
                "a:has-text('Discord')",
                "button:has-text('Discord')",
                "a[href*='discord']",
                "a[href*='oauth']",
            ]:
                try:
                    discord_btn = await page.wait_for_selector(selector, timeout=5000)
                    if discord_btn:
                        print(f"[Browser] Found Discord button: {selector}")
                        break
                except Exception:
                    continue

            if not discord_btn:
                print("[Browser] No Discord button found on pokedex100 login page")
                return False

            await discord_btn.click()
            print("[Browser] Clicked Discord button")
            await asyncio.sleep(3)
            print(f"[Browser] After Discord click — URL: {page.url}")

            # Step 2: Click "Continue" on the "Sign In Via Discord" confirmation page
            current_url = page.url
            if "/accounts/discord/login/" in current_url or "Sign In Via Discord" in (await page.inner_text("body"))[:300]:
                print("[Browser] Step 2: On Discord confirmation page — looking for Continue button")
                continue_btn = None
                for selector in [
                    "button:has-text('Continue')",
                    "a:has-text('Continue')",
                    "input[type='submit']",
                    "button[type='submit']",
                ]:
                    try:
                        continue_btn = await page.wait_for_selector(selector, timeout=5000)
                        if continue_btn:
                            print(f"[Browser] Found Continue button: {selector}")
                            break
                    except Exception:
                        continue

                if continue_btn:
                    await continue_btn.click()
                    print("[Browser] Clicked Continue button")
                    await asyncio.sleep(3)
                    print(f"[Browser] After Continue — URL: {page.url}")
                else:
                    print("[Browser] No Continue button found")

            # Step 3-6: Content-based Discord login flow
            # Known working flow (from login_flow.log):
            #   1. Fill email + password, click Log In
            #   2. JS scroll permissions container to bottom (reveals Authorize)
            #   3. Click Authorize
            #   4. Redirect back to coord page
            print("[Browser] Step 3: Discord login flow")
            self._login_log("=== Discord login flow started ===")

            login_filled = False
            authorize_clicked = False

            for poll in range(45):  # Up to 90 seconds
                await asyncio.sleep(2)

                # Check 1: Back on coord page = success
                current_url = page.url
                parsed = urlparse(current_url)
                if parsed.hostname == "coord.pokedex100.com" and "/accounts/" not in parsed.path:
                    self._login_log(f"SUCCESS: Back on coord page (poll {poll})")
                    self._login_log("=== Login flow complete ===")
                    print("[Browser] Login successful")
                    return True

                # Check 2: "Continue to Discord" modal
                try:
                    continue_btn = await page.query_selector("button:has-text('Continue to Discord')")
                    if continue_btn:
                        self._login_log(f"Poll {poll}: Clicked 'Continue to Discord'")
                        print("[Browser] Clicked 'Continue to Discord'")
                        await continue_btn.click()
                        await asyncio.sleep(3)
                        continue
                except Exception:
                    pass

                # Check 3: Email input (login form)
                try:
                    email_input = await page.query_selector('input[name="email"], input[type="email"], input[placeholder*="mail"], input[placeholder*="Email"]')
                    if email_input and not login_filled:
                        self._login_log(f"Poll {poll}: Filling email and password")
                        print("[Browser] Filling Discord credentials")
                        await email_input.fill(email)
                        await asyncio.sleep(0.5)
                        password_input = await page.query_selector('input[name="password"], input[type="password"], input[placeholder*="assword"]')
                        if password_input:
                            await password_input.fill(password)
                            await asyncio.sleep(0.5)
                            login_btn = await page.query_selector('button[type="submit"], button:has-text("Log In")')
                            if login_btn:
                                await login_btn.click()
                            else:
                                await password_input.press("Enter")
                            login_filled = True
                            self._login_log(f"Poll {poll}: Clicked Log In")
                            print("[Browser] Clicked Log In")
                            await asyncio.sleep(5)
                            continue
                except Exception:
                    pass

                # Check 4: "Keep Scrolling..." — JS scroll container to bottom
                try:
                    scroll_btn = await page.query_selector("button:has-text('Keep Scrolling')")
                    if scroll_btn:
                        self._login_log(f"Poll {poll}: JS scroll to bottom")
                        print("[Browser] Scrolling permissions to bottom")
                        await page.evaluate('''() => {
                            const all = document.querySelectorAll('*');
                            for (const el of all) {
                                if (el.scrollHeight > el.clientHeight && el.clientHeight > 50) {
                                    el.scrollTop = el.scrollHeight;
                                }
                            }
                        }''')
                        await asyncio.sleep(1)
                        continue
                except Exception:
                    pass

                # Check 5: Authorize button
                try:
                    authorize_btn = await page.query_selector('button:has-text("Authorize")')
                    if authorize_btn and not authorize_clicked:
                        await authorize_btn.scroll_into_view_if_needed()
                        await asyncio.sleep(0.5)
                        self._login_log(f"Poll {poll}: Clicked Authorize")
                        print("[Browser] Clicked Authorize")
                        await authorize_btn.click()
                        authorize_clicked = True
                        await asyncio.sleep(3)
                        continue
                except Exception:
                    pass

            self._login_log(f"FAILED: Login did not complete (login_filled={login_filled}, authorize_clicked={authorize_clicked})")
            self._login_log(f"  Final URL: {page.url}")
            print("[Browser] Login did not complete — check login_flow.log")
            return False

        except Exception as e:
            self._login_log(f"EXCEPTION: {e}")
            print(f"[Browser] Error during pokedex100 login: {e}")
            return False

    def _login_log(self, message):
        """Log login flow steps to a file for debugging."""
        import os, time
        log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "login_flow.log")
        try:
            with open(log_path, "a") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
        except Exception:
            pass

    async def teleport(self, coordinates):
        """Enter coordinates into SX dashboard and click Teleport."""
        if not self.ownspots_page:
            self._needs_restart = True
            print("[Browser] No ownspots page — browser may have crashed")
            return
        page = self.ownspots_page
        try:
            await page.bring_to_front()
        except Exception as e:
            if "closed" in str(e).lower() or "target page" in str(e).lower():
                print(f"[Browser] Browser crash detected in teleport: {e}")
                self._needs_restart = True
                return
            raise

        # Navigate to dashboard if needed
        if "sx-pokego.xyz" not in page.url:
            await page.goto(
                self.config.get("sx_dashboard_url"), wait_until="domcontentloaded"
            )

        # Fill coordinate input
        coord_input = await page.wait_for_selector(
            "input.sx-search__input.mono", timeout=15000
        )
        await coord_input.click()
        await coord_input.fill(coordinates)

        # Submit (Enter or button)
        await coord_input.press("Enter")
        await asyncio.sleep(1)
        try:
            submit_btn = await page.query_selector(".sx-ownspots__insert > button.btn-primary")
            if submit_btn:
                await submit_btn.click()
                await asyncio.sleep(1)
        except Exception:
            pass

        await asyncio.sleep(2)

        # Click Teleport
        print("[Browser] Clicking Teleport...")
        clicked = False
        try:
            btn = await page.wait_for_selector(
                ".sx-coord-preview__actions button.btn-primary", timeout=10000
            )
            await btn.click()
            clicked = True
        except Exception:
            pass
        if not clicked:
            try:
                btn = await page.query_selector("button:has-text('Teleport')")
                if btn:
                    await btn.click()
                    clicked = True
            except Exception:
                pass
        if clicked:
            print("[Browser] Teleport clicked!")
            # Wait for teleport to process and verify
            await asyncio.sleep(3)

            # Check for error/toast messages on the page
            try:
                body_text = await page.inner_text("body")
                # Look for common SX error indicators
                for error_phrase in ["error", "failed", "denied", "unauthorized", "no account",
                                     "not linked", "insufficient", "limit reached", "cooldown"]:
                    if error_phrase in body_text.lower():
                        # Find the context around the error
                        idx = body_text.lower().find(error_phrase)
                        context = body_text[max(0, idx-50):idx+100].strip()
                        print(f"[Browser] SX error detected: ...{context}...")
                        break
                else:
                    # No error phrases found — check for success indicators
                    # Look for any toast/notification elements
                    toasts = await page.query_selector_all(".toast, .notification, .alert, [role='alert']")
                    for toast in toasts:
                        toast_text = await toast.inner_text()
                        if toast_text.strip():
                            print(f"[Browser] SX notification: {toast_text.strip()[:200]}")
            except Exception as e:
                print(f"[Browser] Could not read page after teleport: {e}")

            # Take a screenshot for debugging
            try:
                import os
                screenshot_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "teleport_debug.png")
                await page.screenshot(path=screenshot_path)
                print(f"[Browser] Screenshot saved to {screenshot_path}")
            except Exception:
                pass
        else:
            print("[Browser] WARNING: Could not find Teleport button")
            # Dump page content for debugging
            try:
                body_text = await page.inner_text("body")
                print(f"[Browser] Page content (first 500 chars): {body_text[:500]}")
            except Exception:
                pass

    async def walk(self, coordinates):
        """Enter coordinates and click Walk button."""
        page = self.ownspots_page
        await page.bring_to_front()

        # Calculate offset coordinate for walking
        walk_dist = self.config.get("walk_distance_meters", 10)
        walk_coords = offset_coordinate(coordinates, walk_dist)
        print(f"[Browser] Walking to offset: {walk_coords} ({walk_dist}m)")

        # Fill coordinate input
        coord_input = await page.wait_for_selector(
            "input.sx-search__input.mono", timeout=15000
        )
        await coord_input.click()
        await coord_input.fill(walk_coords)
        await coord_input.press("Enter")
        await asyncio.sleep(0.5)
        try:
            submit_btn = await page.query_selector(".sx-ownspots__insert > button.btn-primary")
            if submit_btn:
                await submit_btn.click()
                await asyncio.sleep(0.5)
        except Exception:
            pass
        await asyncio.sleep(1)

        # Click Walk button (first button in actions, text "Walk")
        print("[Browser] Clicking Walk...")
        clicked = False
        try:
            # Walk is the first (non-primary) button in the actions row
            btns = await page.query_selector_all(".sx-coord-preview__actions button")
            for btn in btns:
                text = await btn.inner_text()
                if "walk" in text.lower():
                    await btn.click()
                    clicked = True
                    break
        except Exception:
            pass
        if not clicked:
            try:
                btn = await page.query_selector("button:has-text('Walk')")
                if btn:
                    await btn.click()
                    clicked = True
            except Exception:
                pass
        if clicked:
            print("[Browser] Walk clicked!")
        else:
            print("[Browser] WARNING: Could not find Walk button")

    async def read_logs(self):
        """Read the latest log text from the SX logs page.

        Returns the full text content of the logs area.
        Detects browser crashes and flags for restart.
        """
        if not self.logs_page:
            self._needs_restart = True
            return ""
        try:
            await self.logs_page.bring_to_front()
            text = await self.logs_page.inner_text("body", timeout=5000)
            return text
        except Exception as e:
            err_str = str(e).lower()
            if "closed" in err_str or "target page" in err_str or "browser has been closed" in err_str:
                print(f"[Browser] Browser crash detected in read_logs: {e}")
                self._needs_restart = True
            else:
                print(f"[Browser] Error reading logs: {e}")
            return ""

    async def snapshot_logs(self):
        """Take a snapshot of current log lines for baseline comparison."""
        text = await self.read_logs()
        return set(text.splitlines())

    async def check_logs_for(self, pokemon_name, timeout=10, catch_patterns=None, fled_patterns=None, shiny_patterns=None, baseline_lines=None, on_progress=None, skip_hundo_non_shiny=False, processed_encounter_ids=None, teleport_started_at=0, cluster_skip_threshold=5):
        """Monitor the logs page for a specific Pokemon.

        Uses EncounterId-based deduplication to avoid counting the same spawn twice
        and to prevent reading stale logs from previous teleports.

        Skip logic: Only skip (return early) when the encounter is 100% IV AND NOT shiny.
        - Shiny encounters: never skip (might be shundo), always count shiny.
        - Non-100% IV encounters: never skip (another spawn might be better).
        - 100% IV non-shiny: skip immediately (it's a hundo, not a shundo).

        on_progress(elapsed, timeout) is called each second for real-time timer updates.

        Returns a dict with: found, caught, fled, shiny, cp, iv, iv_percent,
        skipped_hundo_non_shiny, encounter_id, new_encounters (list of dicts)
        """
        catch_patterns = catch_patterns or ["[CatchPokemon] Caught"]
        fled_patterns = fled_patterns or ["fled"]
        shiny_patterns = shiny_patterns or ["shiny", "Shiny"]
        baseline_lines = baseline_lines or set()

        result = {
            "found": False,
            "caught": False,
            "fled": False,
            "shiny": False,
            "cp": None,
            "iv": None,
            "iv_percent": 0,
            "skipped_hundo_non_shiny": False,
            "encounter_id": None,
            "new_encounters": [],
            "non_target_catches": [],
            "cluster_skip": False,
            "manual_skip": False,
        }

        poke_lower = pokemon_name.lower()
        elapsed = 0
        interval = 1
        non_target_encounter_ids = set()
        while elapsed < timeout:
            text = await self.read_logs()
            if text:
                # Only look at lines that are NEW since the baseline
                current_lines = set(text.splitlines())
                new_lines = current_lines - baseline_lines

                # First pass: scan ALL new encounter lines for target, shinies, hundos
                # This ensures we don't miss the target if it appears as the 4th+ encounter
                # and we log all shinies/hundos even from non-target encounters
                target_found_in_cluster = False
                for line in new_lines:
                    line_lower = line.lower()
                    # Check if this is an encounter line
                    if "[normalencounter]" not in line_lower and "normal-encounter" not in line_lower:
                        continue

                    # Parse EncounterId
                    enc_id_match = re.search(r'EncounterId:\s*(\d+)', line)
                    enc_id = enc_id_match.group(1) if enc_id_match else None

                    # Check if it's the target
                    is_target = poke_lower in line_lower

                    if is_target:
                        target_found_in_cluster = True
                        # Parse the target's IV and shiny status right here
                        target_enc_match = re.search(
                            rf'{re.escape(pokemon_name)}.*?(\d+)cp.*?(\d+/\d+/\d+)\s*IV\s*\((\d+)%\)',
                            line, re.IGNORECASE
                        )
                        target_shiny = any(p.lower() in line_lower for p in shiny_patterns)
                        if target_enc_match:
                            target_iv_pct = int(target_enc_match.group(3))
                            result["cp"] = target_enc_match.group(1)
                            result["iv"] = f"{target_enc_match.group(2)} IV ({target_enc_match.group(3)}%)"
                            result["iv_percent"] = target_iv_pct
                            result["found"] = True
                            if enc_id:
                                result["encounter_id"] = enc_id
                            result["shiny"] = target_shiny or result.get("shiny", False)
                            # Mark this encounter as processed so the second pass doesn't re-add it
                            if enc_id and processed_encounter_ids is not None:
                                processed_encounter_ids.add(enc_id)
                            # Record the target encounter
                            result["new_encounters"].append({
                                "pokemon": pokemon_name,
                                "cp": result["cp"],
                                "iv": result["iv"],
                                "iv_percent": target_iv_pct,
                                "shiny": target_shiny,
                                "encounter_id": enc_id,
                            })
                            # If hundo but not shiny — mark for skip, but DON'T return yet.
                            # We need to finish scanning all lines in this batch so we don't
                            # miss any shinies or other encounters that appeared at the same time.
                            if skip_hundo_non_shiny and target_iv_pct == 100 and not target_shiny:
                                if not result.get("skipped_hundo_non_shiny"):
                                    result["skipped_hundo_non_shiny"] = True
                                    print(f"[Browser] {pokemon_name} is 100% IV but NOT shiny — will skip after batch scan")
                        # If we couldn't parse IV, fall through to second pass
                        continue

                    # Non-target encounter — parse for shiny/hundo logging
                    if enc_id:
                        if enc_id in non_target_encounter_ids:
                            continue
                        non_target_encounter_ids.add(enc_id)

                    # Parse CP, IV, shiny from this non-target encounter
                    enc_match = re.search(
                        r'(\w[\w\s-]*?)\s*\(#\d+\)\s.*?(\d+)cp.*?(\d+/\d+/\d+)\s*IV\s*\((\d+)%\)',
                        line, re.IGNORECASE
                    )
                    nt_shiny = any(p.lower() in line_lower for p in shiny_patterns)
                    if enc_match or nt_shiny:
                        nt_pokemon = enc_match.group(1).strip() if enc_match else "unknown"
                        nt_cp = enc_match.group(2) if enc_match else None
                        nt_iv = f"{enc_match.group(3)} IV ({enc_match.group(4)}%)" if enc_match else None
                        nt_iv_pct = int(enc_match.group(4)) if enc_match else 0
                        # Only log if shiny or hundo (to avoid cluttering stats)
                        if nt_shiny or nt_iv_pct == 100:
                            result["new_encounters"].append({
                                "pokemon": nt_pokemon,
                                "cp": nt_cp,
                                "iv": nt_iv,
                                "iv_percent": nt_iv_pct,
                                "shiny": nt_shiny,
                                "encounter_id": enc_id,
                            })

                # Cluster detection: log how many non-targets appeared,
                # but do NOT skip — the target may still spawn after the cluster.
                # Continue monitoring until timeout expires.
                if len(non_target_encounter_ids) >= cluster_skip_threshold and not target_found_in_cluster and not result["found"]:
                    if len(non_target_encounter_ids) == cluster_skip_threshold:
                        print(f"[Browser] Cluster of {len(non_target_encounter_ids)} non-target encounters detected — continuing to monitor for target")

                # Scan for non-target CATCHES (e.g., wild hundo/shundo that was caught)
                # Catch line format: [CatchPokemon] Caught Normal-Encounter Pokemon Panpour (#515) with Ultra Ball (CatchRule: Pinap)
                for line in new_lines:
                    line_lower = line.lower()
                    if "[catchpokemon]" not in line_lower and "caught" not in line_lower:
                        continue
                    # Skip if this is the target Pokemon (handled in second pass)
                    if poke_lower in line_lower:
                        continue
                    # Parse the caught Pokemon name
                    catch_match = re.search(
                        r'Caught.*?Pokemon\s+(.+?)\s+\(#(\d+)\)',
                        line, re.IGNORECASE
                    )
                    if not catch_match:
                        continue
                    nt_catch_name = catch_match.group(1).strip()
                    nt_catch_num = catch_match.group(2)
                    # Try to find the encounter line for this Pokemon to get CP/IV
                    nt_cp = None
                    nt_iv = None
                    nt_iv_pct = 0
                    nt_shiny = False
                    nt_enc_id = None
                    for enc_line in new_lines:
                        enc_lower = enc_line.lower()
                        if nt_catch_name.lower() not in enc_lower:
                            continue
                        if "[normalencounter]" not in enc_lower and "normal-encounter" not in enc_lower:
                            continue
                        # Parse CP, IV from encounter line
                        enc_match = re.search(
                            rf'{re.escape(nt_catch_name)}.*?(\d+)cp.*?(\d+/\d+/\d+)\s*IV\s*\((\d+)%\)',
                            enc_line, re.IGNORECASE
                        )
                        if enc_match:
                            nt_cp = enc_match.group(1)
                            nt_iv = f"{enc_match.group(2)} IV ({enc_match.group(3)}%)"
                            nt_iv_pct = int(enc_match.group(3))
                        nt_shiny = any(p.lower() in enc_lower for p in shiny_patterns)
                        enc_id_match = re.search(r'EncounterId:\s*(\d+)', enc_line)
                        if enc_id_match:
                            nt_enc_id = enc_id_match.group(1)
                        break
                    result["non_target_catches"].append({
                        "pokemon": nt_catch_name,
                        "cp": nt_cp,
                        "iv": nt_iv,
                        "iv_percent": nt_iv_pct,
                        "shiny": nt_shiny,
                        "encounter_id": nt_enc_id,
                    })
                    print(f"[Browser] Non-target catch detected: {nt_catch_name} (shiny={nt_shiny}, IV={nt_iv_pct}%)")

                # Second pass: process target encounters (catch/fled/shiny/skip logic)
                for line in new_lines:
                    line_lower = line.lower()
                    if poke_lower not in line_lower:
                        continue

                    # Parse EncounterId for deduplication
                    enc_id_match = re.search(r'EncounterId:\s*(\d+)', line)
                    enc_id = enc_id_match.group(1) if enc_id_match else None
                    
                    # Skip if we've already processed this encounter
                    if enc_id and processed_encounter_ids is not None:
                        if enc_id in processed_encounter_ids:
                            continue
                        processed_encounter_ids.add(enc_id)
                    elif enc_id and enc_id == result.get("encounter_id"):
                        continue

                    # Pokemon name found in this line
                    result["found"] = True
                    if enc_id:
                        result["encounter_id"] = enc_id

                    # Try to extract CP, IV, and IV percentage
                    # Matches: "Pokemon Fennekin (#653) — 499cp, 15/15/15 IV (100%)"
                    encounter_match = re.search(
                        rf'{re.escape(pokemon_name)}.*?(\d+)cp.*?(\d+/\d+/\d+)\s*IV\s*\((\d+)%\)',
                        line, re.IGNORECASE
                    )
                    if encounter_match:
                        result["cp"] = encounter_match.group(1)
                        result["iv"] = f"{encounter_match.group(2)} IV ({encounter_match.group(3)}%)"
                        result["iv_percent"] = int(encounter_match.group(3))

                    # Check for catch indicators (same line as Pokemon name)
                    for pattern in catch_patterns:
                        if pattern.lower() in line_lower:
                            result["caught"] = True
                            break

                    # Check for fled indicators (same line as Pokemon name)
                    if not result["caught"]:
                        for pattern in fled_patterns:
                            if pattern.lower() in line_lower:
                                result["fled"] = True
                                break

                    # Check for shiny — only set to True, never overwrite to False
                    is_shiny = False
                    for pattern in shiny_patterns:
                        if pattern.lower() in line_lower:
                            result["shiny"] = True
                            is_shiny = True
                            break

                    # Record this encounter
                    enc_record = {
                        "pokemon": pokemon_name,
                        "cp": result["cp"],
                        "iv": result["iv"],
                        "iv_percent": result["iv_percent"],
                        "shiny": is_shiny,
                        "encounter_id": enc_id,
                    }
                    result["new_encounters"].append(enc_record)

                    # Skip logic: ONLY skip when 100% IV AND NOT shiny
                    # - Shiny: never skip (might be shundo)
                    # - Non-100% IV: never skip (another spawn might be better)
                    # - 100% IV non-shiny: mark for skip, but continue scanning rest of batch
                    if skip_hundo_non_shiny and result["found"] and result["iv_percent"] == 100 and not is_shiny:
                        if not result.get("skipped_hundo_non_shiny"):
                            result["skipped_hundo_non_shiny"] = True
                            print(f"[Browser] {pokemon_name} is 100% IV but NOT shiny — will skip after batch scan")
                    # If we found catch or fled, return early
                    if result["caught"] or result["fled"]:
                        print(f"[Browser] Log result: {result}")
                        return result

                # If we marked a hundo non-shiny skip during this batch, return now
                # that all encounters (including shinies) have been recorded
                if result.get("skipped_hundo_non_shiny"):
                    print(f"[Browser] Returning with skip — {len(result['new_encounters'])} encounters recorded")
                    return result

                # Mark all processed lines as seen so they're not re-processed next iteration
                baseline_lines.update(new_lines)

            await asyncio.sleep(interval)
            elapsed += interval
            if on_progress:
                try:
                    should_skip = on_progress(elapsed, timeout)
                    if should_skip:
                        print(f"[Browser] Manual skip requested — stopping monitoring")
                        result["manual_skip"] = True
                        return result
                except Exception:
                    pass

        print(f"[Browser] Log monitoring timed out ({timeout}s). Result: {result}")
        return result

    async def close(self):
        """Clean up browser resources."""
        try:
            if self.browser_context:
                await self.browser_context.close()
        except Exception:
            pass
        try:
            if self.playwright:
                await self.playwright.stop()
        except Exception:
            pass
