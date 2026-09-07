"""
Project Kessler - Edge Processing Payload
TCP Ingestion Listener (Ground AI Interface) (Phase 1)
-------------------------------------------------------------------------------
Provides an asynchronous, non-blocking TCP server to listen for and ingest
Refined Conjunction Data Message (CDM) packets from Ground AI.
Features stream framing, partial-packet buffering, and strict contract validation.
"""

import asyncio
import json
import logging
from typing import Callable, Awaitable, Optional, Set

from edge_pro.models import RefinedCDM, PacketValidationError

logger = logging.getLogger("edge_pro.ingest")


class TCPIngestServer:
    """
    Asynchronous TCP server that listens for incoming Ground AI Refined CDM packets.
    Maintains non-blocking socket I/O using asyncio.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 5555,
        on_cdm_callback: Optional[Callable[[RefinedCDM], Awaitable[None]]] = None,
        max_buffer_size: int = 65536,
    ):
        self.host = host
        self.port = port
        self.on_cdm_callback = on_cdm_callback
        self.max_buffer_size = max_buffer_size

        self._server: Optional[asyncio.AbstractServer] = None
        self._is_running: bool = False
        self._active_connections: Set[asyncio.StreamWriter] = set()

    @property
    def is_running(self) -> bool:
        return self._is_running

    async def start(self) -> None:
        """Binds and starts the non-blocking TCP ingestion server."""
        if self._is_running:
            logger.warning("TCP Ingestion Server already running.")
            return

        logger.info(f"Starting TCP Ingestion Server on {self.host}:{self.port}...")
        self._server = await asyncio.start_server(
            self._handle_client,
            self.host,
            self.port,
            reuse_address=True,
        )
        self._is_running = True
        addrs = ", ".join(str(sock.getsockname()) for sock in self._server.sockets)
        logger.info(f"TCP Ingestion Server active and listening on {addrs}")

    async def stop(self) -> None:
        """Gracefully closes the ingestion server and terminates active client sessions."""
        if not self._is_running:
            return

        logger.info("Stopping TCP Ingestion Server...")
        self._is_running = False

        # Close all active client connections
        for writer in list(self._active_connections):
            try:
                writer.close()
                await writer.wait_closed()
            except Exception as e:
                logger.debug(f"Error closing client writer during shutdown: {e}")
        self._active_connections.clear()

        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

        logger.info("TCP Ingestion Server stopped.")

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """
        Manages an incoming TCP client connection from Ground AI.
        Handles newline-delimited stream framing and packet boundary resolution.
        """
        client_addr = writer.get_extra_info("peername")
        logger.info(f"[Ingest] Connection established with Ground AI node at {client_addr}")
        self._active_connections.add(writer)

        buffer = bytearray()

        try:
            while self._is_running:
                chunk = await reader.read(4096)
                if not chunk:
                    logger.info(f"[Ingest] Ground AI node {client_addr} disconnected.")
                    break

                buffer.extend(chunk)

                if len(buffer) > self.max_buffer_size:
                    logger.error(
                        f"[Ingest] Buffer exceeded {self.max_buffer_size} bytes from {client_addr}. "
                        "Flushing buffer to protect memory."
                    )
                    buffer.clear()
                    continue

                # Process all newline-delimited packets in buffer
                while b"\n" in buffer:
                    line, _, remaining = buffer.partition(b"\n")
                    buffer = bytearray(remaining)

                    line_str = line.decode("utf-8", errors="replace").strip()
                    if not line_str:
                        continue

                    await self._process_raw_message(line_str, writer)

            # Process any remaining buffer upon EOF if it forms a valid JSON
            if buffer:
                tail_str = buffer.decode("utf-8", errors="replace").strip()
                if tail_str:
                    await self._process_raw_message(tail_str, writer)

        except asyncio.CancelledError:
            pass
        except (ConnectionResetError, BrokenPipeError):
            logger.info(f"[Ingest] Ground AI node {client_addr} closed connection.")
        except Exception as ex:
            logger.exception(f"[Ingest] Exception handling client {client_addr}: {ex}")
        finally:
            self._active_connections.discard(writer)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _process_raw_message(self, raw_str: str, writer: asyncio.StreamWriter) -> None:
        """Parses and validates incoming JSON text as a RefinedCDM."""
        try:
            cdm = RefinedCDM.from_json(raw_str)
            logger.info(
                f"[Ingest] Successfully parsed REFINED_CDM:\n"
                f"  Primary: {cdm.conjunction_data.primary_asset}\n"
                f"  Secondary: {cdm.conjunction_data.secondary_asset}\n"
                f"  TCA: {cdm.conjunction_data.time_of_closest_approach}"
            )

            # Send acknowledgment back to Ground AI if socket is writable
            ack = json.dumps({"status": "ACK", "message": "REFINED_CDM_RECEIVED"}) + "\n"
            writer.write(ack.encode("utf-8"))
            await writer.drain()

            # Dispatch to payload manager / scheduler callback
            if self.on_cdm_callback:
                await self.on_cdm_callback(cdm)

        except PacketValidationError as pve:
            logger.error(f"[Ingest] Packet schema validation failed: {pve}")
            err_resp = json.dumps({"status": "NACK", "error": str(pve)}) + "\n"
            writer.write(err_resp.encode("utf-8"))
            await writer.drain()
        except Exception as ex:
            logger.error(f"[Ingest] Unexpected error decoding message '{raw_str[:100]}...': {ex}")
