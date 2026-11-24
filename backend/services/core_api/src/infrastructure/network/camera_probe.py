"""An on-demand reachability test of a camera from the API server (requirements 13.2; D-023).

RTSP: one ``OPTIONS`` request; any RTSP reply (including 401 Unauthorized, since no credentials
are stored) means the server is up. ONVIF (http/https): a TCP connection and one ``HEAD``. The
result wording is generic: it never repeats the address and contains no credentials.
"""

from __future__ import annotations

import asyncio
import socket
import time
from datetime import UTC, datetime
from urllib.parse import urlsplit

from src.domain.entities.recording import HealthCheckResult

_DEFAULT_PORTS = {"rtsp": 554, "rtsps": 322, "http": 80, "https": 443}


def _result(reachable: bool, started: float, detail: str) -> HealthCheckResult:
    return HealthCheckResult(reachable, datetime.now(UTC), int((time.monotonic() - started) * 1000), detail)


async def probe_stream(stream_url: str, timeout_seconds: float = 3.0) -> HealthCheckResult:
    started = time.monotonic()
    parts = urlsplit(stream_url)
    host = parts.hostname or ""
    scheme = parts.scheme.lower()
    port = parts.port or _DEFAULT_PORTS.get(scheme, 554)
    if not host:
        return HealthCheckResult(False, datetime.now(UTC), None, "The stream URL has no host.")
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout_seconds)
    except TimeoutError:
        return _result(False, started, f"No answer within {timeout_seconds:g} s (timed out).")
    except socket.gaierror:
        return _result(False, started, "The host name could not be resolved.")
    except ConnectionRefusedError:
        return _result(False, started, "The connection was refused.")
    except OSError:
        return _result(False, started, "The host could not be reached.")
    try:
        if scheme in ("rtsp", "rtsps"):
            writer.write(f"OPTIONS {stream_url} RTSP/1.0\r\nCSeq: 1\r\nUser-Agent: cctv-core-api\r\n\r\n".encode())
        else:
            path = parts.path or "/"
            writer.write(f"HEAD {path} HTTP/1.0\r\nHost: {host}\r\nUser-Agent: cctv-core-api\r\n\r\n".encode())
        await writer.drain()
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=timeout_seconds)
        except TimeoutError:
            return _result(True, started, "Connected, but the server sent no reply (timed out).")
        status = line.decode(errors="replace").strip()
        if status.upper().startswith(("RTSP/", "HTTP/")):
            return _result(True, started, f"Connected; the server answered: {status[:60]}")
        return _result(True, started, "Connected; the reply was not understood.")
    except OSError:
        return _result(False, started, "The connection dropped.")
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass
