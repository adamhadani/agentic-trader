from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from agentic_trader.diagnostics.doctor import run_diagnostics
from agentic_trader.telemetry.collector import MetricsCollector, global_metrics


if TYPE_CHECKING:
    from asyncio import StreamReader, StreamWriter

    from agentic_trader.config import AppConfig

logger = logging.getLogger(__name__)


class MetricsServer:
    """Lightweight asynchronous HTTP server serving Prometheus metrics on /metrics,
    container probes on /healthz, and JSON diagnostics on /healthcheck.
    """

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 9108,
        collector: MetricsCollector | None = None,
        config: AppConfig | None = None,
        readiness: Callable[[], Awaitable[dict[str, Any]]] | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.collector = collector or global_metrics
        self.config = config
        self.readiness = readiness
        self._server: asyncio.Server | None = None
        self._running = False

    async def start(self) -> None:
        """Starts the async metrics server."""
        if self._running:
            return

        self._server = await asyncio.start_server(
            self._handle_client,
            self.host,
            self.port,
        )
        if self._server and self._server.sockets:
            sock = self._server.sockets[0]
            self.port = sock.getsockname()[1]
        self._running = True
        logger.info(f"Prometheus metrics exporter listening on http://{self.host}:{self.port}/metrics")

    async def stop(self) -> None:
        """Gracefully shuts down the metrics server."""
        if not self._running or not self._server:
            return

        logger.info("Stopping Prometheus metrics exporter...")
        self._server.close()
        await self._server.wait_closed()
        self._running = False
        self._server = None

    async def _handle_client(self, reader: StreamReader, writer: StreamWriter) -> None:
        try:
            line = await reader.readline()
            if not line:
                writer.close()
                await writer.wait_closed()
                return

            request_line = line.decode("utf-8", errors="ignore").strip()
            parts = request_line.split()
            if len(parts) < 2:
                writer.close()
                await writer.wait_closed()
                return

            method, path = parts[0], parts[1]

            # Read remaining headers until empty line
            while True:
                h_line = await reader.readline()
                if not h_line or h_line == b"\r\n" or h_line == b"\n":
                    break

            if method == "GET" and path in ("/metrics", "/metrics/"):
                body_text = self.collector.format_prometheus_exposition()
                body_bytes = body_text.encode("utf-8")
                headers = (
                    "HTTP/1.1 200 OK\r\n"
                    "Content-Type: text/plain; version=0.0.4; charset=utf-8\r\n"
                    f"Content-Length: {len(body_bytes)}\r\n"
                    "Connection: close\r\n"
                    "\r\n"
                )
                writer.write(headers.encode("utf-8") + body_bytes)
                await writer.drain()

            elif method == "GET" and path in ("/readyz", "/readyz/"):
                readiness_report = (
                    await self.readiness()
                    if self.readiness
                    else {"ready": False, "detail": "No daemon readiness provider"}
                )
                body_bytes = json.dumps(readiness_report).encode()
                status = "200 OK" if readiness_report["ready"] else "503 Service Unavailable"
                writer.write(
                    (
                        f"HTTP/1.1 {status}\r\nContent-Type: application/json; charset=utf-8\r\n"
                        f"Content-Length: {len(body_bytes)}\r\nConnection: close\r\n\r\n"
                    ).encode()
                    + body_bytes
                )
                await writer.drain()

            elif method == "GET" and path in ("/healthcheck", "/healthcheck/"):
                report = await run_diagnostics(self.config)
                report_json = report.model_dump_json(indent=2)
                body_bytes = report_json.encode("utf-8")
                headers = (
                    "HTTP/1.1 200 OK\r\n"
                    "Content-Type: application/json; charset=utf-8\r\n"
                    f"Content-Length: {len(body_bytes)}\r\n"
                    "Connection: close\r\n"
                    "\r\n"
                )
                writer.write(headers.encode("utf-8") + body_bytes)
                await writer.drain()

            elif method == "GET" and path in ("/healthz", "/healthz/", "/"):
                body_bytes = b"OK\n"
                headers = (
                    "HTTP/1.1 200 OK\r\n"
                    "Content-Type: text/plain; charset=utf-8\r\n"
                    f"Content-Length: {len(body_bytes)}\r\n"
                    "Connection: close\r\n"
                    "\r\n"
                )
                writer.write(headers.encode("utf-8") + body_bytes)
                await writer.drain()

            else:
                body_bytes = b"Not Found\n"
                headers = (
                    "HTTP/1.1 404 Not Found\r\n"
                    "Content-Type: text/plain; charset=utf-8\r\n"
                    f"Content-Length: {len(body_bytes)}\r\n"
                    "Connection: close\r\n"
                    "\r\n"
                )
                writer.write(headers.encode("utf-8") + body_bytes)
                await writer.drain()

        except Exception as e:
            logger.debug(f"Metrics client handling exception: {e}")
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
