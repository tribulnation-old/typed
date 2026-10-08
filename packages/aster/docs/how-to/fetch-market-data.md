# Fetch Market Data

Market data is public: build the client with `public=True`. `futures`, `spot` and
`prediction` each have a `market` router with the same core methods.

## Symbols And Trading Rules

```python
from typed_aster import Aster

async with Aster.new(public=True) as client:
  info = await client.futures.market.exchange_info()
  for symbol in info['symbols'][:5]:
    print(symbol['symbol'], symbol['status'])

  spot_info = await client.spot.market.exchange_info()
  print(len(spot_info['symbols']))
```

## Order Book

```python
from typed_aster import Aster

async with Aster.new(public=True) as client:
  book = await client.futures.market.depth('BTCUSDT', limit=10)
  best_bid_price, best_bid_qty = book['bids'][0]
  print(best_bid_price, best_bid_qty)
```

## Candles

Each candle is a tuple: open time, open, high, low, close, volume, close time, and so on.

```python
from datetime import datetime, timedelta, timezone

from typed_aster import Aster

async with Aster.new(public=True) as client:
  candles = await client.spot.market.klines(
    'BTCUSDT',
    interval='1h',
    start_time=datetime.now(timezone.utc) - timedelta(days=1),
  )
  open_time, open_, high, low, close, *_ = candles[-1]
  print(open_time, close)
```

Futures also has `mark_price_klines`, `index_price_klines` (by `pair`) and `market_klines`.

## Tickers

With no `symbol`, ticker methods return every symbol as a list.

```python
from typed_aster import Aster

async with Aster.new(public=True) as client:
  ticker = await client.futures.market.ticker_24hr('BTCUSDT')
  prices = await client.futures.market.ticker_price()
  best = await client.futures.market.book_ticker('BTCUSDT')
  print(ticker, prices, best)
```

## Recent Trades

```python
from typed_aster import Aster

async with Aster.new(public=True) as client:
  trades = await client.futures.market.trades('BTCUSDT', limit=20)
  agg = await client.futures.market.agg_trades('BTCUSDT', limit=20)
  print(trades[-1]['price'], agg[-1])
```

## Funding And Mark Price

```python
from typed_aster import Aster

async with Aster.new(public=True) as client:
  mark = await client.futures.market.premium_index('BTCUSDT')
  funding = await client.futures.market.funding_rate('BTCUSDT', limit=10)
  print(mark, funding[-1]['fundingRate'])
```

## Open Interest

Open interest is served one symbol at a time, in base units. Aster does not list this
endpoint in its API reference.

```python
from typed_aster import Aster

async with Aster.new(public=True) as client:
  oi = await client.futures.market.open_interest('BTCUSDT')
  print(oi['openInterest'], oi['time'])
```
