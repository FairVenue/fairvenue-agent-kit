"""No-proxy/no-cookie fixed-origin HTTP adapter. No connection occurs on construction."""

from __future__ import annotations

import asyncio
import base64
import http.client
import json
import math
import os
import ssl
import sys
import time
from urllib.parse import urlsplit

if __name__ != "__main__":
    from .protocol import AdmitRefusal, AgentError, fail


# Worker exit status of a bounded JSON admit refusal body (validated by the
# client, never trusted here); 0 is an ordinary 200 reply, anything else fails.
_REFUSALS = {409: 2, 429: 3}
# The only paths: the agent endpoints, the bot-signed account read and the
# public market read, which needs the TS2 tick and lot sizes. No cookie or key
# reaches any of them.
_PATHS = ("/v1/agent/context", "/v1/agent/prepare", "/v1/agent/admit", "/v1/agent/recovery",
          "/v1/ts2/agent/read", "/api/v1/info")


def _limit(path):
    """Largest accepted reply: 128 KiB for the market snapshot and the account
    read, else a 16 KiB agent reply."""
    return 131072 if path in ("/api/v1/info", "/v1/ts2/agent/read") else 16384


def _http(origin, path, body, timeout):
    """Disposable worker only: no SDK signer, cookies, callback or private-key import.

    Returns ``(exit_status, body)``: 0 for a 200 reply, or ``_REFUSALS`` for a
    JSON 409/429 admit reply whose body the client validates exactly.
    """
    url = urlsplit(origin)
    deadline = time.monotonic() + timeout
    connection = None
    try:
        if url.scheme == "https":
            connection = http.client.HTTPSConnection(
                url.hostname, url.port, timeout=timeout, context=ssl.create_default_context()
            )
        else:
            connection = http.client.HTTPConnection(url.hostname, url.port, timeout=timeout)
        # No ambient requests session, Cookie, Authorization, proxy or redirect support.
        connection.request("POST", path, body=body, headers={
            "Content-Type": "application/json", "Origin": origin,
            "Cache-Control": "no-store", "Connection": "close",
        })
        response = connection.getresponse()
        status = 0 if response.status == 200 else (
            _REFUSALS.get(response.status) if path == "/v1/agent/admit" else None)
        if status is None or response.getheader("Content-Type", "").split(";", 1)[0].strip() != "application/json":
            raise ValueError()
        chunks = []
        size = 0
        limit = _limit(path)
        for _ in range(limit // 128):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError()
            if connection.sock is not None:
                connection.sock.settimeout(remaining)
            chunk = response.read1(min(2048, limit + 1 - size))
            if time.monotonic() >= deadline:
                raise ValueError()
            if not chunk:
                return status, b"".join(chunks)
            size += len(chunk)
            if size > limit:
                raise ValueError()
            chunks.append(chunk)
        raise ValueError()
    finally:
        if connection is not None:
            connection.close()


async def _drain(task):
    """Defer even repeated caller cancellation until this exact cleanup task finishes."""
    interrupted = False
    while True:
        try:
            return await asyncio.shield(task), interrupted
        except asyncio.CancelledError:
            interrupted = True
            if task.done():
                return task.result(), interrupted


class HttpTransport:
    def __init__(self, origin):
        if type(origin) is not str or len(origin) > 256:
            fail()
        try:
            url = urlsplit(origin)
            port = url.port
            if (url.username is not None or url.password is not None or url.query or url.fragment
                    or url.path or not url.hostname or url.scheme not in ("https", "http")
                    or (port is not None and not 1 <= port <= 65535)
                    or (url.scheme == "http" and url.hostname not in ("127.0.0.1", "::1"))):
                fail()
            host = url.hostname
            if host != "::1" and not all(c in "abcdefghijklmnopqrstuvwxyz0123456789.-" for c in host):
                fail()
            authority = ("[::1]" if host == "::1" else host) + (f":{port}" if port else "")
            if origin != f"{url.scheme}://{authority}":
                fail()
            self.__scheme, self.__host, self.__port = url.scheme, host, port
            self.__origin = origin
        except Exception:
            raise AgentError() from None

    async def post(self, path, body, timeout):
        if (path not in _PATHS
                or type(body) is not bytes or len(body) > 8192
                or type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 15):
            fail()
        # A thread watchdog cannot stop libc DNS/TLS before a socket is exposed.
        # A disposable *isolated* interpreter has no signer/private key and can
        # be killed/reaped across DNS/connect, slow-drip headers and body reads.
        deadline = time.monotonic() + timeout
        process = None
        exchange = None
        creation = None
        try:
            creation = asyncio.create_task(asyncio.create_subprocess_exec(
                sys.executable, "-I", os.path.realpath(__file__),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, env={},
            ))
            # Cancellation during spawn must not lose ownership of the created child.
            process = await asyncio.shield(creation)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                fail("deadline")
            request = json.dumps({"origin": self.__origin, "path": path,
                                  "body": base64.b64encode(body).decode("ascii"),
                                  "timeout": remaining}, separators=(",", ":")).encode()
            if len(request) > 16384:
                fail()

            limit = _limit(path)

            async def pipe_exchange():
                process.stdin.write(request)
                await process.stdin.drain()
                process.stdin.close()
                chunks = []
                size = 0
                for _ in range(limit // 128):
                    chunk = await process.stdout.read(min(2048, limit + 1 - size))
                    if not chunk:
                        await process.wait()
                        return b"".join(chunks)
                    size += len(chunk)
                    if size > limit:
                        fail("response_invalid")
                    chunks.append(chunk)
                fail("response_invalid")

            exchange = asyncio.create_task(pipe_exchange())
            done, _ = await asyncio.wait({exchange}, timeout=remaining)
            if not done:
                fail("deadline")
            result = exchange.result()
            if time.monotonic() >= deadline:
                fail("deadline")
            if type(result) is not bytes or len(result) > limit:
                fail("unavailable")
            refusal = {code: status for status, code in _REFUSALS.items()}.get(process.returncode)
            if refusal is not None and path == "/v1/agent/admit":
                raise AdmitRefusal(refusal, result)
            if process.returncode != 0:
                fail("unavailable")
            return result
        except AgentError:
            raise
        except Exception:
            raise AgentError("unavailable") from None
        finally:
            interrupted = False
            if process is None and creation is not None:
                try:
                    process, interrupted = await _drain(creation)
                except Exception:
                    pass  # Spawn failed; no owned child and no raw OS error output.
            if process is not None:
                async def cleanup():
                    if process.returncode is None:
                        try:
                            process.kill()  # Exact known worker only; it starts no children.
                        except ProcessLookupError:
                            pass
                    # Keep the SDK callback lease until worker death and bounded pipe drain.
                    await process.wait()
                    if exchange is not None:
                        await asyncio.gather(exchange, return_exceptions=True)

                _, canceled = await _drain(asyncio.create_task(cleanup()))
                interrupted = interrupted or canceled
            if interrupted:
                raise asyncio.CancelledError()


if __name__ == "__main__":
    # Private stdlib worker, not a published SDK CLI or source activation path.
    # Only the parent adapter supplies public signed HTTP data via a bounded pipe.
    try:
        request = json.loads(sys.stdin.buffer.read(16385))
        if (type(request) is not dict or set(request) != {"origin", "path", "body", "timeout"}
                or request["path"] not in _PATHS
                or type(request["origin"]) is not str or len(request["origin"]) > 256
                or type(request["timeout"]) not in (int, float)
                or not math.isfinite(request["timeout"]) or not 0 < request["timeout"] <= 15):
            raise ValueError()
        url = urlsplit(request["origin"])
        if (url.scheme not in ("http", "https") or not url.hostname or url.username is not None
                or url.password is not None or url.path or url.query or url.fragment
                or (url.scheme == "http" and url.hostname not in ("127.0.0.1", "::1"))):
            raise ValueError()
        body = base64.b64decode(request["body"], validate=True)
        if len(body) > 8192:
            raise ValueError()
        # Header limits are scoped to this disposable interpreter, not the caller.
        http.client._MAXLINE = 8192
        http.client._MAXHEADERS = 32
        status, reply = _http(request["origin"], request["path"], body, request["timeout"])
        sys.stdout.buffer.write(reply)
        sys.stdout.buffer.flush()
    except BaseException:
        sys.exit(1)  # No response/body/provider/error text on stderr.
    sys.exit(status)
