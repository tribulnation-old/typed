"""Decimal types for request values, serialized in plain notation."""

from decimal import Decimal
from typing_extensions import Annotated
from pydantic import PlainSerializer


def plain(value: Decimal) -> str:
  """Format a decimal in plain notation, never scientific.

  `str(Decimal('0.00000010'))` is `'1.0E-7'`, which most venues reject or misread.
  This keeps every significant digit, trailing zeros included.

  Examples:
    >>> plain(Decimal('0.00000010'))
    '0.00000010'
    >>> plain(Decimal('1E+3'))
    '1000'
  """
  return format(value, 'f')


PlainDecimal = Annotated[Decimal, PlainSerializer(plain, when_used='json')]
"""A `Decimal` that dumps to JSON as a plain-notation string (see `plain`)."""
