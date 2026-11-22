"""A very small real-browser driver: headless Chrome over the DevTools protocol, with no extra
dependency (aiohttp is already installed for the MCP SDK). It exists so the console's behaviour is
tested in a browser, not by reading its source.

`find_chrome()` returns None when no Chrome/Chromium/Edge is installed; the browser tests then skip,
loudly, instead of passing on nothing.
"""

import asyncio
import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

import aiohttp

CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser",
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
)


def find_chrome():
    for name in ("google-chrome", "chromium", "chromium-browser", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    return next((c for c in CANDIDATES if os.path.exists(c)), None)


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Browser:
    """One headless Chrome; `page()` opens a tab (a second call is a second tab of the same browser)."""

    def __init__(self, chrome):
        self.profile = tempfile.mkdtemp(prefix="ccc-chrome-")
        # GitHub-hosted Linux runners restrict the user namespaces Chrome's sandbox needs and have a
        # small /dev/shm; the browser only opens this program's own local pages there.
        ci_flags = ["--no-sandbox", "--disable-dev-shm-usage"] if os.environ.get("CI") else []
        self.process = subprocess.Popen(
            [chrome, "--headless=new", "--remote-debugging-port=0",
             f"--user-data-dir={self.profile}", "--no-first-run", "--no-default-browser-check",
             "--disable-gpu", "--window-size=1280,900", "--disable-background-timer-throttling",
             "--disable-renderer-backgrounding", "--disable-backgrounding-occluded-windows",
             *ci_flags, "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            self.port = self._wait_for_port()
        except BaseException:
            self._stop_chrome()
            raise
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.session = self.run(self._session())

    def _wait_for_port(self):
        # Chrome binds its own port and publishes it after listening. Reserving a port
        # in Python and releasing it before Chrome starts races other xdist workers.
        port_file = Path(self.profile) / "DevToolsActivePort"
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            code = self.process.poll()
            if code is not None:
                raise RuntimeError(f"Chrome exited before opening DevTools (exit {code})")
            try:
                port, endpoint = port_file.read_text(encoding="ascii").splitlines()
                if endpoint.startswith("/devtools/browser/"):
                    return int(port)
            except (OSError, ValueError):
                pass
            time.sleep(0.15)
        raise RuntimeError("Chrome did not open DevTools within 45 seconds")

    async def _session(self):
        return aiohttp.ClientSession()

    def run(self, coroutine, timeout=60):
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop).result(timeout)

    def page(self, url):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/json/new?{url}", method="PUT")
        target = json.loads(urllib.request.urlopen(request, timeout=10).read())
        return Page(self, target)

    def close(self):
        try:
            self.run(self.session.close(), 5)
        except Exception:
            pass
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._stop_chrome()

    def _stop_chrome(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(5)
        shutil.rmtree(self.profile, ignore_errors=True)


class Page:
    def __init__(self, browser, target):
        self.browser, self.target = browser, target
        self.ws = browser.run(browser.session.ws_connect(target["webSocketDebuggerUrl"], max_msg_size=0))
        self._id = 0
        self.send("Runtime.enable")
        self.send("Page.enable")

    def send(self, method, **params):
        async def call():
            self._id += 1
            wanted = self._id
            await self.ws.send_json({"id": wanted, "method": method, "params": params})
            while True:
                message = await self.ws.receive_json()
                if message.get("id") == wanted:
                    if "error" in message:
                        raise RuntimeError(message["error"])
                    return message.get("result", {})
        return self.browser.run(call())

    def eval(self, expression):
        """Evaluate JavaScript (a promise is awaited) and return its JSON value."""
        result = self.send("Runtime.evaluate", expression=f"(async () => {{ {expression} }})()",
                           awaitPromise=True, returnByValue=True)
        if "exceptionDetails" in result:
            raise RuntimeError(result["exceptionDetails"].get("exception", {}).get("description"))
        return result["result"].get("value")

    def goto(self, url):
        self.send("Page.navigate", url=url)

    def wait_for(self, expression, timeout=15):
        """Poll a JavaScript boolean expression until it is true."""
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            try:
                last = self.eval(f"return Boolean({expression});")
            except RuntimeError:
                last = False
            if last:
                return True
            time.sleep(0.15)
        raise AssertionError(f"timed out waiting for: {expression}")

    def key(self, text):
        """Type real key events into whatever has focus."""
        for char in text:
            self.send("Input.dispatchKeyEvent", type="keyDown", text=char)
            self.send("Input.dispatchKeyEvent", type="keyUp", text=char)

    def close(self):
        try:
            self.browser.run(self.ws.close(), 5)
            urllib.request.urlopen(
                f"http://127.0.0.1:{self.browser.port}/json/close/{self.target['id']}", timeout=5).read()
        except Exception:
            pass
