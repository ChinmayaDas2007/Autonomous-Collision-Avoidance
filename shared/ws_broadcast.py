import asyncio
import json
import logging
import threading

try:
    import websockets
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    websockets = None
    WEBSOCKETS_AVAILABLE = False


class DashboardBroadcaster:
    def __init__(self, port: int):
        self.port = port
        self.clients = set()
        self.loop = None
        self._server = None

    async def _handler(self, websocket):
        self.clients.add(websocket)
        try:
            await websocket.wait_closed()
        finally:
            self.clients.discard(websocket)

    async def start(self):
        if not WEBSOCKETS_AVAILABLE:
            logging.warning(
                f"[dashboard-broadcast] 'websockets' library not installed. WebSocket broadcasting on port {self.port} disabled."
            )
            return
        try:
            self._server = await websockets.serve(self._handler, "0.0.0.0", self.port)
            logging.info(f"[dashboard-broadcast] listening on ws://0.0.0.0:{self.port}")
        except OSError as e:
            logging.warning(f"[dashboard-broadcast] Port {self.port} unavailable ({e}); WebSocket live broadcast disabled.")
        except Exception as e:
            logging.warning(f"[dashboard-broadcast] Failed to initialize WebSocket on port {self.port}: {e}")

    def run_in_background(self):
        """Starts the websocket server in a background thread so sync apps can use it."""
        if not WEBSOCKETS_AVAILABLE:
            return

        def _run():
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            try:
                self.loop.run_until_complete(self.start())
                self.loop.run_forever()
            except Exception as e:
                logging.debug(f"[dashboard-broadcast] Loop stopped on port {self.port}: {e}")

        t = threading.Thread(target=_run, daemon=True)
        t.start()

    def stop(self):
        """Closes the WebSocket server."""
        if self._server:
            try:
                self._server.close()
            except Exception:
                pass

    def broadcast(self, payload: dict):
        """Call this from anywhere in your node once you have fresh data.
        Safe to call even if no dashboard is connected yet — silently no-ops."""
        if not WEBSOCKETS_AVAILABLE or not self.clients or not self.loop:
            return
        msg = json.dumps(payload)
        dead = set()
        for ws in list(self.clients):
            try:
                # Thread-safe async task scheduling
                asyncio.run_coroutine_threadsafe(ws.send(msg), self.loop)
            except Exception:
                dead.add(ws)
        self.clients -= dead

