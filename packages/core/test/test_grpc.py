"""Pin the gRPC transport primitives promoted from typed-dydx's `chain/core.py` (design doc §9):
`wrap_exceptions` mapping a transport failure to `NetworkError`, and `GrpcClient.channel`'s
lazy-construction contract, mirroring `test_http.py`/`test_socket.py`'s own reasoning for the
other two transports.
"""
import asyncio
import socket
from contextlib import asynccontextmanager

import pytest
from grpclib.client import Channel
from grpclib.const import Cardinality, Handler
from grpclib.encoding.base import CodecBase
from grpclib.server import Server, Stream
from grpclib.const import Status
from grpclib.exceptions import GRPCError, ProtocolError, StreamTerminatedError
from typed_core.exceptions import ApiError, AuthError, BadRequest, NetworkError, RateLimited
from typed_core.grpc import GrpcClient, GrpcEndpoint, server_stream, wrap_exceptions

@wrap_exceptions
async def _raises_protocol_error():
  """Always raise a transport-level `ProtocolError`, for `wrap_exceptions` to normalize."""
  raise ProtocolError('boom')

@pytest.mark.asyncio
async def test_wrap_exceptions_maps_transport_failure_to_network_error():
  """A grpclib transport failure surfaces as `typed_core.exceptions.NetworkError`."""
  with pytest.raises(NetworkError):
    await _raises_protocol_error()

@pytest.mark.asyncio
async def test_aenter_opens_nothing():
  """Entering a `GrpcClient` must not construct a channel -- mirrors `test_http.py`'s
  `test_aenter_opens_nothing` for `HttpClient`, whose own `__aenter__` docstring states
  the same contract this class's docstring claims ("owns a lazily opened channel"):
  taking ownership via `async with` opens nothing, first use opens the transport.
  """
  client = GrpcClient(host='example.com', port=443)
  async with client as opened:
    assert opened is client
    assert client._channel is None

@pytest.mark.asyncio
async def test_grpc_client_channel_is_lazy():
  """`GrpcClient.channel` is built on first access, not at construction.

  Run under `pytest.mark.asyncio` (matching this package's other transport tests, e.g.
  `test_socket.py`/`test_http.py`) so a running event loop is present when `.channel`
  triggers grpclib's `Channel(...)` construction -- `grpclib.client.Channel.__init__`
  calls `asyncio.get_event_loop()` directly, which raises outside a running loop once a
  prior test's own loop has been torn down.
  """
  client = GrpcClient(host='example.com', port=443)
  assert client._channel is None
  channel = client.channel
  assert client._channel is channel

@pytest.mark.asyncio
async def test_close_clears_the_cached_channel():
  """`close()` closes the cached channel and clears the slot, so a later `.channel`
  access opens a fresh one rather than reusing a closed connection.
  """
  client = GrpcClient(host='example.com', port=443)
  channel = client.channel
  client.close()
  assert client._channel is None
  assert client.channel is not channel


@pytest.mark.asyncio
@pytest.mark.parametrize('status, expected', [
  (Status.UNAVAILABLE, NetworkError),
  (Status.DEADLINE_EXCEEDED, NetworkError),
  (Status.INVALID_ARGUMENT, BadRequest),
  (Status.NOT_FOUND, BadRequest),
  (Status.OUT_OF_RANGE, BadRequest),
  (Status.FAILED_PRECONDITION, BadRequest),
  (Status.ALREADY_EXISTS, BadRequest),
  (Status.UNAUTHENTICATED, AuthError),
  (Status.PERMISSION_DENIED, AuthError),
  (Status.RESOURCE_EXHAUSTED, RateLimited),
  (Status.INTERNAL, ApiError),
  (Status.UNKNOWN, ApiError),
  (Status.UNIMPLEMENTED, ApiError),
  (Status.ABORTED, ApiError),
  (Status.CANCELLED, ApiError),
  (Status.DATA_LOSS, ApiError),
])
async def test_wrap_exceptions_maps_grpc_status(status, expected):
  """Each gRPC status raises its typed_core error, keeping the original as the cause."""
  error = GRPCError(status, 'boom')

  @wrap_exceptions
  async def call():
    """Fail with the parametrized status."""
    raise error

  with pytest.raises(expected) as info:
    await call()
  assert type(info.value) is expected
  assert info.value.__cause__ is error

@pytest.mark.asyncio
@pytest.mark.parametrize('error', [
  ProtocolError('boom'),
  StreamTerminatedError('GOAWAY'),
  ConnectionRefusedError('refused'),
  TimeoutError('timed out'),
])
async def test_wrap_exceptions_maps_every_transport_failure(error):
  """Every transport failure raises `NetworkError`, keeping the original as the cause."""

  @wrap_exceptions
  async def call():
    """Fail at the transport layer."""
    raise error

  with pytest.raises(NetworkError) as info:
    await call()
  assert info.value.__cause__ is error

@pytest.mark.asyncio
async def test_wrap_exceptions_returns_value():
  """A successful call's value passes through unchanged."""

  @wrap_exceptions
  async def call() -> int:
    """Succeed."""
    return 42

  assert await call() == 42


ROUTE = '/test.Feed/Watch'

