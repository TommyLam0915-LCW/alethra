import pytest
from datetime import date, datetime, time
from app.providers.instruments import (
    Market,
    Exchange,
    Currency,
    AssetType,
    Instrument,
    TradingSession,
    MarketStructure,
    ProviderSymbolMapping,
    LotSizeInfo,
    LotSizeSource,
    US_MARKET_STRUCTURE,
    HK_MARKET_STRUCTURE,
    get_market_structure,
    parse_instrument_id,
)


class TestInstrumentCreation:
    def test_create_us_instrument(self):
        instrument = Instrument.create_us("AAPL", "Apple Inc.", AssetType.STOCK)
        assert instrument.market == Market.US
        assert instrument.symbol == "AAPL"
        assert instrument.name == "Apple Inc."
        assert instrument.asset_type == AssetType.STOCK
        assert instrument.exchange == Exchange.NYSE
        assert instrument.currency == Currency.USD
        assert instrument.timezone == "America/New_York"
        assert instrument.lot_size_info.board_lot == 1
        assert instrument.lot_size_info.source == LotSizeSource.MARKET_CONVENTION
        assert instrument.lot_size_info.verified is True
        assert instrument.is_execution_ready is True

    def test_create_us_instrument_uppercases_symbol(self):
        instrument = Instrument.create_us("aapl", "Apple Inc.", AssetType.STOCK)
        assert instrument.symbol == "AAPL"

    def test_create_us_instrument_with_custom_exchange(self):
        instrument = Instrument.create_us(
            "SPY", "SPDR S&P 500 ETF", AssetType.ETF, exchange=Exchange.ARCA
        )
        assert instrument.exchange == Exchange.ARCA

    def test_create_hk_instrument(self):
        instrument = Instrument.create_hk("700", "Tencent Holdings", AssetType.STOCK)
        assert instrument.market == Market.HK
        assert instrument.symbol == "0700"  # Padded to 4 digits
        assert instrument.name == "Tencent Holdings"
        assert instrument.asset_type == AssetType.STOCK
        assert instrument.exchange == Exchange.HKEX
        assert instrument.currency == Currency.HKD
        assert instrument.timezone == "Asia/Hong_Kong"
        assert instrument.lot_size_info.board_lot is None
        assert instrument.lot_size_info.source == LotSizeSource.UNKNOWN
        assert instrument.lot_size_info.verified is False
        assert instrument.is_execution_ready is False

    def test_create_hk_instrument_pads_symbol(self):
        instrument1 = Instrument.create_hk("1", "Test Stock 1", AssetType.STOCK)
        assert instrument1.symbol == "0001"

        instrument2 = Instrument.create_hk("700", "Tencent", AssetType.STOCK)
        assert instrument2.symbol == "0700"

        instrument3 = Instrument.create_hk("9988", "Alibaba", AssetType.STOCK)
        assert instrument3.symbol == "9988"  # Already 4 digits

    def test_create_hk_instrument_with_board_lot(self):
        instrument = Instrument.create_hk(
            "0005", "HSBC Holdings", AssetType.STOCK, board_lot=400
        )
        assert instrument.lot_size_info.board_lot == 400
        assert instrument.lot_size_info.source == LotSizeSource.MARKET_CONVENTION
        assert instrument.lot_size_info.verified is False
        assert instrument.is_execution_ready is False

    def test_create_hk_instrument_without_board_lot_is_unknown(self):
        instrument = Instrument.create_hk("0700", "Tencent", AssetType.STOCK)
        assert instrument.lot_size_info.board_lot is None
        assert instrument.lot_size_info.is_known is False
        assert instrument.is_execution_ready is False


class TestLotSizeInfo:
    def test_known_and_verified_is_execution_ready(self):
        info = LotSizeInfo(board_lot=100, source=LotSizeSource.EXCHANGE_OFFICIAL, verified=True)
        assert info.is_known is True
        assert info.is_execution_ready is True

    def test_known_but_unverified_not_execution_ready(self):
        info = LotSizeInfo(board_lot=100, source=LotSizeSource.MARKET_CONVENTION, verified=False)
        assert info.is_known is True
        assert info.is_execution_ready is False

    def test_unknown_board_lot_not_execution_ready(self):
        info = LotSizeInfo(board_lot=None, source=LotSizeSource.UNKNOWN, verified=False)
        assert info.is_known is False
        assert info.is_execution_ready is False

    def test_lot_size_info_is_frozen(self):
        info = LotSizeInfo(board_lot=1, source=LotSizeSource.MARKET_CONVENTION, verified=True)
        with pytest.raises(Exception):  # FrozenInstanceError
            info.board_lot = 100


