"""dYdX client root composition (design §4/§5c, 2026-08-31 codegen mechanization):
`DydxBase`, the hand-written class holding every heterogeneous child's already-built
transport, wrapped by the generated `Dydx` composite (`main.py`).

`Dydx` composes three genuinely different children -- `indexer` (Indexer HTTP/WS),
`chain` (gRPC + Comet HTTP), `node` (wallet signing, order placement, broadcast) -- so its
own resolved `core` (`DydxBase`) is a *Base* holding one field per child's own transport,
built once in `.new()`/its alternate constructors, never a single shared transport every
child forwards unchanged (design §5c, kraken's `KrakenBase` being the section's own worked
example this mirrors). `indexer`/`chain` are reached through the generated `Dydx.indexer`/
`Dydx.chain` cached properties (design §5a: `IndexerBase.new`/`ChainBase.new` each take
this base's own `indexer_http_client`/`chain_grpc_client` as their first, `client`-named
parameter, with `indexer_ws_client`/`chain_comet_client` auto-forwarded by own-field-name
matching). `node` carries no `spec/endpoints/` tree of its own -- it stays a plain field
here, inherited directly onto `Dydx`, exactly as it always has been.

Every alternate constructor here (`.oegs()`, `.polkachu()`, `.testnet()`, ...) is moved
verbatim, consolidated, from what used to be duplicated three ways across `Dydx`/`Chain`/
`Node`'s own hand-written alternate constructors -- `ChainBase`'s own identically-shaped
methods (`typed_dydx.chain.core`) already do the gRPC-host/Comet-URL pairing; this module
adds the indexer URL pair (mainnet vs. testnet only -- the Indexer is dYdX's own single
service, not a multi-provider one) and the node wallet/chain-id/denom selection on top.
"""

from dataclasses import dataclass
from contextlib import AsyncExitStack
from types import TracebackType

from typing_extensions import TYPE_CHECKING, Self

from typed_core.grpc import GrpcClient

from typed_dydx.chain.comet.core import CometClient, CometOptions
from typed_dydx.chain.core import GrpcOptions
from typed_dydx.indexer.core import IndexerOptions
from typed_dydx.indexer.data.core import IndexerHttpClient
from typed_dydx.indexer.streams.core import IndexerWsClient
from typed_dydx.node.constants import (
  DYDX_MAINNET_CHAIN_ID,
  DYDX_MAINNET_USDC_DENOM,
  DYDX_TESTNET_CHAIN_ID,
  DYDX_TESTNET_USDC_DENOM,
)
from typed_dydx.node.core import Node

if TYPE_CHECKING:
  # Deferred at runtime (imported locally inside each method below instead): `Chain`/
  # `Indexer` are the *generated* composites, regenerated in the same codegen run this
  # module's own introspection (design §5a's `.new()` signature resolution) runs in --
  # importing them at module level would try to load whichever of the two files codegen
  # hasn't written yet this run. Imported here only so these forward-referenced
  # annotations resolve for the type checker.
  from typed_dydx.chain import Chain
  from typed_dydx.indexer import Indexer


