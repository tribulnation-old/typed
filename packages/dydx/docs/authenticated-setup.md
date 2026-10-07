# Wallet Setup

dYdX node write workflows use a Cosmos wallet, either a mnemonic or a hex
private key, for signing transactions. Public indexer and chain reads do not require credentials.

You can export your secret mnemonic from the [dYdX website](https://dydx.trade/):

![How to export your secret phrase from dYdX](media/export_secret_phrase.png)


## Environment Variables

The recommended setup is an environment variable:

```bash
export DYDX_MNEMONIC='your wallet mnemonic'
export DYDX_TESTNET_MNEMONIC='your testnet wallet mnemonic'
```

Or, with a hex private key instead of a mnemonic:

```bash
export DYDX_PRIVATE_KEY='your wallet private key'
export DYDX_TESTNET_PRIVATE_KEY='your testnet wallet private key'
```

Set only one of the two per network: a client refuses to guess which wallet to
use when both are set.

Public indexer and chain queries can run without a wallet by passing
`public=True`.

## Direct Usage

You can also pass the mnemonic or private key directly:

```python
from typed_dydx import Dydx

async with Dydx.testnet('your testnet mnemonic') as client:
  await client.node.refresh_wallet()
  assert client.node.wallet is not None
  print(client.node.wallet.address)

async with Dydx.testnet(private_key='0x...') as client:
  ...
```

Pass one or the other, not both. A `0x` prefix on the private key is optional.
When neither is passed, mainnet constructors read `DYDX_MNEMONIC` or
`DYDX_PRIVATE_KEY`, and testnet constructors read `DYDX_TESTNET_MNEMONIC` or
`DYDX_TESTNET_PRIVATE_KEY`.

## Write Workflows

Order placement, cancellation, and raw transaction signing require a wallet.
Use `simulate=True` while validating order flows against testnet.

## Security Notes

- never commit credentials to git
- prefer `public=True` for read-only scripts
- use a separate testnet wallet for development
- simulate new transaction flows before broadcasting
- rotate credentials after any suspected leak

## Troubleshooting

If authenticated requests fail:

- confirm the mnemonic or private key belongs to a funded dYdX account
- confirm your environment variables are loaded
- check [Error Handling](reference/error-handling.md) for the client error model
