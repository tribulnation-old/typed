"""dYdX wallet derivation and signing helpers."""

import hashlib
from dataclasses import dataclass

import bech32
from bip_utils import Bip39SeedGenerator, Bip44, Bip44Coins
from coincurve import PrivateKey
from coincurve.utils import GROUP_ORDER_INT, int_to_bytes
from Crypto.Hash import RIPEMD160

from typed_dydx.protos.cosmos.crypto import secp256k1

def private_key_bytes_from_mnemonic(mnemonic: str) -> bytes:
  """Derive the default Cosmos private key from a mnemonic.

  Args:
    mnemonic: BIP-39 mnemonic phrase.

  Returns:
    Raw private key bytes for the default Cosmos derivation path.
  """
  seed = Bip39SeedGenerator(mnemonic).Generate()
  return (
    Bip44.FromSeed(seed, Bip44Coins.COSMOS)
    .DeriveDefaultPath()
    .PrivateKey()
    .Raw()
    .ToBytes()
  )

def canonize_signature(signature: bytes) -> bytes:
  """Return the canonical 64-byte secp256k1 signature.

  Args:
    signature: Recoverable secp256k1 signature bytes.

  Returns:
    Canonical `r || s` signature bytes.
  """
  r = int.from_bytes(signature[:32], 'big')
  s = int.from_bytes(signature[32:64], 'big')
  if s > GROUP_ORDER_INT // 2:
    s = GROUP_ORDER_INT - s
  return int_to_bytes(r).rjust(32, b'\x00') + int_to_bytes(s).rjust(32, b'\x00')

@dataclass
class KeyPair:
  """Secp256k1 key pair used for Cosmos direct signing."""

  key: PrivateKey
  """Private key used for signing."""

  @classmethod
  def from_mnemonic(cls, mnemonic: str) -> 'KeyPair':
    """Create a key pair from a mnemonic.

    Args:
      mnemonic: BIP-39 mnemonic phrase.

    Returns:
      Secp256k1 key pair for Cosmos signing.
    """
    return cls(PrivateKey(private_key_bytes_from_mnemonic(mnemonic)))

  @classmethod
  def from_hex(cls, value: str) -> 'KeyPair':
    """Create a key pair from a hex private key.

    Args:
      value: Hex-encoded private key, with or without a `0x` prefix.

    Returns:
      Secp256k1 key pair for Cosmos signing.
    """
    return cls(PrivateKey.from_hex(value.removeprefix('0x')))

  @property
  def public_key_bytes(self) -> bytes:
    """Return the compressed public key bytes.

    Returns:
      Compressed secp256k1 public key bytes.
    """
    return self.key.public_key.format(compressed=True)

  def sign(self, message: bytes) -> bytes:
    """Sign bytes with canonical secp256k1 output.

    Args:
      message: Bytes to sign.

    Returns:
      Canonical signature bytes.
    """
    return canonize_signature(self.key.sign_recoverable(message))

@dataclass
class Wallet:
  """dYdX wallet plus account metadata required for signing."""

  key_pair: KeyPair
  """Secp256k1 key pair used for signing."""
  account_number: int = 0
  """Cosmos account number used in direct-sign documents."""
  sequence: int = 0
  """Cosmos account sequence used by the next signed transaction."""

  @classmethod
  def from_mnemonic(
    cls, mnemonic: str, *, account_number: int = 0, sequence: int = 0,
  ) -> 'Wallet':
    """Create a wallet from a mnemonic.

    Args:
      mnemonic: BIP-39 mnemonic phrase.
      account_number: Cosmos account number to attach to the wallet.
      sequence: Cosmos account sequence to attach to the wallet.

    Returns:
      Wallet with key material and account metadata.
    """
    return cls(
      key_pair=KeyPair.from_mnemonic(mnemonic),
      account_number=account_number,
      sequence=sequence,
    )

  @classmethod
  def from_hex(cls, value: str, *, account_number: int = 0, sequence: int = 0) -> 'Wallet':
    """Create a wallet from a hex private key.

    Args:
      value: Hex-encoded private key, with or without a `0x` prefix.
      account_number: Cosmos account number to attach to the wallet.
      sequence: Cosmos account sequence to attach to the wallet.

    Returns:
      Wallet with key material and account metadata.
    """
    return cls(
      key_pair=KeyPair.from_hex(value),
      account_number=account_number,
      sequence=sequence,
    )

  @property
  def public_key(self) -> secp256k1.PubKey:
    """Return the Cosmos secp256k1 public key message.

    Returns:
      Cosmos secp256k1 public key protobuf message.
    """
    return secp256k1.PubKey(key=self.key_pair.public_key_bytes)

  @property
  def address(self) -> str:
    """Return the dYdX bech32 account address.

    Returns:
      dYdX bech32 account address derived from the compressed public key.
    """
    sha256_hash = hashlib.sha256(self.key_pair.public_key_bytes).digest()
    ripemd160_hash = RIPEMD160.new(sha256_hash).digest()
    data = bech32.convertbits(ripemd160_hash, 8, 5)
    if data is None:
      raise ValueError('Could not convert dYdX wallet bytes to bech32 data')
    return bech32.bech32_encode('dydx', data)

  def sign(self, message: bytes) -> bytes:
    """Sign bytes with the wallet key.

    Args:
      message: Bytes to sign.

    Returns:
      Canonical signature bytes.
    """
    return self.key_pair.sign(message)

  def with_account(self, *, account_number: int, sequence: int) -> 'Wallet':
    """Return a wallet copy with account metadata.

    Args:
      account_number: Cosmos account number.
      sequence: Cosmos account sequence.

    Returns:
      Wallet copy with the same key pair and updated account metadata.
    """
    return Wallet(
      key_pair=self.key_pair,
      account_number=account_number,
      sequence=sequence,
    )
