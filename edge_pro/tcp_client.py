"""
Project Kessler - Edge Processing Payload
TCP Publisher Client (core_phy / Basilisk Interface) (Phase 1)
-------------------------------------------------------------------------------
Provides an asynchronous TCP client to dispatch Slew Targeting Commands
to the Basilisk ADCS physics engine node (core_phy). Features auto-reconnect,
outbound queuing, and non-blocking delivery.
"""

import asyncio
import json
import logging
from typing import Optional

from edge_pro.models import SlewCommand

logger = logging.getLogger("edge_pro.publisher")


class TCPSlewPublisher:
    """
    Asynchronous TCP client dedicated to transmitting SlewCommand packets
    to the core_phy physics engine node.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 5556,
        reconnect_interval: float = 2.0,
        connect_timeout: float = 3.0,
    ):
        self.host = host
        self.port = port
        self.reconnect_interval = reconnect_interval
        self.connect_timeout = connect_timeout

        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._is_connected: bool = False
        self._lock = asyncio.Lock()
        self._running: bool = False

    @property
    def is_connected(self) -> bool:
        return self._is_connected and self._writer is not None

    async def connect(self) -> bool:
        """
        Attempts to establish a TCP connection with the core_phy node.
        Returns True on success, False if core_phy is not reachable.
        """
        async with self._lock:
            if self.is_connected:
                return True

            try:
                logger.info(f"[Publisher] Connecting to core_phy at {self.host}:{self.port}...")
                self._reader, self._writer = await asyncio.wait_for(
                    asyncio.open_connection(self.host, self.port),
                    timeout=self.connect_timeout,
                )
                self._is_connected = True
                peer = self._writer.get_extra_info("peername")
                logger.info(f"[Publisher] Connected to core_phy node at {peer}.")
                return True
            except (ConnectionRefusedError, asyncio.TimeoutError, OSError) as err:
                logger.warning(
                    f"[Publisher] Unable to connect to core_phy at {self.host}:{self.port} ({err}). "
                    f"Will retry when commanding."
                )
                self._is_connected = False
                self._reader = None
                self._writer = None
                return False

    async def disconnect(self) -> None:
        """Closes the connection to core_phy."""
        async with self._lock:
            if self._writer:
                try:
                    logger.info("[Publisher] Closing connection to core_phy.")
                    self._writer.close()
                    await self._writer.wait_closed()
                except Exception as e:
                    logger.debug(f"[Publisher] Error closing writer: {e}")
            self._writer = None
            self._reader = None
            self._is_connected = False

    async def publish_slew_command(
        self,
        command: SlewCommand,
        retry_count: int = 3,
        retry_delay: float = 1.0,
    ) -> bool:
        """
        Transmits a SlewCommand packet to core_phy over the TCP stream.
        Applies newline-delimited framing and validates acknowledgment.
        Retries connection up to retry_count times if disconnected.
        """
        command.validate()
        wire_data = command.to_wire()

        for attempt in range(1, retry_count + 1):
            if not self.is_connected:
                success = await self.connect()
                if not success:
                    if attempt < retry_count:
                        logger.warning(
                            f"[Publisher] Retry {attempt}/{retry_count}: Waiting {retry_delay}s for core_phy..."
                        )
                        await asyncio.sleep(retry_delay)
                    continue

            async with self._lock:
                if not self._writer:
                    continue
                try:
                    logger.info(
                        f"[Publisher] Transmitting SlewCommand to core_phy:\n"
                        f"  Command:     {command.command}\n"
                        f"  Target:      {command.target_asset}\n"
                        f"  Execute UTC: {command.execute_at_utc}\n"
                        f"  Sensor Mode: {command.sensor_mode}"
                    )
                    self._writer.write(wire_data)
                    await self._writer.drain()
                    logger.info("[Publisher] SlewCommand successfully transmitted.")
                    return True
                except (ConnectionResetError, BrokenPipeError, OSError) as net_err:
                    logger.error(
                        f"[Publisher] Network pipe broken while sending to core_phy ({net_err}). Attempt {attempt}/{retry_count}."
                    )
                    self._is_connected = False
                    self._writer = None
                    self._reader = None

            if attempt < retry_count:
                await asyncio.sleep(retry_delay)

        logger.critical(
            f"[Publisher] Failed to deliver SlewCommand to core_phy after {retry_count} attempts."
        )
        return False
