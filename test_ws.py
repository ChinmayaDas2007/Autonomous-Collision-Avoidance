import asyncio
import websockets
import json

async def connect_and_listen(uri, name):
    try:
        async with websockets.connect(uri) as ws:
            print(f"[TEST] Connected to {name} at {uri}")
            msg = await ws.recv()
            print(f"[TEST] Received from {name}:\n{json.dumps(json.loads(msg), indent=2)}")
    except Exception as e:
        print(f"[TEST] Failed to connect to {name} ({uri}): {e}")

async def main():
    await asyncio.gather(
        connect_and_listen("ws://127.0.0.1:8001", "core_phy (orbit)"),
        connect_and_listen("ws://127.0.0.1:8002", "edge_pro (vision)"),
        connect_and_listen("ws://127.0.0.1:8003", "gnd_ops (drag)")
    )

if __name__ == "__main__":
    asyncio.run(main())
