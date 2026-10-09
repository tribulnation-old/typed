# Error Handling

Typed Clients should distinguish between failure modes clearly.

## Common Error Categories

- `NetworkError`: connection failures, timeouts, transport errors
- `AuthError`: missing credentials, invalid signatures, rejected authentication
- `BadRequest`: invalid request parameters or malformed payloads rejected locally or remotely
- `RateLimited`: provider-side rate limiting
- `ApiError`: the remote API returned an application-level error
- `ValidationError`: the response shape did not match the expected schema
- `LogicError`: incorrect local usage of the client

## Recommended Pattern

```python
from typed_dydx.core import ApiError, AuthError, NetworkError, RateLimited, ValidationError

try:
  ...
except ValidationError:
  ...
except AuthError:
  ...
except RateLimited:
  ...
except ApiError:
  ...
except NetworkError:
  ...
```

## Operational Guidance

- retry transient network failures carefully
- do not blindly retry authentication failures
- back off on rate limits according to the provider's documented policy
- log validation failures because they often signal upstream API changes
- include request identifiers from the provider when available


## gRPC Errors

Chain gRPC calls map grpclib failures by status, keeping the original `GRPCError` as
`__cause__`:

- transport failures, `UNAVAILABLE`, `DEADLINE_EXCEEDED`: `NetworkError`
- `INVALID_ARGUMENT`, `NOT_FOUND`, `OUT_OF_RANGE`, `FAILED_PRECONDITION`,
  `ALREADY_EXISTS`: `BadRequest`
- `UNAUTHENTICATED`, `PERMISSION_DENIED`: `AuthError`
- `RESOURCE_EXHAUSTED`: `RateLimited`
- any other status: `ApiError`

A transaction rejected by the chain is not a gRPC error: its code arrives in the
successful broadcast response.

## ABCI Queries

`chain.comet.abci_query` raises `ApiError` when the application returns a nonzero
result code, even if the HTTP response is successful or response validation is disabled.
The exception's arguments contain the HTTP status and the raw ABCI response, including
`code`, `codespace`, and `log`.

This applies to the query itself failing. A transaction inspection endpoint can still
return a transaction that failed on-chain; that transaction's execution code remains
part of the returned data.
