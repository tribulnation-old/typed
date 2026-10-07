# Environment Variables

These are the environment variables read by dYdX wallet-aware constructors.

## Wallet Variables

```bash
DYDX_MNEMONIC=
DYDX_TESTNET_MNEMONIC=
DYDX_PRIVATE_KEY=
DYDX_TESTNET_PRIVATE_KEY=
```

## Guidance

- keep local values in an untracked `.env` file if needed
- load them explicitly in scripts and notebooks
- pass `public=True` when constructing read-only clients without a wallet
- mainnet node constructors read `DYDX_MNEMONIC` or `DYDX_PRIVATE_KEY`
- testnet node constructors read `DYDX_TESTNET_MNEMONIC` or `DYDX_TESTNET_PRIVATE_KEY`
- set only one wallet variable per network; both at once raises `AuthError`

## HTTP Proxy Environment

| Variable | Description |
| --- | --- |
| `HTTP_PROXY` | Proxy URL for HTTP destinations. |
| `HTTPS_PROXY` | Proxy URL for HTTPS destinations. |
| `ALL_PROXY` | Fallback proxy URL when a scheme-specific variable is absent. |
| `NO_PROXY` | Comma-separated hosts or URLs that bypass environment proxies. |

HTTPX reads these by default. See [HTTP Timeouts and Proxies](async-usage.md#http-timeouts-and-proxies)
for explicit proxy configuration and `trust_env=False`.
