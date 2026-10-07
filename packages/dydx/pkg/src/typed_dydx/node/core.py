"""Core dYdX node client composition."""

import os
from dataclasses import dataclass, field
from types import TracebackType

from typed_core.exceptions import AuthError
from typing_extensions import TYPE_CHECKING, Self, Sequence

from typed_dydx.chain import Chain
from typed_dydx.chain.comet.core import CometOptions
from typed_dydx.chain.core import GrpcOptions
from typed_dydx.indexer.schemas import PerpetualMarket
from typed_dydx.node.constants import (
  DYDX_MAINNET_CHAIN_ID,
  DYDX_MAINNET_USDC_DENOM,
  DYDX_TESTNET_CHAIN_ID,
  DYDX_TESTNET_USDC_DENOM,
)
from typed_dydx.node.context import NodeContext, WalletState
from typed_dydx.node.market import Market
from typed_dydx.node.orders.place_order import PlacedOrder, PlacedOrders
from typed_dydx.node.orders.types import OrderParams, OrderPlacement
from typed_dydx.node.wallet import Wallet
from typed_dydx.protos.cosmos.tx import v1beta1 as tx_proto
from typed_dydx.protos.dydxprotocol import clob

if TYPE_CHECKING:
  from typed_dydx.node.orders import Orders
  from typed_dydx.node.public import Public
  from typed_dydx.node.tx import Tx

def node_wallet(
  *,
  mnemonic: str | None = None,
  private_key: str | None = None,
  address: str | None = None,
  public: bool = False,
  mnemonic_env: str = 'DYDX_MNEMONIC',
  private_key_env: str = 'DYDX_PRIVATE_KEY',
) -> Wallet | None:
  """Resolve explicit, environment, or public node wallet configuration.

  Explicit arguments take precedence over environment variables.

  Args:
    mnemonic: Wallet mnemonic passed directly by the caller.
    private_key: Hex private key passed directly by the caller, instead of `mnemonic`.
    address: Account an API wallet key trades for. Defaults to the key's own address.
    public: Allow read-only usage when no wallet is configured.
    mnemonic_env: Environment variable used as the mnemonic fallback.
    private_key_env: Environment variable used as the private key fallback.

  Returns:
    A wallet when credentials are available, otherwise `None` for public mode.

  Raises:
    ValueError: Raised when both `mnemonic` and `private_key` are passed.
    AuthError: Raised when both environment variables are set, or when no wallet is
      available and public mode is disabled.
  """
  if mnemonic is not None and private_key is not None:
    raise ValueError('Pass either `mnemonic` or `private_key`, not both.')
  if mnemonic is None and private_key is None:
    mnemonic = os.environ.get(mnemonic_env)
    private_key = os.environ.get(private_key_env)
    if mnemonic is not None and private_key is not None:
      raise AuthError(f'Both `{mnemonic_env}` and `{private_key_env}` are set; unset one.')
  if mnemonic is not None:
    return Wallet.from_mnemonic(mnemonic, account=address)
  if private_key is not None:
    return Wallet.from_hex(private_key, account=address)
  if public:
    return None
  raise AuthError(
    f'Provide `mnemonic` or `private_key`, set `{mnemonic_env}` or `{private_key_env}`, '
    'or pass `public=True` for read-only dYdX node usage.'
  )

