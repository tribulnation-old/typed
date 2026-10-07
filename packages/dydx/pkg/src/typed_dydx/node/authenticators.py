"""dYdX API wallet (permissioned key) authenticator lookup."""

import base64
import json

from typed_core.exceptions import AuthError
from typing_extensions import Sequence

from typed_dydx.chain import Chain
from typed_dydx.protos.dydxprotocol import accountplus

def config_bytes(config: str | list[int]) -> bytes:
  """Decode a sub-authenticator config from its JSON encoding.

  Args:
    config: Base64 string or list of byte values.

  Returns:
    Raw config bytes.
  """
  return bytes(config) if isinstance(config, list) else base64.b64decode(config)

def verifies_key(authenticator_type: str, config: bytes, public_key: bytes) -> bool:
  """Return whether an authenticator tree verifies signatures from a key.

  Composite authenticators (`AllOf`, `AnyOf`) hold a JSON list of
  sub-authenticators whose configs are bytes, encoded as base64 (dYdX frontend)
  or as a list of integers (official Python client).

  Args:
    authenticator_type: Authenticator type.
    config: Authenticator config bytes.
    public_key: Compressed secp256k1 public key bytes.

  Returns:
    Whether any `SignatureVerification` node in the tree holds `public_key`.
  """
  if authenticator_type == 'SignatureVerification':
    return config == public_key
  if authenticator_type in ('AllOf', 'AnyOf'):
    return any(
      verifies_key(sub['type'], config_bytes(sub['config']), public_key)
      for sub in json.loads(config)
    )
  return False

def match_authenticator(
  authenticators: Sequence[accountplus.AccountAuthenticator], *, account: str, public_key: bytes,
) -> int:
  """Pick the authenticator through which a key signs for an account.

  Args:
    authenticators: Authenticators registered on `account`.
    account: Account address the API wallet trades for.
    public_key: Compressed secp256k1 public key of the API wallet.

  Returns:
    The matching authenticator ID. The newest one wins when several match.

  Raises:
    AuthError: Raised when no authenticator verifies the key.
  """
  ids = [
    authenticator.id
    for authenticator in authenticators
    if verifies_key(authenticator.type, authenticator.config, public_key)
  ]
  if not ids:
    raise AuthError(f'The private key is not an API wallet of {account}')
  return max(ids)

async def find_authenticator_id(chain: Chain, *, account: str, public_key: bytes) -> int:
  """Find the authenticator through which a key signs for an account.

  Args:
    chain: Chain client used for the `accountplus` query.
    account: Account address the API wallet trades for.
    public_key: Compressed secp256k1 public key of the API wallet.

  Returns:
    The matching authenticator ID. The newest one wins when several match.

  Raises:
    AuthError: Raised when no authenticator of `account` verifies the key.

  References:
    - [Permissioned keys](https://docs.dydx.xyz/interaction/permissioned-keys)
  """
  response = await accountplus.QueryStub(chain.grpc_client.channel).get_authenticators(
    accountplus.GetAuthenticatorsRequest(account=account)
  )
  return match_authenticator(
    response.account_authenticators, account=account, public_key=public_key
  )
