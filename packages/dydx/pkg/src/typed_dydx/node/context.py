"""Shared dYdX node runtime context."""

import asyncio
from dataclasses import dataclass, field

from typed_core.exceptions import AuthError, BadRequest

from typed_dydx.chain import Chain
from typed_dydx.node.authenticators import find_authenticator_id
from typed_dydx.node.wallet import Wallet
from typed_dydx.protos.any import unpack_expected
from typed_dydx.protos.cosmos.auth import v1beta1 as auth_proto

@dataclass
class WalletState:
  """Mutable node wallet account snapshot."""

  wallet: Wallet | None = None
  """Wallet key material plus the latest account number and sequence."""
  account_loaded: bool = False
  """Whether account metadata has been loaded from chain for this wallet."""
  authenticator_id: int | None = None
  """Authenticator through which an API wallet signs, resolved on first account load."""
  lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
  """Per-wallet broadcast lock that preserves consecutive sequence usage."""

  def require(self) -> Wallet:
    """Return the current wallet or fail before network I/O.

    Returns:
      The configured wallet.

    Raises:
      AuthError: Raised when the node was constructed without a wallet.
    """
    if self.wallet is None:
      raise AuthError('A wallet is required for signed dYdX node operations')
    return self.wallet

  def update_account(self, *, account_number: int, sequence: int):
    """Update wallet account metadata while preserving key material.

    Args:
      account_number: Cosmos account number returned by auth account queries.
      sequence: Current Cosmos account sequence returned by auth account
        queries.
    """
    wallet = self.require()
    self.wallet = wallet.with_account(
      account_number=account_number,
      sequence=sequence,
    )
    self.account_loaded = True

  def increment_sequence(self):
    """Advance the current wallet sequence after an accepted transaction.

    Raises:
      AuthError: Raised when the node was constructed without a wallet.
    """
    wallet = self.require()
    self.wallet = wallet.with_account(
      account_number=wallet.account_number,
      sequence=wallet.sequence + 1,
    )

@dataclass
class NodeContext:
  """Shared dYdX node configuration and mutable state."""

  chain: Chain
  """Chain transport used by node helpers."""
  chain_id: str
  """Cosmos chain ID included in direct-sign transaction sign docs."""
  usdc_denom: str
  """USDC denomination used for default fee construction."""
  wallet_state: WalletState
  """Mutable wallet metadata shared across transaction helper instances."""
  memo: str = ''
  """Default transaction memo used when signing unless a call overrides it."""

  def require_wallet(self) -> Wallet:
    """Return the configured wallet or fail before network I/O.

    Returns:
      The configured wallet.

    Raises:
      AuthError: Raised when the node was constructed without a wallet.
    """
    return self.wallet_state.require()

  async def refresh_wallet(self):
    """Load account number and sequence for the configured wallet.

    For an API wallet, this also resolves the authenticator it signs through, once.

    Raises:
      AuthError: Raised when the node was constructed without a wallet.
      BadRequest: Raised when the auth query does not return a supported base
        account payload.
    """
    wallet = self.require_wallet()
    if wallet.is_api_wallet and self.wallet_state.authenticator_id is None:
      self.wallet_state.authenticator_id = await find_authenticator_id(
        self.chain, account=wallet.address, public_key=wallet.key_pair.public_key_bytes
      )
    response = await self.chain.auth.account(wallet.address)
    if response.account is None:
      raise BadRequest(f'No chain account found for {wallet.address}')
    try:
      account = unpack_expected(response.account, auth_proto.BaseAccount)
    except TypeError as exc:
      raise BadRequest(f'Unsupported account payload for {wallet.address}') from exc
    self.wallet_state.update_account(
      account_number=account.account_number,
      sequence=account.sequence,
    )

  async def ensure_wallet_loaded(self):
    """Load wallet account metadata once before signing.

    Raises:
      AuthError: Raised when the node was constructed without a wallet.
      BadRequest: Raised when account metadata cannot be loaded from chain.
    """
    self.require_wallet()
    if not self.wallet_state.account_loaded:
      await self.refresh_wallet()
