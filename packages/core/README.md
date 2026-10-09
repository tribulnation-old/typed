# Typed Core

> Shared core types and utilities for Typed Clients

[![PyPI version](https://img.shields.io/pypi/v/typed-core.svg)](https://pypi.org/project/typed-core/)
[![License](https://img.shields.io/pypi/l/typed-core.svg)](LICENSE)

Every [Typed Client](https://tribulnation.com/blog/clients) builds its `core` on top of this
package. Prefer it over a client-local copy of the same logic — a client's own `core/` should
hold only what's genuinely venue-specific (envelope extraction, error mapping, signing, wire
quirks), not a reimplementation of transport, timestamps, or error types.

## Installation

```bash
pip install typed-core
```

## What it provides

| module | contents |
| --- | --- |
| `typed_core.exceptions` | `Error`, `NetworkError`, `ValidationError`, `ApiError` (`BadRequest`, `AuthError`, `RateLimited`), `LogicError` |
| `typed_core.http` | async HTTP client and response helpers |
| `typed_core.ws` | websocket socket, streams, JSON-RPC, streams-over-RPC |
| `typed_core.grpc` | `GrpcClient`/`GrpcEndpoint`, `wrap_exceptions` for unary calls and `server_stream` (a `StreamManager`) for server-streaming ones, mapping grpclib failures to the errors above by gRPC status (requires the `grpc` extra) |
| `typed_core.times` | `TimeConverter`, `EpochConverter`, `IsoConverter` — parse/dump between a venue's wire timestamp and a real `datetime` |
| `typed_core.util` | `RateLimit`, paging and stream helpers |

```python
from typed_core.times import EpochConverter, IsoConverter

timestamp_millis = EpochConverter.milliseconds()  # epoch, milliseconds
timestamp_iso = IsoConverter()                    # RFC 3339, Z-suffixed
```

A wire value carrying no offset is read as UTC, matching what `dump` already assumes of a
naive `datetime`; pass `tz=None` to keep it naive instead. Every converter raises
`ValueError` on a value of the wrong kind, so a `null` on a non-nullable field arrives as
an ordinary validation error rather than an `AttributeError`.

### HTTP configuration

```python
from typed_core.http import HttpClient

async with HttpClient(timeout=30, proxy="http://localhost:8080") as http:
  response = await http.request("GET", "https://example.com")
  response = await http.request("GET", "https://example.com", timeout=120)
```

`timeout` defaults to five seconds of network inactivity; `None` disables timeouts.
Use `httpx.Timeout` for separate connect, read, write, and pool limits. Per-request
`timeout` overrides the client default. Requests are single-attempt, with retries left
to the caller.

`proxy` accepts a URL or an `httpx.Proxy`, including authenticated proxies. When omitted,
HTTPX uses `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, and `NO_PROXY` from the environment.
An explicit proxy overrides environment proxy routing, including `NO_PROXY`. For an HTTPS
destination, an `http://` proxy URL commonly works by tunneling the TLS connection.
`trust_env=False` ignores HTTPX environment settings (proxies and certificate locations),
but still uses an explicit `proxy`. See [HTTPX environment variables](https://www.python-httpx.org/environment_variables/).

`limits` continues to control connection pooling. The existing default disables keepalive
when uppercase `HTTP_PROXY` or `HTTPS_PROXY` is present; pass explicit `httpx.Limits` to
override it, including when using `trust_env=False`.

All construction settings apply when the underlying HTTPX client is lazily created. An
existing client supplied through the legacy `_client` field keeps its own configuration.
A Typed client accepting `http=...` manages that transport's cleanup when its context exits;
use separate transports for independently managed client lifetimes.

### Paging

Every generated `<method>_paged` returns a `PaginatedResponse`: awaitable (every row,
flattened) and async-iterable (one page of rows at a time). Each page is one pure
`next(state)` call, so a caller can retry or resume a single page rather than the whole walk.

```python
from typed_core import PaginatedResponse

paging = client.market.kline_paged(symbol='BTCUSDT', interval='1', start=start, end=end)
candles = await paging                       # every row, flattened
async for rows in paging: ...                # one page at a time
async for page in paging.pages():            # Page(rows, state, next), for checkpointing
  checkpoint(page.next)
paging.resume(saved_state)                   # restart from a checkpointed state
paging.via(retried)                          # route every page fetch through a middleware
```

`via(call)` hands each page fetch to `call` as one zero-argument coroutine function, so a
retry or logging layer wraps a page without unrolling the loop by hand.

### WebSocket connection drops

Streams and requests are bound to the connection they were made on. When it drops, or the
client is closed, everything bound to it raises `NetworkError`:

- a stream raises right away if it is being iterated, otherwise on its next read (after any
  message it had already received); it never reconnects on its own
- a request waiting for its reply raises

Leaving a stream whose connection is gone (`async with` exit or `unsubscribe()`) does
nothing: no frame, no reconnect, no exception, so the stream's own `NetworkError` is what
surfaces. A new subscription or request after a drop simply opens a fresh connection.
Resubscribing is up to the caller:

```python
from typed_core.exceptions import NetworkError

while True:
  try:
    async with socket.subscribe('trades') as trades:
      async for trade in trades:
        ...
  except NetworkError:
    await asyncio.sleep(1)  # then subscribe again, on a fresh connection
```

Closing is cancellation-safe. Cancelling one caller waiting for a connection never affects
the other callers or the connect itself. The owner's exit never waits for a connect in
flight. Instead, that connect closes its own connection once it completes, and the work that
joined it before the exit raises `NetworkError`. Work that joined only after the exit
reconnects once. An exit cancelled mid-close aborts the connection instead of leaving it
half-open, and an `HttpClient` exit that is cancelled still closes its client.

Clients re-export the exceptions users are expected to catch from their own package root, so
`from kraken import AuthError` works while implementation imports still come from
`typed_core`. Route those re-exports through `lazy_loader.attach_stub`, not a plain
`from typed_core.exceptions import ...` in an `__init__.py` — the latter breaks type-checking
for every downstream consumer of a `py.typed` package.

## License

MIT — see [LICENSE](LICENSE).
