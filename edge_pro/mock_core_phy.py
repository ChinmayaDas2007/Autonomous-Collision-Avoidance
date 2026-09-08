"""
Project Kessler - Mock core_phy (Basilisk Physics Engine Node)
-------------------------------------------------------------------------------
Simulates the Basilisk flight software / physics engine node listening
on a local TCP socket for incoming Slew Targeting Commands from edge_pro.

Usage:
  python3 -m edge_pro.mock_core_phy [--host 127.0.0.1] [--port 5556]
"""

import argparse
import asyncio
import json
import sys


class MockCorePhyServer:
    """Mock receiver representing Basilisk core_phy attitude guidance."""

    def __init__(self, host: str = "127.0.0.1", port: int = 5556):
        self.host = host
        self.port = port
        self._server = None
        self.received_commands = []

    async def start(self):
        self._server = await asyncio.start_server(
            self.handle_client, self.host, self.port, reuse_address=True
        )
        print(f"[Mock core_phy] Basilisk Guidance Node listening on {self.host}:{self.port}...", flush=True)

    async def stop(self):
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            print("[Mock core_phy] Guidance Node stopped.", flush=True)

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        peer = writer.get_extra_info("peername")
        print(f"[Mock core_phy] Connection accepted from Edge Payload at {peer}", flush=True)

        buffer = bytearray()
        try:
            while True:
                chunk = await reader.read(4096)
                if not chunk:
                    print(f"[Mock core_phy] Edge Payload {peer} disconnected.")
                    break
                buffer.extend(chunk)

                while b"\n" in buffer:
                    line, _, remaining = buffer.partition(b"\n")
                    buffer = bytearray(remaining)

                    line_str = line.decode("utf-8").strip()
                    if not line_str:
                        continue

                    try:
                        packet = json.loads(line_str)
                        self.received_commands.append(packet)
                        print("\n=======================================================", flush=True)
                        print("[Mock core_phy] >>> RECEIVED SLEW TARGETING PACKET <<<", flush=True)
                        print("=======================================================", flush=True)
                        print(json.dumps(packet, indent=2), flush=True)
                        print("=======================================================\n", flush=True)

                        # Validate expected fields
                        cmd = packet.get("command")
                        target = packet.get("target_asset")
                        exec_time = packet.get("execute_at_utc")
                        mode = packet.get("sensor_mode")

                        print(f"[Mock core_phy] Parsed Command: {cmd}", flush=True)
                        print(f"[Mock core_phy] Targeting Asset: {target}", flush=True)
                        print(f"[Mock core_phy] Slew Execution Time: {exec_time}", flush=True)
                        print(f"[Mock core_phy] Sensor Configuration: {mode}", flush=True)
                        print("[Mock core_phy] ADCS MRP Guidance Law initialized.\n", flush=True)

                    except json.JSONDecodeError as err:
                        print(f"[Mock core_phy] Malformed JSON received: {err}", file=sys.stderr)

        except asyncio.CancelledError:
            pass
        finally:
            writer.close()
            await writer.wait_closed()


async def run_server(host: str, port: int):
    server = MockCorePhyServer(host, port)
    await server.start()
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, asyncio.CancelledError):
        await server.stop()


def main():
    parser = argparse.ArgumentParser(description="Mock core_phy for Project Kessler")
    parser.add_argument("--host", default="127.0.0.1", help="Listening host")
    parser.add_argument("--port", type=int, default=5556, help="Listening port")
    args = parser.parse_args()

    try:
        asyncio.run(run_server(args.host, args.port))
    except KeyboardInterrupt:
        print("\n[Mock core_phy] Shutting down.")


if __name__ == "__main__":
    main()
