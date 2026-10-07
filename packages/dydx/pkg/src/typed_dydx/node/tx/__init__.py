"""dYdX node transaction helpers."""

from dataclasses import dataclass

import betterproto2
from typed_core.exceptions import ApiError
from typing_extensions import Sequence

from typed_dydx.protos.any import pack_cosmos_any
from typed_dydx.protos.cosmos.base import v1beta1 as coin_proto
from typed_dydx.protos.cosmos.tx import v1beta1 as tx_proto
from typed_dydx.protos.cosmos.tx.signing import v1beta1 as signing_proto
from typed_dydx.protos.dydxprotocol import accountplus
from typed_dydx.node.context import NodeContext
from typed_dydx.node.wallet import Wallet

INVALID_SEQUENCE_MARKERS = (
  'incorrect account sequence',
  'invalid sequence',
  'account sequence mismatch',
)

@dataclass
class TxOptions:
  """Explicit account metadata for transaction signing."""

  account_number: int
  """Cosmos account number included in the direct-sign document."""
  sequence: int
  """Cosmos account sequence used for this signed transaction."""

@dataclass
class Tx:
  """Transaction builder, signer, simulator, and broadcaster."""

  context: NodeContext
  """Shared node configuration and wallet sequence state."""

  def is_invalid_sequence_error(self, response: tx_proto.BroadcastTxResponse) -> bool:
    """Return whether a broadcast response indicates a stale wallet sequence.

    Args:
      response: Broadcast response returned by the chain transaction service.

    Returns:
      Whether the raw log contains a known account-sequence failure marker.
    """
    tx_response = response.tx_response
    if tx_response is None:
      return False
    log = (tx_response.raw_log or '').lower()
    return any(marker in log for marker in INVALID_SEQUENCE_MARKERS)

  def fee(
    self, *, gas_limit: int = 1_000_000, amount: int = 0, denom: str | None = None,
  ) -> tx_proto.Fee:
    """Build a Cosmos transaction fee.

    Args:
      gas_limit: Gas limit to include in the transaction fee.
      amount: Fee amount. Zero produces an empty fee coin list.
      denom: Fee denomination. Defaults to the node USDC denomination.

    Returns:
      Cosmos transaction fee message.
    """
    coins = []
    if amount:
      coins.append(coin_proto.Coin(denom=denom or self.context.usdc_denom, amount=str(amount)))
    return tx_proto.Fee(amount=coins, gas_limit=gas_limit)

  def signer_info(self, wallet: Wallet, *, sequence: int) -> tx_proto.SignerInfo:
    """Build direct-signing signer metadata.

    Args:
      wallet: Wallet whose public key signs the transaction.
      sequence: Cosmos account sequence to include in signer metadata.

    Returns:
      Cosmos signer info message for sign mode direct.
    """
    return tx_proto.SignerInfo(
      public_key=pack_cosmos_any(wallet.public_key),
      mode_info=tx_proto.ModeInfo(
        single=tx_proto.ModeInfoSingle(mode=signing_proto.SignMode.DIRECT),
      ),
      sequence=sequence,
    )

  def sign(
    self,
    messages: Sequence[betterproto2.Message],
    *,
    wallet: Wallet | None = None,
    fee: tx_proto.Fee | None = None,
    memo: str | None = None,
    options: TxOptions | None = None,
  ) -> tx_proto.TxRaw:
    """Build and sign a transaction from protocol messages.

    Args:
      messages: Protocol messages to pack into the transaction body.
      wallet: Optional wallet override. Defaults to the node wallet.
      fee: Optional explicit Cosmos transaction fee.
      memo: Optional transaction memo. Defaults to the node memo.
      options: Optional explicit account number and sequence.

    Returns:
      Signed raw transaction ready for simulation or broadcast.
    """
    signing_wallet = wallet or self.context.require_wallet()
    sequence = options.sequence if options else signing_wallet.sequence
    account_number = options.account_number if options else signing_wallet.account_number
    authenticator_id = self.context.wallet_state.authenticator_id if wallet is None else None
    body = tx_proto.TxBody(
      messages=[pack_cosmos_any(message) for message in messages],
      memo=self.context.memo if memo is None else memo,
      non_critical_extension_options=[
        pack_cosmos_any(accountplus.TxExtension(selected_authenticators=[authenticator_id]))
      ] if authenticator_id is not None else [],
    )
    auth_info = tx_proto.AuthInfo(
      signer_infos=[self.signer_info(signing_wallet, sequence=sequence)],
      fee=fee or self.fee(),
    )
    body_bytes = bytes(body)
    auth_info_bytes = bytes(auth_info)
    sign_doc = tx_proto.SignDoc(
      body_bytes=body_bytes,
      auth_info_bytes=auth_info_bytes,
      chain_id=self.context.chain_id,
      account_number=account_number,
    )
    signature = signing_wallet.sign(bytes(sign_doc))
    return tx_proto.TxRaw(
      body_bytes=body_bytes,
      auth_info_bytes=auth_info_bytes,
      signatures=[signature],
    )

  async def refresh_wallet(self):
    """Refresh the node wallet account number and sequence from chain state.

    Raises:
      AuthError: Raised when the node was constructed without a wallet.
      BadRequest: Raised when account metadata cannot be loaded from chain.
    """
    await self.context.refresh_wallet()

  async def simulate(self, tx: tx_proto.TxRaw) -> tx_proto.SimulateResponse:
    """Simulate signed transaction bytes.

    Args:
      tx: Signed raw transaction to simulate.

    Returns:
      Cosmos simulation response.
    """
    return await self.context.chain.tx.simulate(tx_bytes=bytes(tx))

  async def broadcast(
    self,
    tx: tx_proto.TxRaw,
    *,
    mode: tx_proto.BroadcastMode = tx_proto.BroadcastMode(2),
    raise_on_error: bool = True,
  ) -> tx_proto.BroadcastTxResponse:
    """Broadcast signed transaction bytes.

    Args:
      tx: Signed raw transaction to broadcast.
      mode: Cosmos broadcast mode.
      raise_on_error: Raise `ApiError` when the chain returns a nonzero code.

    Returns:
      Cosmos broadcast response.

    Raises:
      ApiError: Raised when no transaction response is returned or when the
        broadcast fails and `raise_on_error` is enabled.
    """
    response = await self.context.chain.tx.broadcast(bytes(tx), mode=mode)
    tx_response = response.tx_response
    if tx_response is None:
      raise ApiError('dYdX transaction broadcast did not return a tx response')
    if raise_on_error and tx_response.code != 0:
      raise ApiError(tx_response.raw_log or f'dYdX transaction failed with code {tx_response.code}')
    return response

  async def sign_and_broadcast(
    self,
    messages: Sequence[betterproto2.Message],
    *,
    fee: tx_proto.Fee | None = None,
    mode: tx_proto.BroadcastMode = tx_proto.BroadcastMode(2),
    memo: str | None = None,
    simulate: bool = False,
  ) -> tx_proto.BroadcastTxResponse:
    """Sign protocol messages and broadcast them.

    Args:
      messages: Protocol messages to pack into one transaction.
      fee: Optional explicit Cosmos transaction fee.
      mode: Cosmos broadcast mode.
      memo: Optional transaction memo. Defaults to the node memo.
      simulate: Whether to simulate the signed transaction before broadcast.

    Returns:
      Cosmos broadcast response.

    Raises:
      ApiError: Raised when broadcast fails after the sequence refresh retry.
    """
    async with self.context.wallet_state.lock:
      await self.context.ensure_wallet_loaded()
      response = await self.sign_and_broadcast_once(
        messages,
        fee=fee,
        mode=mode,
        memo=memo,
        simulate=simulate,
        raise_on_error=False,
      )
      tx_response = response.tx_response
      if tx_response is None:
        raise ApiError('dYdX transaction broadcast did not return a tx response')
      if tx_response.code == 0:
        self.context.wallet_state.increment_sequence()
        return response
      if self.is_invalid_sequence_error(response):
        await self.context.refresh_wallet()
        response = await self.sign_and_broadcast_once(
          messages,
          fee=fee,
          mode=mode,
          memo=memo,
          simulate=simulate,
          raise_on_error=False,
        )
        tx_response = response.tx_response
        if tx_response is None:
          raise ApiError('dYdX transaction broadcast did not return a tx response')
        if tx_response.code == 0:
          self.context.wallet_state.increment_sequence()
          return response
      raise ApiError(tx_response.raw_log or f'dYdX transaction failed with code {tx_response.code}')

  async def sign_and_broadcast_once(
    self,
    messages: Sequence[betterproto2.Message],
    *,
    fee: tx_proto.Fee | None = None,
    mode: tx_proto.BroadcastMode = tx_proto.BroadcastMode(2),
    memo: str | None = None,
    simulate: bool = False,
    raise_on_error: bool = True,
  ) -> tx_proto.BroadcastTxResponse:
    """Sign protocol messages once and broadcast the signed transaction.

    Args:
      messages: Protocol messages to pack into one transaction.
      fee: Optional explicit Cosmos transaction fee.
      mode: Cosmos broadcast mode.
      memo: Optional transaction memo. Defaults to the node memo.
      simulate: Whether to simulate the signed transaction before broadcast.
      raise_on_error: Raise `ApiError` on nonzero broadcast responses.

    Returns:
      Cosmos broadcast response.
    """
    signed = self.sign(messages, fee=fee, memo=memo)
    if simulate:
      await self.simulate(signed)
    return await self.broadcast(signed, mode=mode, raise_on_error=raise_on_error)