@dataclass(kw_only=True)
class DydxBase:
  """dYdX client root: builds and owns every transport `Dydx`'s three children forward --
  Indexer HTTP/WS, Chain gRPC/Comet, and the fully-composed `node: Node` field itself."""

  indexer_http_client: IndexerHttpClient
  indexer_ws_client: IndexerWsClient
  chain_grpc_client: GrpcClient
  chain_comet_client: CometClient
  node: Node

  async def __aenter__(self) -> Self:
    """Open shared transports, closing them if initialization fails or is cancelled."""
    try:
      await self.indexer_http_client.__aenter__()
      await self.indexer_ws_client.__aenter__()
      await self.chain_grpc_client.__aenter__()
      await self.chain_comet_client.__aenter__()
      if self.node.wallet is not None:
        await self.node.refresh_wallet()
    except BaseException:
      await self.close()
      raise
    return self

  async def __aexit__(
    self,
    exc_type: type[BaseException] | None,
    exc: BaseException | None,
    traceback: TracebackType | None,
  ):
    """Close every shared transport for an async context."""
    await self.close()

  async def close(self):
    """Attempt every transport close, preserving cleanup after an earlier failure."""
    async with AsyncExitStack() as stack:
      stack.push_async_callback(self.chain_comet_client.close)
      stack.callback(self.chain_grpc_client.close)
      stack.push_async_exit(self.indexer_ws_client)
      stack.push_async_exit(self.indexer_http_client)

  @classmethod
  def new(
    cls,
    mnemonic: str | None = None,
    *,
    private_key: str | None = None,
    indexer: 'Indexer | None' = None,
    chain: 'Chain | None' = None,
    public: bool = False,
    chain_id: str = DYDX_MAINNET_CHAIN_ID,
    usdc_denom: str = DYDX_MAINNET_USDC_DENOM,
    memo: str = '',
    mnemonic_env: str = 'DYDX_MNEMONIC',
    private_key_env: str = 'DYDX_PRIVATE_KEY',
  ) -> Self:
    """Create a dYdX client base from existing indexer and chain clients.

    Args:
      mnemonic: Optional wallet mnemonic.
      private_key: Optional hex private key, instead of `mnemonic`.
      indexer: Indexer HTTP and WebSocket client. Defaults to the OEGS mainnet indexer.
      chain: Chain client shared by `chain` and `node`. Defaults to the OEGS mainnet chain.
      public: Allow construction without a wallet for read-only workflows.
      chain_id: Cosmos chain ID included in transaction sign docs.
      usdc_denom: USDC denomination used when building transaction fees.
      memo: Default transaction memo used when signing.
      mnemonic_env: Mnemonic environment variable consulted when neither `mnemonic` nor
        `private_key` is passed.
      private_key_env: Private key environment variable consulted when neither `mnemonic`
        nor `private_key` is passed.
    """
    from typed_dydx.chain import Chain
    from typed_dydx.indexer import Indexer

    indexer = indexer or Indexer.mainnet()
    chain = chain or Chain.oegs()
    node = Node.new(
      chain=chain,
      chain_id=chain_id,
      usdc_denom=usdc_denom,
      mnemonic=mnemonic,
      private_key=private_key,
      public=public,
      mnemonic_env=mnemonic_env,
      private_key_env=private_key_env,
      memo=memo,
    )
    return cls(
      indexer_http_client=indexer.http_client,
      indexer_ws_client=indexer.ws_client,
      chain_grpc_client=chain.grpc_client,
      chain_comet_client=chain.comet_client,
      node=node,
    )

  @classmethod
  def oegs(
    cls,
    mnemonic: str | None = None,
    *,
    private_key: str | None = None,
    indexer: 'IndexerOptions | None' = None,
    modules: GrpcOptions | None = None,
    comet: CometOptions | None = None,
    public: bool = False,
    memo: str = '',
  ) -> Self:
    """Create a mainnet dYdX client for OEGS chain endpoints.

    Args:
      mnemonic: Optional wallet mnemonic. Falls back to `DYDX_MNEMONIC`.
      private_key: Optional hex private key, instead of `mnemonic`. Falls back to
        `DYDX_PRIVATE_KEY`.
      indexer: Optional indexer transport overrides.
      modules: Optional gRPC transport overrides.
      comet: Optional Comet HTTP transport overrides.
      public: Allow read-only construction without a wallet.
      memo: Default transaction memo used when signing.
    """
    from typed_dydx.chain import Chain
    from typed_dydx.indexer import Indexer

    return cls.new(
      mnemonic,
      private_key=private_key,
      indexer=Indexer.mainnet(**(indexer or {})),
      chain=Chain.oegs(modules=modules, comet=comet),
      public=public,
      memo=memo,
    )

  @classmethod
  def mainnet(
    cls,
    mnemonic: str | None = None,
    *,
    private_key: str | None = None,
    indexer: 'IndexerOptions | None' = None,
    modules: GrpcOptions | None = None,
    comet: CometOptions | None = None,
    public: bool = False,
    memo: str = '',
  ) -> Self:
    """Create the default mainnet dYdX client (OEGS chain endpoints).

    Args:
      mnemonic: Optional wallet mnemonic. Falls back to `DYDX_MNEMONIC`.
      private_key: Optional hex private key, instead of `mnemonic`. Falls back to
        `DYDX_PRIVATE_KEY`.
      indexer: Optional indexer transport overrides.
      modules: Optional gRPC transport overrides.
      comet: Optional Comet HTTP transport overrides.
      public: Allow read-only construction without a wallet.
      memo: Default transaction memo used when signing.
    """
    return cls.oegs(
      mnemonic,
      private_key=private_key,
      indexer=indexer,
      modules=modules,
      comet=comet,
      public=public,
      memo=memo,
    )

  @classmethod
  def polkachu(
    cls,
    mnemonic: str | None = None,
    *,
    private_key: str | None = None,
    indexer: 'IndexerOptions | None' = None,
    modules: GrpcOptions | None = None,
    comet: CometOptions | None = None,
    public: bool = False,
    memo: str = '',
  ) -> Self:
    """Create a mainnet dYdX client for Polkachu chain endpoints."""
    from typed_dydx.chain import Chain
    from typed_dydx.indexer import Indexer

    return cls.new(
      mnemonic,
      private_key=private_key,
      indexer=Indexer.mainnet(**(indexer or {})),
      chain=Chain.polkachu(modules=modules, comet=comet),
      public=public,
      memo=memo,
    )

  @classmethod
  def kingnodes(
    cls,
    mnemonic: str | None = None,
    *,
    private_key: str | None = None,
    indexer: 'IndexerOptions | None' = None,
    modules: GrpcOptions | None = None,
    comet: CometOptions | None = None,
    public: bool = False,
    memo: str = '',
  ) -> Self:
    """Create a mainnet dYdX client for KingNodes chain endpoints."""
    from typed_dydx.chain import Chain
    from typed_dydx.indexer import Indexer

    return cls.new(
      mnemonic,
      private_key=private_key,
      indexer=Indexer.mainnet(**(indexer or {})),
      chain=Chain.kingnodes(modules=modules, comet=comet),
      public=public,
      memo=memo,
    )

  @classmethod
  def enigma(
    cls,
    mnemonic: str | None = None,
    *,
    private_key: str | None = None,
    indexer: 'IndexerOptions | None' = None,
    modules: GrpcOptions | None = None,
    comet: CometOptions | None = None,
    public: bool = False,
    memo: str = '',
  ) -> Self:
    """Create a mainnet dYdX client for Enigma chain endpoints."""
    from typed_dydx.chain import Chain
    from typed_dydx.indexer import Indexer

    return cls.new(
      mnemonic,
      private_key=private_key,
      indexer=Indexer.mainnet(**(indexer or {})),
      chain=Chain.enigma(modules=modules, comet=comet),
      public=public,
      memo=memo,
    )

  @classmethod
  def polkachu_archive(
    cls,
    mnemonic: str | None = None,
    *,
    private_key: str | None = None,
    indexer: 'IndexerOptions | None' = None,
    modules: GrpcOptions | None = None,
    comet: CometOptions | None = None,
    public: bool = False,
    memo: str = '',
  ) -> Self:
    """Create a mainnet dYdX client for Polkachu archive chain endpoints."""
    from typed_dydx.chain import Chain
    from typed_dydx.indexer import Indexer

    return cls.new(
      mnemonic,
      private_key=private_key,
      indexer=Indexer.mainnet(**(indexer or {})),
      chain=Chain.polkachu_archive(modules=modules, comet=comet),
      public=public,
      memo=memo,
    )

  @classmethod
  def kingnodes_archive(
    cls,
    mnemonic: str | None = None,
    *,
    private_key: str | None = None,
    indexer: 'IndexerOptions | None' = None,
    modules: GrpcOptions | None = None,
    comet: CometOptions | None = None,
    public: bool = False,
    memo: str = '',
  ) -> Self:
    """Create a mainnet dYdX client for KingNodes archive chain endpoints."""
    from typed_dydx.chain import Chain
    from typed_dydx.indexer import Indexer

    return cls.new(
      mnemonic,
      private_key=private_key,
      indexer=Indexer.mainnet(**(indexer or {})),
      chain=Chain.kingnodes_archive(modules=modules, comet=comet),
      public=public,
      memo=memo,
    )

  @classmethod
  def enigma_archive(
    cls,
    mnemonic: str | None = None,
    *,
    private_key: str | None = None,
    indexer: 'IndexerOptions | None' = None,
    modules: GrpcOptions | None = None,
    comet: CometOptions | None = None,
    public: bool = False,
    memo: str = '',
  ) -> Self:
    """Create a mainnet dYdX client for Enigma archive chain endpoints."""
    from typed_dydx.chain import Chain
    from typed_dydx.indexer import Indexer

    return cls.new(
      mnemonic,
      private_key=private_key,
      indexer=Indexer.mainnet(**(indexer or {})),
      chain=Chain.enigma_archive(modules=modules, comet=comet),
      public=public,
      memo=memo,
    )

  @classmethod
  def testnet_oegs(
    cls,
    mnemonic: str | None = None,
    *,
    private_key: str | None = None,
    indexer: 'IndexerOptions | None' = None,
    modules: GrpcOptions | None = None,
    comet: CometOptions | None = None,
    public: bool = False,
    memo: str = '',
  ) -> Self:
    """Create a testnet dYdX client for OEGS chain endpoints."""
    from typed_dydx.chain import Chain
    from typed_dydx.indexer import Indexer

    return cls.new(
      mnemonic,
      private_key=private_key,
      indexer=Indexer.testnet(**(indexer or {})),
      chain=Chain.testnet_oegs(modules=modules, comet=comet),
      public=public,
      chain_id=DYDX_TESTNET_CHAIN_ID,
      usdc_denom=DYDX_TESTNET_USDC_DENOM,
      memo=memo,
      mnemonic_env='DYDX_TESTNET_MNEMONIC',
      private_key_env='DYDX_TESTNET_PRIVATE_KEY',
    )

  @classmethod
  def testnet(
    cls,
    mnemonic: str | None = None,
    *,
    private_key: str | None = None,
    indexer: 'IndexerOptions | None' = None,
    modules: GrpcOptions | None = None,
    comet: CometOptions | None = None,
    public: bool = False,
    memo: str = '',
  ) -> Self:
    """Create the default testnet dYdX client (KingNodes chain endpoints)."""
    return cls.testnet_kingnodes(
      mnemonic,
      private_key=private_key,
      indexer=indexer,
      modules=modules,
      comet=comet,
      public=public,
      memo=memo,
    )

  @classmethod
  def testnet_kingnodes(
    cls,
    mnemonic: str | None = None,
    *,
    private_key: str | None = None,
    indexer: 'IndexerOptions | None' = None,
    modules: GrpcOptions | None = None,
    comet: CometOptions | None = None,
    public: bool = False,
    memo: str = '',
  ) -> Self:
    """Create a testnet dYdX client for KingNodes chain endpoints."""
    from typed_dydx.chain import Chain
    from typed_dydx.indexer import Indexer

    return cls.new(
      mnemonic,
      private_key=private_key,
      indexer=Indexer.testnet(**(indexer or {})),
      chain=Chain.testnet_kingnodes(modules=modules, comet=comet),
      public=public,
      chain_id=DYDX_TESTNET_CHAIN_ID,
      usdc_denom=DYDX_TESTNET_USDC_DENOM,
      memo=memo,
      mnemonic_env='DYDX_TESTNET_MNEMONIC',
      private_key_env='DYDX_TESTNET_PRIVATE_KEY',
    )

  @classmethod
  def testnet_polkachu(
    cls,
    mnemonic: str | None = None,
    *,
    private_key: str | None = None,
    indexer: 'IndexerOptions | None' = None,
    modules: GrpcOptions | None = None,
    comet: CometOptions | None = None,
    public: bool = False,
    memo: str = '',
  ) -> Self:
    """Create a testnet dYdX client for Polkachu chain endpoints."""
    from typed_dydx.chain import Chain
    from typed_dydx.indexer import Indexer

    return cls.new(
      mnemonic,
      private_key=private_key,
      indexer=Indexer.testnet(**(indexer or {})),
      chain=Chain.testnet_polkachu(modules=modules, comet=comet),
      public=public,
      chain_id=DYDX_TESTNET_CHAIN_ID,
      usdc_denom=DYDX_TESTNET_USDC_DENOM,
      memo=memo,
      mnemonic_env='DYDX_TESTNET_MNEMONIC',
      private_key_env='DYDX_TESTNET_PRIVATE_KEY',
    )
