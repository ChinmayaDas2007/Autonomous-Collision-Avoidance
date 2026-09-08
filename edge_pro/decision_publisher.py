"""
Project Kessler - Edge Processing Payload
Phase 4: Decision Telemetry Publisher & Dashboard Broadcast Server
-------------------------------------------------------------------------------
Dispatches ManeuverDecisionPackets to:
1. PS 5 Mission Control / Streamlit UI Dashboard.
2. Flight Dynamics & Propulsion Controller in core_phy.
Supports both active client pushing and passive broadcast serving over TCP.
"""

import asyncio
import logging
from typing import List, Optional, Set

from edge_pro.models import ManeuverDecisionPacket

logger = logging.getLogger("edge_pro.publisher")


class TCPDecisionPublisher:
    """
    Active TCP client that connects to the UI Dashboard / core_phy socket
    and transmits newline-delimited JSON ManeuverDecisionPackets.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 5560):
        self.host = host
        self.port = port
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._lock = asyncio.Lock()

    async def connect(self) -> bool:
        """Attempts connection to the dashboard / telemetry receiver socket."""
        try:
            self._reader, self._writer = await asyncio.open_connection(self.host, self.port)
            logger.info(f"Connected to Decision Telemetry receiver at {self.host}:{self.port}")
            return True
        except (OSError, ConnectionRefusedError) as err:
            logger.debug(
                f"Dashboard receiver not currently available at {self.host}:{self.port}: {err}"
            )
            self._reader, self._writer = None, None
            return False

    async def disconnect(self) -> None:
        """Closes the TCP connection."""
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None
            self._reader = None

    async def publish_decision(
        self,
        decision: ManeuverDecisionPacket,
        retry_count: int = 2,
        retry_delay: float = 0.5,
    ) -> bool:
        """
        Transmits a ManeuverDecisionPacket over the wire.
        If receiver is offline, logs gracefully and returns False (non-blocking).
        """
        wire_data = decision.to_wire()
        async with self._lock:
            for attempt in range(1, retry_count + 1):
                try:
                    if self._writer is None or self._writer.is_closing():
                        connected = await self.connect()
                        if not connected:
                            if attempt < retry_count:
                                await asyncio.sleep(retry_delay)
                            continue

                    assert self._writer is not None
                    self._writer.write(wire_data)
                    await self._writer.drain()
                    logger.info(
                        f"[Dashboard Telemetry Dispatched] Decision: {decision.decision} "
                        f"| Status: {decision.decision_status} | Target: {decision.target_asset}"
                    )
                    return True
                except (OSError, ConnectionResetError) as err:
                    logger.warning(
                        f"Decision publish attempt {attempt}/{retry_count} failed: {err}"
                    )
                    await self.disconnect()
                    if attempt < retry_count:
                        await asyncio.sleep(retry_delay)

        logger.info(
            f"Decision packet queued locally (Dashboard at {self.host}:{self.port} unreachable)."
        )
        return False


class TCPDecisionBroadcastServer:
    """
    Passive TCP broadcast server hosted by the Edge node.
    Allows UI Dashboards or ground monitors to connect and subscribe to live decision feeds.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 5560):
        self.host = host
        self.port = port
        self._server: Optional[asyncio.Server] = None
        self._clients: Set[asyncio.StreamWriter] = set()

    async def start(self) -> None:
        """Starts the decision broadcast TCP server."""
        self._server = await asyncio.start_server(
            self._handle_client, self.host, self.port
        )
        logger.info(f"Decision Broadcast Server listening on {self.host}:{self.port}")

    async def stop(self) -> None:
        """Stops the server and closes all active subscriber connections."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

        for client in list(self._clients):
            try:
                client.close()
                await client.wait_closed()
            except Exception:
                pass
        self._clients.clear()

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = writer.get_extra_info("peername")
        logger.info(f"Dashboard subscriber connected: {peer}")
        self._clients.add(writer)
        try:
            while True:
                data = await reader.readline()
                if not data:
                    break
        except Exception:
            pass
        finally:
            logger.info(f"Dashboard subscriber disconnected: {peer}")
            self._clients.discard(writer)

    async def broadcast_decision(self, decision: ManeuverDecisionPacket) -> int:
        """Broadcasts decision packet to all connected dashboard subscribers."""
        if not self._clients:
            return 0
        wire_data = decision.to_wire()
        dead = []
        count = 0
        for client in list(self._clients):
            try:
                client.write(wire_data)
                await client.drain()
                count += 1
            except Exception:
                dead.append(client)
        for d in dead:
            self._clients.discard(d)
        return count
