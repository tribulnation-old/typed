"""`PlainDecimal` dumps in plain notation, and a `str` beside it passes through untouched."""

from decimal import Decimal
from typing_extensions import TypedDict
import warnings

import pytest

from typed_core.decimals import PlainDecimal
from typed_core.validation import validator


class Order(TypedDict):
  price: PlainDecimal | str


@pytest.mark.parametrize(
  'value, wire',
  [
    (Decimal('0.00000010'), b'{"price":"0.00000010"}'),
    (Decimal('1E+3'), b'{"price":"1000"}'),
    (Decimal('-2.50'), b'{"price":"-2.50"}'),
  ],
)
def test_decimal_dumps_in_plain_notation(value: Decimal, wire: bytes):
  assert validator(Order).dump({'price': value}) == wire


@pytest.mark.parametrize('value', ['1e-7', '0.00000010', '100.0'])
def test_string_passes_through_untouched(value: str):
  with warnings.catch_warnings():
    warnings.simplefilter('error')
    assert validator(Order).dump({'price': value}) == f'{{"price":"{value}"}}'.encode()


def test_validation_still_yields_a_decimal():
  class Row(TypedDict):
    price: PlainDecimal

  assert validator(Row).json(b'{"price":"1.0E-7"}') == {'price': Decimal('1.0E-7')}