class TestInstrumentID:
    def test_instrument_id_us(self):
        instrument = Instrument.create_us("AAPL", "Apple Inc.", AssetType.STOCK)
        assert instrument.instrument_id == "US:AAPL"

    def test_instrument_id_hk(self):
        instrument = Instrument.create_hk("700", "Tencent", AssetType.STOCK)
        assert instrument.instrument_id == "HK:0700"

    def test_parse_instrument_id_us(self):
        market, symbol = parse_instrument_id("US:AAPL")
        assert market == Market.US
        assert symbol == "AAPL"

    def test_parse_instrument_id_hk(self):
        market, symbol = parse_instrument_id("HK:0700")
        assert market == Market.HK
        assert symbol == "0700"

    def test_parse_instrument_id_invalid_format(self):
        with pytest.raises(ValueError, match="Invalid instrument ID format"):
            parse_instrument_id("AAPL")

    def test_parse_instrument_id_unknown_market(self):
        with pytest.raises(ValueError, match="Unknown market"):
            parse_instrument_id("XX:AAPL")


class TestMarketStructure:
    def test_us_market_structure(self):
        structure = US_MARKET_STRUCTURE
        assert structure.market == Market.US
        assert structure.currency == Currency.USD
        assert structure.timezone == "America/New_York"
        assert len(structure.sessions) == 3

    def test_us_market_sessions(self):
        sessions = US_MARKET_STRUCTURE.sessions
        session_names = [s.name for s in sessions]
        assert "Pre-market" in session_names
        assert "Regular" in session_names
        assert "After-hours" in session_names

        regular_sessions = [s for s in sessions if s.is_regular]
        assert len(regular_sessions) == 1
        assert regular_sessions[0].name == "Regular"
        assert regular_sessions[0].start == time(9, 30)
        assert regular_sessions[0].end == time(16, 0)

    def test_hk_market_structure(self):
        structure = HK_MARKET_STRUCTURE
        assert structure.market == Market.HK
        assert structure.currency == Currency.HKD
        assert structure.timezone == "Asia/Hong_Kong"
        assert len(structure.sessions) == 4

    def test_hk_market_sessions(self):
        sessions = HK_MARKET_STRUCTURE.sessions
        session_names = [s.name for s in sessions]
        assert "Pre-opening" in session_names
        assert "Morning" in session_names
        assert "Afternoon" in session_names
        assert "Closing auction" in session_names

        regular_sessions = [s for s in sessions if s.is_regular]
        assert len(regular_sessions) == 2  # Morning and Afternoon

    def test_is_trading_day_weekday(self):
        monday = date(2024, 1, 8)  # A Monday
        assert US_MARKET_STRUCTURE.is_trading_day(monday) is True

    def test_is_trading_day_saturday(self):
        saturday = date(2024, 1, 6)  # A Saturday
        assert US_MARKET_STRUCTURE.is_trading_day(saturday) is False

    def test_is_trading_day_sunday(self):
        sunday = date(2024, 1, 7)  # A Sunday
        assert US_MARKET_STRUCTURE.is_trading_day(sunday) is False

    def test_is_trading_day_holiday(self):
        # Create a structure with a holiday
        holiday = date(2024, 12, 25)  # Christmas
        structure = MarketStructure(
            market=Market.US,
            exchange=Exchange.NYSE,
            currency=Currency.USD,
            timezone="America/New_York",
            sessions=[TradingSession("Regular", time(9, 30), time(16, 0))],
            holidays={holiday},
        )
        assert structure.is_trading_day(holiday) is False

    def test_get_local_time(self):
        # UTC time (timezone-aware)
        import zoneinfo
        utc_dt = datetime(2024, 1, 8, 14, 30, 0, tzinfo=zoneinfo.ZoneInfo("UTC"))
        local_dt = US_MARKET_STRUCTURE.get_local_time(utc_dt)
        # Should be 9:30 AM Eastern (UTC-5 in January)
        assert local_dt.hour == 9
        assert local_dt.minute == 30


class TestGetMarketStructure:
    def test_get_us_market_structure(self):
        structure = get_market_structure(Market.US)
        assert structure.market == Market.US
        assert structure == US_MARKET_STRUCTURE

    def test_get_hk_market_structure(self):
        structure = get_market_structure(Market.HK)
        assert structure.market == Market.HK
        assert structure == HK_MARKET_STRUCTURE


class TestProviderSymbolMapping:
    def test_create_mapping(self):
        mapping = ProviderSymbolMapping(
            provider_id="lseg",
            instrument_id="US:AAPL",
            provider_symbol="AAPL.O",
            provider_exchange="NAS",
        )
        assert mapping.provider_id == "lseg"
        assert mapping.instrument_id == "US:AAPL"
        assert mapping.provider_symbol == "AAPL.O"
        assert mapping.provider_exchange == "NAS"

    def test_create_mapping_with_metadata(self):
        mapping = ProviderSymbolMapping(
            provider_id="ibkr",
            instrument_id="HK:0700",
            provider_symbol="0700",
            provider_exchange="SEHK",
            metadata={"secType": "STK", "currency": "HKD"},
        )
        assert mapping.metadata["secType"] == "STK"
        assert mapping.metadata["currency"] == "HKD"


class TestInstrumentImmutability:
    def test_instrument_is_frozen(self):
        instrument = Instrument.create_us("AAPL", "Apple Inc.", AssetType.STOCK)
        with pytest.raises(Exception):  # FrozenInstanceError
            instrument.symbol = "MSFT"

    def test_market_structure_is_frozen(self):
        with pytest.raises(Exception):  # FrozenInstanceError
            US_MARKET_STRUCTURE.timezone = "UTC"
