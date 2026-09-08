import asyncio
import json
import websockets
import logging
import threading

class DashboardBroadcaster:
    def __init__(self, port: int):
        self.port = port
        self.clients = set()
        self.loop = None

    async def _handler(self, websocket):
        self.clients.add(websocket)
        try:
            await websocket.wait_closed()
        finally:
            self.clients.discard(websocket)

    async def start(self):
        # We start the server in the background
        start_server = websockets.serve(self._handler, "0.0.0.0", self.port)
        await start_server
        logging.info(f"[dashboard-broadcast] listening on ws://0.0.0.0:{self.port}")

    def run_in_background(self):
        """Starts the websocket server in a background thread so sync apps can use it."""
        def _run():
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.loop.run_until_complete(self.start())
            self.loop.run_forever()
        t = threading.Thread(target=_run, daemon=True)
        t.start()

    def broadcast(self, payload: dict):
        """Call this from anywhere in your node once you have fresh data.
        Safe to call even if no dashboard is connected yet — silently no-ops."""
        if not self.clients or not self.loop:
            return
        msg = json.dumps(payload)
        dead = set()
        for ws in self.clients:
            try:
                # Thread-safe async task scheduling
                asyncio.run_coroutine_threadsafe(ws.send(msg), self.loop)
            except Exception:
                dead.add(ws)
        self.clients -= dead
