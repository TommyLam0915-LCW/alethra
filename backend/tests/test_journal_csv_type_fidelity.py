"""G1-3 — the journal CSV keeps numbers as numbers and text as guarded text.

Every cell of the export passes through one renderer, and the money columns
reach it as *text* because the JSON body carries decimals as strings. The
formula guard covers ``-``, so before this fix a loss was written as ``'-80``:
a spreadsheet shows it left-aligned, refuses to add it, and the one column a
user is most likely to sum was the column the guard broke.

Rendering is now decided per column, so the five case shapes the requirement
names are asserted per column — negative, decimal, zero, empty, leading-formula
text — and the product-path tests read the file the real endpoint returns
rather than the helper's return value.
"""

from __future__ import annotations

import csv
import io
from decimal import Decimal, InvalidOperation

import pytest
from sqlalchemy import Float, Integer, Numeric

from app.models.strategy_study import StrategyStudyTrade, StrategyStudyRun
from app.services import journal_service as svc
from tests.conftest import TestSession
from tests.test_journal_api import (
    INSTRUMENT_BTC,
    _auth_headers,
    _new_user,
    _run,
    _trade,
)

def _schema_numeric_columns(header):
    """Which exported columns the schema itself types as numbers.

    Derived from the ORM and not from the renderer's registry: a sweep that
    walks the registry checks nothing at all the day the registry is emptied, or
    a money column is exported without joining it.
    """
    numeric_types = (Integer, Numeric, Float)
    schema_numeric = {
        column.name
        for model in (StrategyStudyTrade, StrategyStudyRun)
        for column in model.__table__.columns
        if isinstance(column.type, numeric_types)
    }
    columns = schema_numeric & set(header)
    assert columns, "the export has no numeric column, so this sweep is vacuous"
    return columns


@pytest.fixture()
def typed_export_user_id():
    """Three trades: a decimal win, a negative loss, a zero, plus a hostile label.

    Each row exists to exercise one case shape in the real file — ``150.5`` the
    decimal, ``-80`` the negative, ``0`` the zero — and the third row's Chinese
    label starts with ``=`` so the text guard has something to catch here too.
    """
    db = TestSession()
    try:
        uid = _new_user(db)
        run = _run(db, uid, instrument=INSTRUMENT_BTC, interval=1440, trade_count=3)
        _trade(db, run, index=0, rule_id="breakout_trend", net_pnl="150.5",
               entry_day_offset=0)
        _trade(db, run, index=1, rule_id="breakout_trend", net_pnl="-80",
               entry_day_offset=5, outcome="loss", exit_reason="stop_loss")
        _trade(db, run, index=2, rule_id="range_fade", net_pnl="0",
               entry_day_offset=10, rule_label_zh="=SUM(A1:A2)")
        db.commit()
        return uid
    finally:
        db.close()


def _export(client, user_id):
    """Fetch the real file and return (header, data rows) as lists of text."""
    resp = client.get(
        "/api/v1/journal/trades.csv", headers=_auth_headers(client, user_id)
    )
    assert resp.status_code == 200
    rows = list(csv.reader(io.StringIO(resp.text)))
    assert len(rows) > 1, "the fixture exported no data rows to type-check"
    return rows[0], rows[1:]


def _columns(header):
    return {name: i for i, name in enumerate(header)}


class TestCellRenderingIsPerColumn:
    """The same string renders differently depending on its column."""

    @pytest.mark.parametrize(
        "value, expected",
        [
            ("-80", "-80"),                      # negative: the case that broke
            ("150.50000000", "150.50000000"),    # decimal: precision preserved
            ("0", "0"),                          # zero stays zero
            ("-0.000500", "-0.000500"),          # negative sub-cent cost
            (None, ""),                          # empty means null
        ],
    )
    def test_numeric_column_keeps_parseable_text(self, value, expected):
        cell = svc._csv_cell(value, "net_pnl")
        assert cell == expected
        if cell:
            assert not cell.startswith("'"), f"{cell!r} is text, not a number"
            assert Decimal(cell).is_finite()

    def test_same_value_in_a_text_column_stays_guarded(self):
        """A number-looking string in a text column is still guarded.

        Dropping the guard globally would have been the easy way to make the
        numeric cells parse; this is what says the fix is per column instead.
        """
        assert svc._csv_cell("-80", "net_pnl") == "-80"
        assert svc._csv_cell("-80", "exit_reason") == "'-80"

    @pytest.mark.parametrize(
        "column, value, expected",
        [
            ("pnl_reason_en", "=SUM(A1)", "'=SUM(A1)"),
            ("rule_label_zh", "+1", "'+1"),
            ("exit_reason", "-cmd", "'-cmd"),
            ("instrument_id", "@x", "'@x"),
            ("outcome", "\ttab", "'\ttab"),
        ],
    )
    def test_text_columns_keep_the_formula_guard(self, column, value, expected):
        assert svc._csv_cell(value, column) == expected

    def test_ints_and_decimals_render_like_text_decimals(self):
        assert svc._csv_cell(0, "trade_index") == "0"
        assert svc._csv_cell(Decimal("-1.25"), "gross_pnl") == "-1.25"
        assert svc._csv_cell(True, "outcome") == "true"

    def test_unknown_column_is_treated_as_text(self):
        """Fail-closed: a column nobody registered keeps the guard."""
        assert svc._csv_cell("-80") == "'-80"
        assert svc._csv_cell("-80", "brand_new_column") == "'-80"

    @pytest.mark.parametrize("junk", ["-inf", "NaN", "not-a-number"])
    def test_a_non_number_in_a_numeric_column_is_not_coerced(self, junk):
        """It is written as text, unchanged or guarded — never as a plausible 0."""
        cell = svc._csv_cell(junk, "net_pnl")
        assert cell.lstrip("'") == junk
        try:
            assert not Decimal(cell).is_finite()
        except InvalidOperation:
            pass