@dataclass
class Node:
  """dYdX node client for wallet signing and trading."""

  chain: Chain
  """Chain transport used for gRPC queries, Comet lookups, and broadcasts."""
  chain_id: str = DYDX_MAINNET_CHAIN_ID
  """Cosmos chain ID included in direct-sign transaction sign docs."""
  usdc_denom: str = DYDX_MAINNET_USDC_DENOM
  """USDC denomination used when building transaction fees."""
  memo: str = ''
  """Default transaction memo used when signing unless a call overrides it."""
  wallet_state: WalletState = field(default_factory=WalletState)
  """Mutable wallet account metadata shared by transaction helpers."""

  @property
  def wallet(self) -> Wallet | None:
    """Return the current wallet snapshot.

    Returns:
      Current wallet and account metadata, or `None` for public-only nodes.
    """
    return self.wallet_state.wallet

  @property
  def context(self) -> NodeContext:
    """Return the shared node context.

    Returns:
      Node context carrying chain, signing configuration, and wallet state.
    """
    return NodeContext(
      chain=self.chain,
      chain_id=self.chain_id,
      usdc_denom=self.usdc_denom,
      wallet_state=self.wallet_state,
      memo=self.memo,
    )

  @property
  def public(self) -> 'Public':
    """Return read-only node query helpers.

    Returns:
      Public node query helper bound to the shared chain client.
    """
    from typed_dydx.node.public import Public
    return Public(self.chain)

  @property
  def tx(self) -> 'Tx':
    """Return transaction helpers.

    Returns:
      Transaction helper bound to the current node context.
    """
    from typed_dydx.node.tx import Tx
    return Tx(self.context)

  @property
  def orders(self) -> 'Orders':
    """Return order helpers.

    Returns:
      Order helper bound to the current node context and transaction helper.
    """
    from typed_dydx.node.orders import Orders
    return Orders(self.context, self.tx)

  async def place_order(
    self,
    market: Market | PerpetualMarket,
    *,
    order: OrderParams,
    subaccount: int = 0,
    mode: tx_proto.BroadcastMode = tx_proto.BroadcastMode(2),
    simulate: bool = False,
  ) -> PlacedOrder:
    """Build, sign, and broadcast a long-term dYdX order.

    Args:
      market: Market metadata used to convert price and size into protocol
        subticks and quantums. Accepts either a prepared `Market` or the
        indexer `PerpetualMarket` payload.
      order: Order parameters for the CLOB message. dYdX only allows batched
        placement for stateful long-term or conditional orders.
      subaccount: dYdX subaccount number owned by the wallet.
      mode: Cosmos broadcast mode.
      simulate: Whether to simulate the transaction instead of broadcasting it.

    Returns:
      The broadcast response and protocol order that was signed.
    """
    return await self.orders.place_order(
      market,
      order=order,
      subaccount=subaccount,
      mode=mode,
      simulate=simulate,
    )

  async def place_orders(
    self,
    orders: Sequence[OrderPlacement],
    *,
    mode: tx_proto.BroadcastMode = tx_proto.BroadcastMode(2),
    simulate: bool = False,
  ) -> PlacedOrders:
    """Build, sign, and broadcast multiple stateful dYdX orders.

    dYdX rejects transactions containing multiple short-term `MsgPlaceOrder`
    messages. This helper only accepts long-term or conditional orders, which
    are stateful and can be submitted together in one transaction.

    Args:
      orders: Order placement items. Each item carries its market metadata,
        order parameters, and optional subaccount number.
      mode: Cosmos broadcast mode.
      simulate: Whether to simulate the transaction before broadcasting.

    Returns:
      The broadcast response and protocol orders that were signed.
    """
    return await self.orders.place_orders(
      orders,
      mode=mode,
      simulate=simulate,
    )

  async def cancel_order(
    self,
    order_id: clob.OrderId,
    *,
    good_til_block: int | None = None,
    good_til_block_time: int | None = None,
    mode: tx_proto.BroadcastMode = tx_proto.BroadcastMode.SYNC,
    simulate: bool = False,
  ) -> tx_proto.BroadcastTxResponse:
    """Build, sign, and broadcast an order cancellation.

    Args:
      order_id: Protocol order identifier to cancel.
      good_til_block: Optional short-term cancellation expiry block.
      good_til_block_time: Optional stateful cancellation expiry timestamp.
      mode: Cosmos broadcast mode.
      simulate: Whether to simulate the transaction before broadcasting.

    Returns:
      The transaction broadcast response.
    """
    return await self.orders.cancel_order(
      order_id,
      good_til_block=good_til_block,
      good_til_block_time=good_til_block_time,
      mode=mode,
      simulate=simulate,
    )

  async def batch_cancel_orders(
    self,
    order_ids: Sequence[clob.OrderId],
    *,
    good_til_block: int | None = None,
    mode: tx_proto.BroadcastMode = tx_proto.BroadcastMode(2),
    simulate: bool = False,
  ) -> tx_proto.BroadcastTxResponse:
    """Build, sign, and broadcast short-term order cancellations.

    Args:
      order_ids: Short-term order identifiers to cancel in one CLOB message.
      good_til_block: Optional batch cancellation expiry block.
      mode: Cosmos broadcast mode.
      simulate: Whether to simulate the transaction before broadcasting.

    Returns:
      The transaction broadcast response.
    """
    return await self.orders.batch_cancel_orders(
      order_ids,
      good_til_block=good_til_block,
      mode=mode,
      simulate=simulate,
    )

  @classmethod
  def new(
    cls,
    *,
    chain: Chain,
    chain_id: str = DYDX_MAINNET_CHAIN_ID,
    usdc_denom: str = DYDX_MAINNET_USDC_DENOM,
    mnemonic: str | None = None,
    private_key: str | None = None,
    address: str | None = None,
    public: bool = False,
    mnemonic_env: str = 'DYDX_MNEMONIC',
    private_key_env: str = 'DYDX_PRIVATE_KEY',
    memo: str = '',
  ) -> Self:
    """Create a node client from an existing chain client.

    Args:
      chain: Chain client used for gRPC queries, Comet reads, and broadcasts.
      chain_id: Cosmos chain ID included in direct-sign transaction sign docs.
      usdc_denom: USDC denomination used when building transaction fees.
      mnemonic: Optional wallet mnemonic.
      private_key: Optional hex private key, instead of `mnemonic`.
      address: Account an API wallet key trades for. Defaults to the key's own address.
      public: Allow construction without a wallet for read-only node helpers.
      mnemonic_env: Mnemonic environment variable consulted when neither `mnemonic` nor
        `private_key` is passed.
      private_key_env: Private key environment variable consulted when neither `mnemonic`
        nor `private_key` is passed.
      memo: Default transaction memo used when signing.

    Returns:
      A node client with wallet state attached when credentials are available.
    """
    return cls(
      chain=chain,
      chain_id=chain_id,
      usdc_denom=usdc_denom,
      memo=memo,
      wallet_state=WalletState(
        wallet=node_wallet(
          mnemonic=mnemonic,
          private_key=private_key,
          address=address,
          public=public,
          mnemonic_env=mnemonic_env,
          private_key_env=private_key_env,
        ),
      ),
    )

  @classmethod
  def oegs(
    cls,
    mnemonic: str | None = None,
    *,
    private_key: str | None = None,
    address: str | None = None,
    modules: GrpcOptions | None = None,
    comet: CometOptions | None = None,
    public: bool = False,
    memo: str = '',
  ) -> Self:
    """Create a mainnet node client for OEGS endpoints.

    Args:
      mnemonic: Optional wallet mnemonic. Falls back to `DYDX_MNEMONIC`.
      private_key: Optional hex private key, instead of `mnemonic`. Falls back to
        `DYDX_PRIVATE_KEY`.
      address: Account an API wallet key trades for. Defaults to the key's own address.
      modules: Optional gRPC transport overrides.
      comet: Optional Comet HTTP transport overrides.
      public: Allow read-only construction without a wallet.
      memo: Default transaction memo used when signing.

    Returns:
      A mainnet node client backed by OEGS chain endpoints.
    """
    return cls.new(
      chain=Chain.oegs(modules=modules, comet=comet),
      mnemonic=mnemonic,
      private_key=private_key,
      address=address,
      public=public,
      memo=memo,
    )

  @classmethod
  def mainnet(
    cls,
    mnemonic: str | None = None,
    *,
    private_key: str | None = None,
    address: str | None = None,
    modules: GrpcOptions | None = None,
    comet: CometOptions | None = None,
    public: bool = False,
    memo: str = '',
  ) -> Self:
    """Create the default mainnet node client.

    Args:
      mnemonic: Optional wallet mnemonic. Falls back to `DYDX_MNEMONIC`.
      private_key: Optional hex private key, instead of `mnemonic`. Falls back to
        `DYDX_PRIVATE_KEY`.
      address: Account an API wallet key trades for. Defaults to the key's own address.
      modules: Optional gRPC transport overrides.
      comet: Optional Comet HTTP transport overrides.
      public: Allow read-only construction without a wallet.
      memo: Default transaction memo used when signing.

    Returns:
      A mainnet node client using the default provider.
    """
    return cls.oegs(
      modules=modules,
      comet=comet,
      mnemonic=mnemonic,
      private_key=private_key,
      address=address,
      public=public,
      memo=memo,
    )

  @classmethod
  def polkachu(
    cls,
    mnemonic: str | None = None,
    *,
    private_key: str | None = None,
    address: str | None = None,
    modules: GrpcOptions | None = None,
    comet: CometOptions | None = None,
    public: bool = False,
    memo: str = '',
  ) -> Self:
    """Create a mainnet node client for Polkachu endpoints.

    Args:
      mnemonic: Optional wallet mnemonic. Falls back to `DYDX_MNEMONIC`.
      private_key: Optional hex private key, instead of `mnemonic`. Falls back to
        `DYDX_PRIVATE_KEY`.
      address: Account an API wallet key trades for. Defaults to the key's own address.
      modules: Optional gRPC transport overrides.
      comet: Optional Comet HTTP transport overrides.
      public: Allow read-only construction without a wallet.
      memo: Default transaction memo used when signing.

    Returns:
      A mainnet node client backed by Polkachu chain endpoints.
    """
    return cls.new(
      chain=Chain.polkachu(modules=modules, comet=comet),
      mnemonic=mnemonic,
      private_key=private_key,
      address=address,
      public=public,
      memo=memo,
    )

  @classmethod
  def kingnodes(
    cls,
    mnemonic: str | None = None,
    *,
    private_key: str | None = None,
    address: str | None = None,
    modules: GrpcOptions | None = None,
    comet: CometOptions | None = None,
    public: bool = False,
    memo: str = '',
  ) -> Self:
    """Create a mainnet node client for KingNodes endpoints.

    Args:
      mnemonic: Optional wallet mnemonic. Falls back to `DYDX_MNEMONIC`.
      private_key: Optional hex private key, instead of `mnemonic`. Falls back to
        `DYDX_PRIVATE_KEY`.
      address: Account an API wallet key trades for. Defaults to the key's own address.
      modules: Optional gRPC transport overrides.
      comet: Optional Comet HTTP transport overrides.
      public: Allow read-only construction without a wallet.
      memo: Default transaction memo used when signing.

    Returns:
      A mainnet node client backed by KingNodes chain endpoints.
    """
    return cls.new(
      chain=Chain.kingnodes(modules=modules, comet=comet),
      mnemonic=mnemonic,
      private_key=private_key,
      address=address,
      public=public,
      memo=memo,
    )

  @classmethod
  def enigma(
    cls,
    mnemonic: str | None = None,
    *,
    private_key: str | None = None,
    address: str | None = None,
    modules: GrpcOptions | None = None,
    comet: CometOptions | None = None,
    public: bool = False,
    memo: str = '',
  ) -> Self:
    """Create a mainnet node client for Enigma endpoints.

    Args:
      mnemonic: Optional wallet mnemonic. Falls back to `DYDX_MNEMONIC`.
      private_key: Optional hex private key, instead of `mnemonic`. Falls back to
        `DYDX_PRIVATE_KEY`.
      address: Account an API wallet key trades for. Defaults to the key's own address.
      modules: Optional gRPC transport overrides.
      comet: Optional Comet HTTP transport overrides.
      public: Allow read-only construction without a wallet.
      memo: Default transaction memo used when signing.

    Returns:
      A mainnet node client backed by Enigma chain endpoints.
    """
    return cls.new(
      chain=Chain.enigma(modules=modules, comet=comet),
      mnemonic=mnemonic,
      private_key=private_key,
      address=address,
      public=public,
      memo=memo,
    )

  @classmethod
  def polkachu_archive(
    cls,
    mnemonic: str | None = None,
    *,
    private_key: str | None = None,
    address: str | None = None,
    modules: GrpcOptions | None = None,
    comet: CometOptions | None = None,
    public: bool = False,
    memo: str = '',
  ) -> Self:
    """Create a mainnet node client for Polkachu archive endpoints.

    Args:
      mnemonic: Optional wallet mnemonic. Falls back to `DYDX_MNEMONIC`.
      private_key: Optional hex private key, instead of `mnemonic`. Falls back to
        `DYDX_PRIVATE_KEY`.
      address: Account an API wallet key trades for. Defaults to the key's own address.
      modules: Optional gRPC transport overrides.
      comet: Optional Comet HTTP transport overrides.
      public: Allow read-only construction without a wallet.
      memo: Default transaction memo used when signing.

    Returns:
      A mainnet node client backed by Polkachu archive endpoints.
    """
    return cls.new(
      chain=Chain.polkachu_archive(modules=modules, comet=comet),
      mnemonic=mnemonic,
      private_key=private_key,
      address=address,
      public=public,
      memo=memo,
    )

  @classmethod
  def kingnodes_archive(
    cls,
    mnemonic: str | None = None,
    *,
    private_key: str | None = None,
    address: str | None = None,
    modules: GrpcOptions | None = None,
    comet: CometOptions | None = None,
    public: bool = False,
    memo: str = '',
  ) -> Self:
    """Create a mainnet node client for KingNodes archive endpoints.

    Args:
      mnemonic: Optional wallet mnemonic. Falls back to `DYDX_MNEMONIC`.
      private_key: Optional hex private key, instead of `mnemonic`. Falls back to
        `DYDX_PRIVATE_KEY`.
      address: Account an API wallet key trades for. Defaults to the key's own address.
      modules: Optional gRPC transport overrides.
      comet: Optional Comet HTTP transport overrides.
      public: Allow read-only construction without a wallet.
      memo: Default transaction memo used when signing.

    Returns:
      A mainnet node client backed by KingNodes archive endpoints.
    """
    return cls.new(
      chain=Chain.kingnodes_archive(modules=modules, comet=comet),
      mnemonic=mnemonic,
      private_key=private_key,
      address=address,
      public=public,
      memo=memo,
    )

  @classmethod
  def enigma_archive(
    cls,
    mnemonic: str | None = None,
    *,
    private_key: str | None = None,
    address: str | None = None,
    modules: GrpcOptions | None = None,
    comet: CometOptions | None = None,
    public: bool = False,
    memo: str = '',
  ) -> Self:
    """Create a mainnet node client for Enigma archive endpoints.

    Args:
      mnemonic: Optional wallet mnemonic. Falls back to `DYDX_MNEMONIC`.
      private_key: Optional hex private key, instead of `mnemonic`. Falls back to
        `DYDX_PRIVATE_KEY`.
      address: Account an API wallet key trades for. Defaults to the key's own address.
      modules: Optional gRPC transport overrides.
      comet: Optional Comet HTTP transport overrides.
      public: Allow read-only construction without a wallet.
      memo: Default transaction memo used when signing.

    Returns:
      A mainnet node client backed by Enigma archive endpoints.
    """
    return cls.new(
      chain=Chain.enigma_archive(modules=modules, comet=comet),
      mnemonic=mnemonic,
      private_key=private_key,
      address=address,
      public=public,
      memo=memo,
    )

  @classmethod
  def testnet_oegs(
    cls,
    mnemonic: str | None = None,
    *,
    private_key: str | None = None,
    address: str | None = None,
    modules: GrpcOptions | None = None,
    comet: CometOptions | None = None,
    public: bool = False,
    memo: str = '',
  ) -> Self:
    """Create a testnet node client for OEGS endpoints.

    Args:
      mnemonic: Optional wallet mnemonic. Falls back to `DYDX_TESTNET_MNEMONIC`.
      private_key: Optional hex private key, instead of `mnemonic`. Falls back to
        `DYDX_TESTNET_PRIVATE_KEY`.
      address: Account an API wallet key trades for. Defaults to the key's own address.
      modules: Optional gRPC transport overrides.
      comet: Optional Comet HTTP transport overrides.
      public: Allow read-only construction without a wallet.
      memo: Default transaction memo used when signing.

    Returns:
      A testnet node client backed by OEGS chain endpoints.
    """
    return cls.new(
      chain=Chain.testnet_oegs(modules=modules, comet=comet),
      chain_id=DYDX_TESTNET_CHAIN_ID,
      usdc_denom=DYDX_TESTNET_USDC_DENOM,
      mnemonic=mnemonic,
      private_key=private_key,
      address=address,
      public=public,
      mnemonic_env='DYDX_TESTNET_MNEMONIC',
      private_key_env='DYDX_TESTNET_PRIVATE_KEY',
      memo=memo,
    )

  @classmethod
  def testnet(
    cls,
    mnemonic: str | None = None,
    *,
    private_key: str | None = None,
    address: str | None = None,
    modules: GrpcOptions | None = None,
    comet: CometOptions | None = None,
    public: bool = False,
    memo: str = '',
  ) -> Self:
    """Create the default testnet node client.

    Args:
      mnemonic: Optional wallet mnemonic. Falls back to `DYDX_TESTNET_MNEMONIC`.
      private_key: Optional hex private key, instead of `mnemonic`. Falls back to
        `DYDX_TESTNET_PRIVATE_KEY`.
      address: Account an API wallet key trades for. Defaults to the key's own address.
      modules: Optional gRPC transport overrides.
      comet: Optional Comet HTTP transport overrides.
      public: Allow read-only construction without a wallet.
      memo: Default transaction memo used when signing.

    Returns:
      A testnet node client using the default provider.
    """
    return cls.testnet_kingnodes(
      mnemonic,
      private_key=private_key,
      address=address,
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
    address: str | None = None,
    modules: GrpcOptions | None = None,
    comet: CometOptions | None = None,
    public: bool = False,
    memo: str = '',
  ) -> Self:
    """Create a testnet node client for KingNodes endpoints.

    Args:
      mnemonic: Optional wallet mnemonic. Falls back to `DYDX_TESTNET_MNEMONIC`.
      private_key: Optional hex private key, instead of `mnemonic`. Falls back to
        `DYDX_TESTNET_PRIVATE_KEY`.
      address: Account an API wallet key trades for. Defaults to the key's own address.
      modules: Optional gRPC transport overrides.
      comet: Optional Comet HTTP transport overrides.
      public: Allow read-only construction without a wallet.
      memo: Default transaction memo used when signing.

    Returns:
      A testnet node client backed by KingNodes chain endpoints.
    """
    return cls.new(
      chain=Chain.testnet_kingnodes(modules=modules, comet=comet),
      chain_id=DYDX_TESTNET_CHAIN_ID,
      usdc_denom=DYDX_TESTNET_USDC_DENOM,
      mnemonic=mnemonic,
      private_key=private_key,
      address=address,
      public=public,
      mnemonic_env='DYDX_TESTNET_MNEMONIC',
      private_key_env='DYDX_TESTNET_PRIVATE_KEY',
      memo=memo,
    )

  @classmethod
  def testnet_polkachu(
    cls,
    mnemonic: str | None = None,
    *,
    private_key: str | None = None,
    address: str | None = None,
    modules: GrpcOptions | None = None,
    comet: CometOptions | None = None,
    public: bool = False,
    memo: str = '',
  ) -> Self:
    """Create a testnet node client for Polkachu endpoints.

    Args:
      mnemonic: Optional wallet mnemonic. Falls back to `DYDX_TESTNET_MNEMONIC`.
      private_key: Optional hex private key, instead of `mnemonic`. Falls back to
        `DYDX_TESTNET_PRIVATE_KEY`.
      address: Account an API wallet key trades for. Defaults to the key's own address.
      modules: Optional gRPC transport overrides.
      comet: Optional Comet HTTP transport overrides.
      public: Allow read-only construction without a wallet.
      memo: Default transaction memo used when signing.

    Returns:
      A testnet node client backed by Polkachu chain endpoints.
    """
    return cls.new(
      chain=Chain.testnet_polkachu(modules=modules, comet=comet),
      chain_id=DYDX_TESTNET_CHAIN_ID,
      usdc_denom=DYDX_TESTNET_USDC_DENOM,
      mnemonic=mnemonic,
      private_key=private_key,
      address=address,
      public=public,
      mnemonic_env='DYDX_TESTNET_MNEMONIC',
      private_key_env='DYDX_TESTNET_PRIVATE_KEY',
      memo=memo,
    )

  async def __aenter__(self) -> Self:
    """Enter the node client context and refresh wallet metadata.

    Returns:
      The entered node client. Wallet account metadata is loaded when a wallet
      is configured.
    """
    await self.chain.__aenter__()
    if self.wallet is not None:
      await self.refresh_wallet()
    return self

  async def __aexit__(
    self,
    exc_type: type[BaseException] | None,
    exc: BaseException | None,
    traceback: TracebackType | None,
  ):
    """Close the underlying chain client.

    Args:
      exc_type: Exception type from the async context manager, if any.
      exc: Exception instance from the async context manager, if any.
      traceback: Traceback from the async context manager, if any.
    """
    await self.chain.__aexit__(exc_type, exc, traceback)

  def require_wallet(self) -> Wallet:
    """Return the configured wallet or fail before network I/O.

    Returns:
      The configured wallet with any loaded account metadata.

    Raises:
      AuthError: Raised when this node was constructed for public-only usage.
    """
    return self.context.require_wallet()

  async def refresh_wallet(self):
    """Load account number and sequence for the configured wallet.

    Raises:
      AuthError: Raised when this node was constructed for public-only usage.
      BadRequest: Raised when the chain account response is missing or
        unsupported.
    """
    await self.context.refresh_wallet()

  async def close(self):
    """Close the underlying chain transports."""
    await self.chain.close()
