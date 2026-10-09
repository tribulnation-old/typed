"""Shared gRPC transport primitives -- promoted from dYdX's chain/core.py (design doc §9,
2026-08-31 codegen mechanization)."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from functools import wraps
from types import TracebackType

from grpclib.client import Channel
from grpclib.const import Cardinality, Status
from grpclib.exceptions import GRPCError, ProtocolError, StreamTerminatedError
from typing_extensions import Any, Collection, Mapping, ParamSpec, Self, TypeVar

from .exceptions import ApiError, AuthError, BadRequest, Error, NetworkError, RateLimited
from .util.streams import Stream, StreamManager

P = ParamSpec('P')
T = TypeVar('T')

MetadataLike = Mapping[str, str | bytes] | Collection[tuple[str, str | bytes]]
"""gRPC call metadata: a mapping, or (key, value) pairs for repeated keys."""

GRPC_STATUS_ERRORS: dict[Status, type[Error]] = {
  Status.UNAVAILABLE: NetworkError,
  Status.DEADLINE_EXCEEDED: NetworkError,
  Status.INVALID_ARGUMENT: BadRequest,
  Status.NOT_FOUND: BadRequest,
  Status.OUT_OF_RANGE: BadRequest,
  Status.FAILED_PRECONDITION: BadRequest,
  Status.ALREADY_EXISTS: BadRequest,
  Status.UNAUTHENTICATED: AuthError,
  Status.PERMISSION_DENIED: AuthError,
  Status.RESOURCE_EXHAUSTED: RateLimited,
}
"""typed_core error raised for each gRPC status; any other status raises `ApiError`."""

TRANSPORT_ERRORS = (ProtocolError, StreamTerminatedError, ConnectionError, TimeoutError)
"""grpclib and socket failures raised as `NetworkError`."""

def grpc_error(exc: GRPCError) -> Error:
  """Return the typed_core error for a grpclib `GRPCError`, by its status.

  Args:
    exc: The error a gRPC call raised.
  """
  error = GRPC_STATUS_ERRORS.get(exc.status, ApiError)
  return error(str(exc))

@contextmanager
def map_grpc_errors() -> Iterator[None]:
  """Re-raise grpclib failures inside the block as typed_core errors.

  Transport failures (GOAWAY, HTTP/2 protocol errors, terminated streams, refused or
  timed-out connections) raise `NetworkError`; a `GRPCError` raises the error
  `GRPC_STATUS_ERRORS` assigns its status, `ApiError` otherwise. The original exception
  is kept as `__cause__`.
  """
  try:
    yield
  except TRANSPORT_ERRORS as exc:
    raise NetworkError(str(exc)) from exc
  except GRPCError as exc:
    raise grpc_error(exc) from exc

def wrap_exceptions(fn: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
  """Map grpclib failures of a unary gRPC call to typed_core errors (`map_grpc_errors`).

  Business errors returned inside a successful response are left untouched.
  """
  @wraps(fn)
  async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
    """Await the wrapped gRPC call, normalizing grpclib exceptions."""
    with map_grpc_errors():
      return await fn(*args, **kwargs)
  return wrapper

def server_stream(
  channel: Channel,
  route: str,
  request: Any,
  response_type: type[T],
  *,
  metadata: MetadataLike | None = None,
) -> StreamManager[T, None, None]:
  """Open a server-streaming gRPC call as a `StreamManager`, like every other stream.

  Awaiting it (or entering it with `async with`) sends `request` and waits for the
  server's response headers, so a call the server rejects outright (an invalid request,
  an unimplemented method, an unreachable server) raises there rather than on the first
  iteration. The returned `Stream` has no subscription reply (`reply` is `None`); it
  yields every response message and ends without an error when the server ends the
  stream normally. `unsubscribe()` cancels the call; it is idempotent, and any iteration
  in progress then ends quietly. All grpclib failures map through `map_grpc_errors`.

  Args:
    channel: Channel to issue the call over.
    route: Fully qualified method route, `/package.Service/Method`.
    request: Request message, sent once.
    response_type: Response message type.
    metadata: gRPC metadata sent with the call.
  """
  async def connect() -> Stream[T, None, None]:
    """Send the request and wait for the response headers."""
    call = channel.request(
      route, Cardinality.UNARY_STREAM, type(request), response_type, metadata=metadata
    )
    await call.__aenter__()
    done = False  # the server ended the call and its trailers were read
    closing = asyncio.get_running_loop().create_future()

    async def unsubscribe() -> None:
      """Cancel the call (unless it already ended) and release it; idempotent."""
      if closing.done():
        return
      closing.set_result(None)
      with suppress(Exception):
        if not done:
          await call.cancel()
        await call.__aexit__(None, None, None)

    try:
      with map_grpc_errors():
        await call.send_message(request, end=True)
        await call.recv_initial_metadata()
    except BaseException as exc:
      closing.set_result(None)
      # Releasing with the error skips waiting for trailers the server already sent.
      with suppress(BaseException):
        await call.__aexit__(type(exc), exc, exc.__traceback__)
      raise

    async def receive() -> T | None:
      """Next message, or `None` once the call ended or was unsubscribed."""
      nonlocal done
      with map_grpc_errors():
        message = await call.recv_message()
        if message is None:
          await call.recv_trailing_metadata()
          done = True
      return message

    async def messages() -> AsyncIterator[T]:
      """Yield every response until the server ends the call or it is unsubscribed."""
      try:
        while not closing.done():
          # Race the receive against `unsubscribe`, which grpclib's reset does not wake.
          next_message = asyncio.ensure_future(receive())
          try:
            await asyncio.wait([next_message, closing], return_when=asyncio.FIRST_COMPLETED)
          finally:
            if not next_message.done():
              next_message.cancel()
              with suppress(BaseException):
                await next_message
          if next_message.cancelled():
            return
          if next_message.exception() is not None and closing.done():
            return
          message = next_message.result()
          if message is None:
            return
          yield message
      finally:
        await unsubscribe()

    return Stream(reply=None, stream=messages(), unsubscribe=unsubscribe)

  return StreamManager(connect=connect)

@dataclass(kw_only=True)
class GrpcClient:
  """Async gRPC transport that owns a lazily opened channel.

  Not frozen, unlike `GrpcEndpoint` below -- it needs a real mutable `_channel` slot
  to cache into, the same shape `HttpClient._client` already uses for the identical
  lazy-open/close contract on the HTTP side. `GrpcEndpoint`'s own freeze is a separate
  concern (matching every other `Endpoint` composition base) and doesn't require the
  `Client` object it merely holds a reference to to be frozen too.
  """

  host: str
  port: int = 443
  ssl: bool = True
  _channel: Channel | None = field(default=None, init=False, repr=False)

  @property
  def channel(self) -> Channel:
    """Return the gRPC channel, creating it on first use."""
    if self._channel is None:
      self._channel = Channel(self.host, self.port, ssl=self.ssl)
    return self._channel

  async def __aenter__(self) -> Self:
    """Take ownership without connecting -- the channel opens lazily on first use,
    matching `HttpClient.__aenter__`'s own contract."""
    return self

  async def __aexit__(
    self,
    exc_type: type[BaseException] | None,
    exc: BaseException | None,
    traceback: TracebackType | None,
  ):
    """Close the channel for an async client context."""
    self.close()

  def close(self):
    """Close the open channel, if one was created."""
    if self._channel is not None:
      self._channel.close()
      self._channel = None

@dataclass(kw_only=True, frozen=True)
class GrpcEndpoint:
  """Base for every generated/hand-written gRPC module -- talks only to `client`."""

  client: GrpcClient

  @property
  def channel(self) -> Channel:
    """Return the active shared gRPC channel."""
    return self.client.channel