class TestExportedFileIsTyped:
    """The file the endpoint returns, read back as a spreadsheet would."""

    def test_every_numeric_cell_in_the_real_file_parses(self, client, typed_export_user_id):
        header, rows = _export(client, typed_export_user_id)
        idx = _columns(header)
        assert sorted(idx) == sorted(header), "duplicate header names"
        numeric = _schema_numeric_columns(header)
        for row in rows:
            for name in sorted(numeric):
                cell = row[idx[name]]
                if cell:
                    assert not cell.startswith("'"), (
                        f"{name}={cell!r}: a numeric column exported as text"
                    )
                    assert Decimal(cell).is_finite(), f"{name}={cell!r} is not finite"

    def test_the_negative_row_is_the_one_that_used_to_break(self, client, typed_export_user_id):
        header, rows = _export(client, typed_export_user_id)
        idx = _columns(header)
        losses = [r[idx["net_pnl"]] for r in rows if r[idx["net_pnl"]].startswith("-")]
        assert losses, "the fixture exported no loss to check"
        assert all(not cell.startswith("'") for cell in losses)
        assert Decimal("-80") in [Decimal(c) for c in losses]

    def test_the_exported_pnl_column_can_be_added_up(self, client, typed_export_user_id):
        """What the guard broke is what the column is for: summing it.

        Hand-added from the three seeded rows (150.5, -80, 0) so the expectation
        is the fixture's, not the exporter's.
        """
        header, rows = _export(client, typed_export_user_id)
        idx = _columns(header)
        total = sum(Decimal(r[idx["net_pnl"]]) for r in rows)
        assert total == Decimal("70.5")

    def test_a_formula_prefixed_label_is_guarded_in_the_real_file(
        self, client, typed_export_user_id
    ):
        header, rows = _export(client, typed_export_user_id)
        idx = _columns(header)
        guarded = [r[idx["rule_label_zh"]] for r in rows
                   if r[idx["rule_label_zh"]].startswith("'")]
        assert guarded == ["'=SUM(A1:A2)"], (
            "the hostile label reached the file unguarded, or the guard moved "
            "into a numeric column"
        )

    def test_an_unmeasured_number_is_empty_not_zero(self, client, typed_export_user_id):
        """The fixture never sets mfe/mae/cost ratio, so null must stay null."""
        header, rows = _export(client, typed_export_user_id)
        idx = _columns(header)
        for name in ("mfe_pct", "mae_pct", "cost_ratio_pct"):
            cells = {r[idx[name]] for r in rows}
            assert cells == {""}, f"{name} exported {cells}, expected empty for null"

    def test_json_body_and_csv_agree_on_the_same_row(self, client, typed_export_user_id):
        """Fixing the CSV must not move the JSON contract.

        The list view keeps decimals as strings for exactness; the file must
        carry the same number, so the two are compared as decimals rather than
        as text.
        """
        trades = client.get(
            "/api/v1/journal/trades", headers=_auth_headers(client, typed_export_user_id)
        ).json()["trades"]
        header, rows = _export(client, typed_export_user_id)
        idx = _columns(header)
        by_id = {r[idx["trade_id"]]: r for r in rows}
        assert len(by_id) == len(rows) == len(trades)
        numeric = _schema_numeric_columns(header)
        for trade in trades:
            row = by_id[trade["trade_id"]]
            for name in sorted(numeric):
                json_value = trade.get(name)
                cell = row[idx[name]]
                if json_value is None:
                    assert cell == "", f"{name}: JSON null exported as {cell!r}"
                else:
                    assert Decimal(cell) == Decimal(str(json_value)), (
                        f"{name}: file says {cell!r}, body says {json_value!r}"
                    )

    def test_numeric_registry_matches_the_columns_the_schema_types_as_numbers(
        self, client, typed_export_user_id
    ):
        """The registry is hand-written; the schema is not asked to agree.

        A money column added to the export without being registered here would
        silently keep the guard and re-introduce this bug, so the expectation is
        derived from the ORM's own column types and compared against the set the
        renderer consults.
        """
        header, _ = _export(client, typed_export_user_id)
        expected = _schema_numeric_columns(header)
        registered = set(svc._JOURNAL_CSV_NUMERIC_COLUMNS)
        assert expected == registered, (
            f"schema numbers not registered: {sorted(expected - registered)}; "
            f"registered but not a schema number: {sorted(registered - expected)}"
        )
