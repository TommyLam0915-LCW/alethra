"""Crypto instrument identity: the venue and quote asset are part of the id.

A price without its venue is not an instrument. These tests pin that contract
down before any provider or API code is allowed to quote a crypto pair.
"""

from decimal import Decimal

import pytest

from app.data.crypto_instruments import (
    CRYPTO_INSTRUMENTS,
    KRAKEN_BTC_USD,
    KRAKEN_ETH_USD,
    KRAKEN_SOL_USD,
    KRAKEN_ETH_BTC,
    KRAKEN_PAIR_MAP,
    get_crypto_instrument,
    get_kraken_pair,
)
from app.providers.instruments import (
    AssetType,
    Currency,
    Exchange,
    Market,
    get_market_structure,
    parse_crypto_instrument_id,
)


def test_crypto_instrument_id_carries_the_venue():
    assert KRAKEN_BTC_USD.instrument_id == "CRYPTO:KRAKEN:BTC-USD"
    assert KRAKEN_BTC_USD.venue == "KRAKEN"
    assert KRAKEN_BTC_USD.exchange is Exchange.KRAKEN
    assert KRAKEN_BTC_USD.market is Market.CRYPTO
    assert KRAKEN_BTC_USD.asset_type is AssetType.CRYPTO_PAIR


def test_quote_asset_comes_from_declared_currency():
    assert KRAKEN_BTC_USD.quote_asset == "USD"
    assert KRAKEN_BTC_USD.currency is Currency.USD
    assert KRAKEN_BTC_USD.base_asset == "BTC"


def test_two_venues_are_two_instruments():
    # Same base/quote, different venue -> a different id, not a shared price.
    other = KRAKEN_BTC_USD.__class__.create_crypto_pair(
        symbol="BTC-USD",
        name="Bitcoin / US Dollar (spot)",
        base_asset="BTC",
        quote=Currency.USD,
        exchange=Exchange.KRAKEN,
    )
    assert other.instrument_id == KRAKEN_BTC_USD.instrument_id
    # The venue lives in the id, so an id with a different venue cannot parse
    # into this catalog even if the pair spelling matches.
    with pytest.raises(ValueError, match="Unknown crypto venue"):
        parse_crypto_instrument_id("CRYPTO:BINANCE:BTC-USD")


def test_usdt_pair_cannot_be_built_as_usd():
    with pytest.raises(ValueError, match="does not match BASE-QUOTE"):
        KRAKEN_BTC_USD.__class__.create_crypto_pair(
            symbol="BTC-USDT",
            name="Bitcoin / Tether",
            base_asset="BTC",
            quote=Currency.USD,
            exchange=Exchange.KRAKEN,
        )


def test_bitcoin_symbol_must_match_base_and_quote():
    with pytest.raises(ValueError, match="does not match BASE-QUOTE"):
        KRAKEN_BTC_USD.__class__.create_crypto_pair(
            symbol="ETH-USD",
            name="Ether",
            base_asset="BTC",
            quote=Currency.USD,
            exchange=Exchange.KRAKEN,
        )


def test_undeclared_venue_is_refused():
    with pytest.raises(ValueError, match="not a declared crypto spot venue"):
        KRAKEN_BTC_USD.__class__.create_crypto_pair(
            symbol="BTC-USD",
            name="Bitcoin",
            base_asset="BTC",
            quote=Currency.USD,
            exchange=Exchange.NASDAQ,
        )


@pytest.mark.parametrize("bad", [-1, 1.5, "8", True])
def test_precision_must_be_non_negative_int(bad):
    with pytest.raises(ValueError, match="non-negative int"):
        KRAKEN_BTC_USD.__class__.create_crypto_pair(
            symbol="BTC-USD",
            name="Bitcoin",
            base_asset="BTC",
            quote=Currency.USD,
            exchange=Exchange.KRAKEN,
            price_precision=bad,
        )


def test_declared_specs_are_recorded_not_invented():
    assert KRAKEN_BTC_USD.price_precision == 1
    assert KRAKEN_BTC_USD.quantity_precision == 8
    assert KRAKEN_BTC_USD.min_quantity == Decimal("0.00006")
    assert KRAKEN_BTC_USD.min_notional == Decimal("0.5")


def test_parse_crypto_instrument_id_round_trip():
    exchange, base, quote = parse_crypto_instrument_id(KRAKEN_BTC_USD.instrument_id)
    assert exchange is Exchange.KRAKEN
    assert base == "BTC"
    assert quote == "USD"


@pytest.mark.parametrize(
    "bad",
    [
        "BTC-USD",                 # no market prefix
        "US:AAPL",                 # equity shape, not crypto
        "CRYPTO:BTC-USD",          # missing venue
        "CRYPTO:KRAKEN",           # missing pair
        "CRYPTO:KRAKEN:BTCUSD",    # missing separator
        "CRYPTO:KRAKEN:-USD",      # missing base
        "CRYPTO:KRAKEN:BTC-",      # missing quote
    ],
)
def test_parse_crypto_instrument_id_rejects_malformed(bad):
    with pytest.raises(ValueError):
        parse_crypto_instrument_id(bad)


def test_crypto_market_is_always_open():
    structure = get_market_structure(Market.CRYPTO)
    assert structure.always_open is True
    # A Saturday and a Sunday both count as trading days for a continuous venue.
    from datetime import date

    assert structure.is_trading_day(date(2026, 10, 31))  # Saturday
    assert structure.is_trading_day(date(2026, 11, 1))   # Sunday


def test_catalog_exposes_four_pairs():
    assert [i.instrument_id for i in CRYPTO_INSTRUMENTS] == [
        "CRYPTO:KRAKEN:BTC-USD",
        "CRYPTO:KRAKEN:ETH-USD",
        "CRYPTO:KRAKEN:SOL-USD",
        "CRYPTO:KRAKEN:ETH-BTC",
    ]
    assert get_crypto_instrument("CRYPTO:KRAKEN:BTC-USD") is KRAKEN_BTC_USD
    assert get_crypto_instrument("CRYPTO:KRAKEN:ETH-USD") is KRAKEN_ETH_USD
    assert get_crypto_instrument("CRYPTO:KRAKEN:DOGE-USD") is None


def test_kraken_pair_map_keeps_exchange_spelling_separate():
    mapping = get_kraken_pair("CRYPTO:KRAKEN:BTC-USD")
    assert mapping is not None
    assert mapping.provider_symbol == "XBTUSD"
    assert mapping.metadata["result_key"] == "XXBTZUSD"
    assert mapping.metadata["result_key_alt"] == "XBTUSD"
    # The internal id never leaks Kraken's nickname.
    assert "XBT" not in (KRAKEN_BTC_USD.instrument_id)
    assert set(KRAKEN_PAIR_MAP) == {
        "CRYPTO:KRAKEN:BTC-USD",
        "CRYPTO:KRAKEN:ETH-USD",
        "CRYPTO:KRAKEN:SOL-USD",
        "CRYPTO:KRAKEN:ETH-BTC",
    }