class TextCodec(CodecBase):
  """Encode `str` messages as UTF-8, so tests need no protobuf types."""
  __content_subtype__ = 'proto'  # pyright: ignore[reportAssignmentType, reportIncompatibleMethodOverride]

  def encode(self, message, message_type):
    """Encode one message."""
    return message.encode()

  def decode(self, data, message_type):
    """Decode one message."""
    return data.decode()

class Feed:
  """Serve `ROUTE` with a test-provided handler."""

  def __init__(self, handle):
    """Store the stream handler."""
    self.handle = handle

  def __mapping__(self):
    """Expose the server-streaming route."""
    return {ROUTE: Handler(self.handle, Cardinality.UNARY_STREAM, str, str)}

@asynccontextmanager
async def serve(handle):
  """Run an in-process server and yield a channel connected to it."""
  server = Server([Feed(handle)], codec=TextCodec())
  with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0))
    await server.start(sock=sock)
    channel = Channel('127.0.0.1', sock.getsockname()[1], codec=TextCodec())
    try:
      yield channel
    finally:
      channel.close()
      server.close()
      await server.wait_closed()

def watch(channel, route=ROUTE, metadata=None):
  """Open the test stream."""
  return server_stream(channel, route, 'hello', str, metadata=metadata)

@pytest.mark.asyncio
async def test_server_stream_await_usage_yields_every_message_then_ends():
  """`await` connects; iteration yields every message and ends when the server does."""
  seen = []

  async def handle(stream: Stream):
    """Echo the request and a header, then end the call."""
    assert stream.metadata is not None
    seen.append((await stream.recv_message(), stream.metadata.get('x-label')))
    for message in ['a', 'b']:
      await stream.send_message(message)

  async with serve(handle) as channel:
    stream = await watch(channel, metadata={'x-label': 'feed'})
    assert stream.reply is None
    assert [m async for m in stream] == ['a', 'b']
    await stream.unsubscribe()
    await stream.unsubscribe()
  assert seen == [('hello', 'feed')]

@pytest.mark.asyncio
async def test_server_stream_async_with_cleans_up_after_early_break():
  """Leaving `async with` after a break cancels the call on the server."""
  cancelled = asyncio.Event()

  async def handle(stream: Stream):
    """Send one message, then wait for the client to go away."""
    await stream.recv_message()
    await stream.send_message('a')
    try:
      await asyncio.sleep(10)
    except asyncio.CancelledError:
      cancelled.set()
      raise

  async with serve(handle) as channel:
    async with watch(channel) as stream:
      async for message in stream:
        assert message == 'a'
        break
    await asyncio.wait_for(cancelled.wait(), timeout=5)

@pytest.mark.asyncio
async def test_server_stream_unsubscribe_ends_a_running_iteration_quietly():
  """Unsubscribing from another task ends the iteration without an error."""

  async def handle(stream: Stream):
    """Send one message, then stay open."""
    await stream.recv_message()
    await stream.send_message('a')
    await asyncio.sleep(10)

  async with serve(handle) as channel:
    stream = await watch(channel)

    async def consume():
      """Collect messages until the stream ends."""
      return [m async for m in stream]

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.2)
    await stream.unsubscribe()
    assert await asyncio.wait_for(task, timeout=5) == ['a']

@pytest.mark.asyncio
@pytest.mark.parametrize('status, expected', [
  (Status.INVALID_ARGUMENT, BadRequest),
  (Status.UNAVAILABLE, NetworkError),
  (Status.INTERNAL, ApiError),
])
async def test_server_stream_rejection_raises_on_entry(status, expected):
  """A call the server rejects before streaming raises on `await` and `async with`."""

  async def handle(stream: Stream):
    """Reject the call outright."""
    await stream.recv_message()
    raise GRPCError(status, 'rejected')

  async with serve(handle) as channel:
    with pytest.raises(expected) as info:
      await watch(channel)
    assert isinstance(info.value.__cause__, GRPCError)
    with pytest.raises(expected):
      async with watch(channel):
        pass

@pytest.mark.asyncio
async def test_server_stream_unknown_method_raises_on_entry():
  """An unimplemented method raises `ApiError` on entry."""

  async def handle(stream: Stream):
    """Never reached."""

  async with serve(handle) as channel:
    with pytest.raises(ApiError):
      await watch(channel, route='/test.Feed/Missing')

@pytest.mark.asyncio
async def test_server_stream_unreachable_server_raises_network_error():
  """A refused connection raises `NetworkError` on entry."""
  with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
  channel = Channel('127.0.0.1', port, codec=TextCodec())
  try:
    with pytest.raises(NetworkError):
      await watch(channel)
  finally:
    channel.close()

@pytest.mark.asyncio
async def test_server_stream_maps_errors_mid_stream():
  """A failure after some messages raises its mapped error from the iteration."""

  async def handle(stream: Stream):
    """Send one message, then fail."""
    await stream.recv_message()
    await stream.send_message('a')
    raise GRPCError(Status.RESOURCE_EXHAUSTED, 'slow down')

  async with serve(handle) as channel:
    received = []
    async with watch(channel) as stream:
      with pytest.raises(RateLimited):
        async for message in stream:
          received.append(message)
    assert received == ['a']
