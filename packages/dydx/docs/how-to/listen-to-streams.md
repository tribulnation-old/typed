# Listen To Streams

Use `client.indexer.streams` for live indexer WebSocket subscriptions.

```python
from typed_dydx import Dydx

async with Dydx.testnet(public=True) as client:
  stream = await client.indexer.streams.markets()
  print(stream.reply)
  async for item in stream:
    print(item)
```

Available stream helpers include:

- `block_height`
- `markets`
- `trades`
- `candles`
- `orders`
- `subaccounts`
- `parent_subaccounts`

Streams return a typed `Stream` object with the subscription snapshot in
`reply`, an async iterator for updates, and an unsubscribe callback.

## Full Node Orderbook Stream

`chain.clob.stream_orderbook_updates` streams orderbook updates, fills, taker orders,
subaccount updates, and prices straight from a dYdX full node, ahead of the indexer.
Public gRPC providers don't serve it: build the chain client against a full node started
with `--grpc-streaming-enabled` (plaintext gRPC on port `9090` by default).

```python
from typed_dydx.chain import Chain
from typed_dydx.protos.dydxprotocol.subaccounts import SubaccountId

node = Chain.from_hosts(grpc_host='10.0.0.5', modules={'port': 9090, 'ssl': False})
async with node:
  me = SubaccountId(owner='dydx1...', number=0)
  async with node.clob.stream_orderbook_updates(clob_pair_id=[0], subaccount_ids=[me]) as stream:
    async for response in stream:
      for update in response.updates:
        fill = update.order_fill
        if fill is None:
          continue
        finalized = update.exec_mode == 7
        for order, filled in zip(fill.orders, fill.fill_amounts):
          print(update.block_height, finalized, order.order_id, filled)
```

Like the indexer streams, it also works without a context manager:
`stream = await node.clob.stream_orderbook_updates(clob_pair_id=[0])`, iterate, then
`await stream.unsubscribe()`. Opening the stream waits for the node's response headers,
so a rejected request (`ApiError`: the node reports it as gRPC `UNKNOWN`) or an
unreachable node (`NetworkError`) raises there, not mid-iteration.

`Chain.new(GrpcClient(host='10.0.0.5', port=9090, ssl=False), chain_comet_client=...)`
does the same from an existing transport, and `Dydx.new(chain=...)` puts it behind a full
client.

Responses are the node's protobuf messages, unchanged. A few facts to read them by:

- `exec_mode` `7` marks updates from a committed block. Lower values are optimistic: the
  node's own view of the book, which may be reverted or replayed. `102` carries the
  initial snapshots and the node's post-commit replays, which are optimistic too.
- `fill_amounts` are each order's total filled quantums after the match, not the size of
  this match. Deleveraging fills carry no orders and no price.
- Updates carry a block height but no block time.
- Leaving `clob_pair_id` empty streams every pair, including with only `subaccount_ids`
  or `market_ids` set.
- The stream can end without an error when the node drops a slow subscriber; resubscribe
  to keep listening.
