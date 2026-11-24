"""G3-7: a rights sentence belongs to the source whose terms it describes.

Two defects were pinned here. The first is a keying defect: one Kraken-only
constant was printed over every crypto row, whatever venue that row's own
identity named, so a second venue would have shipped under somebody else's
clearance. The second is a claim with no source: four spellings of
"not cleared for redistribution" appeared across the API with nothing saying
whether anybody had read a document or simply never looked.

What these tests therefore demand:

* the record is looked up by the provider/venue that served the data, and a
  venue with no record gets ``TERMS_NOT_RECORDED`` — never a neighbour's
  sentence, never a neighbour's zh note;
* the emitted status always travels with its basis, and no record currently
  claims ``terms_document_read`` because no market-data terms document has been
  read on this platform;
* a dataset may not be called ``licensed`` while naming no licence, which is
  presumed approval written into the catalog;
* the stored-bars lineage row declares the same thing instead of leaving a NULL;
* a research lane's own rights sentence is the sentence of the source its bars
  came from, and a lane that cannot see the source says that instead.

Every refusal node is paired with a should-pass sibling, so a gate that is red
because it was broken and a gate that is red because it worked are different
readings.

The third defect is the second one in prose: three research-lane disclosure
lists hand-typed "Market-data redistribution is NOT_CLEARED" and let that one
sentence speak for whichever source happened to serve the bars. Those lanes are
checked here by running the same bars under different declared sources, so the
lookup has to move or the test fails.
"""
from __future__ import annotations

import dataclasses

import pytest

from app.api import instruments as instruments_api
from app.data.crypto_instruments import CRYPTO_INSTRUMENTS, KRAKEN_BTC_USD, get_crypto_instrument
from app.data.market_terms import (
    CLAIM_NO_RECORD,
    CLAIM_NOT_REVIEWED,
    CLAIM_TERMS_READ,
    TERMS_NOT_RECORDED,
    TermsRecord,
    disclosure_sentence,
    instrument_venue,
    terms_for_instrument,
    terms_for_provider,
    venue_labels,
)
from app.models.audit_event import AuditEvent
from app.models.dataset import Dataset
from app.services.dataset_registry import _license_declaration
from app.services.strategy_lab import build_plan, run_lab
from app.services.strategy_research import run_study
from tests.conftest import TestSession
from tests.test_strategy_lab import NO_COSTS, _flat_dataset
from tests.test_strategy_research import _make_bars

DATASETS_URL = "/api/v1/datasets"
KRAKEN_SENTENCE = "NOT_CLEARED_FOR_EXTERNAL_REDISTRIBUTION"
INTERNAL_TEST_SENTENCE = "INTERNAL_TEST_ONLY_NOT_CLEARED_FOR_REDISTRIBUTION"
# The claim the three lanes used to make about a source they had not looked up.
UNSOURCED_EN = "Market-data redistribution is NOT_CLEARED"
UNSOURCED_ZH = "行情資料對外重新分發尚未取得授權"


def _record(**overrides) -> TermsRecord:
    base = dict(
        provider_id="venuex",
        provider_name="Venue X",
        redistribution="NOT_CLEARED_FOR_EXTERNAL_REDISTRIBUTION",
        use="internal_research_and_simulated_fills",
        note_zh=None,
        disclaimer=None,
        disclaimer_zh=None,
        terms_source=None,
        reviewed_at=None,
        reviewed_by=None,
        recorded=True,
    )
    base.update(overrides)
    return TermsRecord(**base)


def _dataset_state():
    db = TestSession()
    try:
        return (
            db.query(Dataset).count(),
            db.query(AuditEvent).filter(AuditEvent.event_type == "dataset_created").count(),
        )
    finally:
        db.close()


class TestRegistryIsKeyedByTheSource:
    def test_kraken_and_twelvedata_get_their_own_records(self):
        assert terms_for_provider("kraken").redistribution == KRAKEN_SENTENCE
        assert terms_for_provider("twelvedata").redistribution == INTERNAL_TEST_SENTENCE
        # Each sentence names the source it belongs to, so a reader cannot move
        # one sentence onto the other lane by accident.
        assert "Kraken" in terms_for_provider("kraken").note_zh
        assert "Kraken" not in terms_for_provider("twelvedata").note_zh

    def test_lookup_normalises_case_and_padding(self):
        assert terms_for_provider("  KRAKEN ") is terms_for_provider("kraken")

    def test_unrecorded_venue_gets_no_neighbour_sentence(self):
        record = terms_for_provider("bitstamp")
        assert record.redistribution == TERMS_NOT_RECORDED
        assert record.recorded is False
        assert record.provider_name is None
        assert record.use is None
        assert record.note_zh is None
        assert record.disclaimer is None
        assert record.claim_basis == CLAIM_NO_RECORD
        # The id still names the venue that was asked about.
        assert record.provenance()["terms_provider_id"] == "bitstamp"

    def test_empty_lookup_does_not_borrow_the_first_record(self):
        assert terms_for_provider(None).redistribution == TERMS_NOT_RECORDED
        assert terms_for_provider("").redistribution == TERMS_NOT_RECORDED

    def test_no_record_on_the_tree_claims_a_document_was_read(self):
        """The unsourced-claim half of the defect, measured over the registry."""
        from app.data import market_terms

        assert market_terms._RECORDS, "the registry must not be empty"
        for provider_id, record in market_terms._RECORDS.items():
            assert record.recorded is True
            assert record.sourced is False, provider_id
            assert record.claim_basis == CLAIM_NOT_REVIEWED, provider_id
            assert record.terms_source is None, provider_id
            assert record.reviewed_at is None, provider_id
            # Fail-closed: nothing in the registry says the data may be published.
            assert "NOT_CLEARED" in record.redistribution, provider_id

    def test_the_reviewed_branch_can_actually_fire(self):
        """Positive control for claim_basis: without it the PASS above is blind."""
        read = _record(terms_source="vendor-terms.pdf#4.2", reviewed_at="2026-11-25")
        assert read.claim_basis == CLAIM_TERMS_READ
        assert read.sourced is True
        assert read.provenance()["terms_source"] == "vendor-terms.pdf#4.2"
        # Half a citation is not a citation.
        assert _record(reviewed_at="2026-11-25").claim_basis == CLAIM_NOT_REVIEWED
        assert _record(terms_source="vendor-terms.pdf").claim_basis == CLAIM_NOT_REVIEWED
        # An unrecorded provider is reported as such even if it carries dates.
        unrecorded = _record(recorded=False, terms_source="x", reviewed_at="y")
        assert unrecorded.claim_basis == CLAIM_NO_RECORD

    def test_payload_always_carries_the_basis_with_the_status(self):
        payload = terms_for_provider("kraken").payload()
        assert payload["redistribution"] == KRAKEN_SENTENCE
        for key in (
            "terms_provider_id",
            "terms_recorded",
            "terms_source",
            "terms_reviewed_at",
            "terms_reviewed_by",
            "terms_claim_basis",
        ):
            assert key in payload, key


class TestCryptoRowsKeyOnTheirOwnVenue:
    def test_terms_follow_the_instruments_own_identity(self):
        assert terms_for_instrument(KRAKEN_BTC_USD).redistribution == KRAKEN_SENTENCE
        foreign = dataclasses.replace(KRAKEN_BTC_USD, venue="COINBASE")
        record = terms_for_instrument(foreign)
        assert record.redistribution == TERMS_NOT_RECORDED
        assert record.note_zh is None
        assert record.provider_name is None

    def test_row_payload_reports_the_row_venue_not_the_module(self):
        foreign = dataclasses.replace(KRAKEN_BTC_USD, venue="COINBASE")
        row = instruments_api._crypto_instrument_payload(foreign)
        terms = row["terms"]
        assert terms["venue"] == "COINBASE"
        assert terms["redistribution"] == TERMS_NOT_RECORDED
        assert terms["terms_claim_basis"] == CLAIM_NO_RECORD
        # The Kraken-only availability figure may not travel either.
        note = row["data_window"]["availability_note"]
        assert note == instruments_api.NO_WINDOW_MEASURED_NOTE
        assert "Kraken serves" not in note
        assert "not this venue" in note

    def test_declared_catalog_is_one_venue_so_the_sentence_is_legal(self):
        assert venue_labels(CRYPTO_INSTRUMENTS) == ["KRAKEN"]
        catalog = instruments_api._catalog_terms(CRYPTO_INSTRUMENTS)
        assert catalog["redistribution"] == KRAKEN_SENTENCE
        assert catalog["provider_id"] == "kraken"
        assert catalog["provenance"]["terms_claim_basis"] == CLAIM_NOT_REVIEWED

    def test_mixed_venue_catalog_declares_no_sentence(self):
        foreign = dataclasses.replace(KRAKEN_BTC_USD, venue="COINBASE")
        catalog = instruments_api._catalog_terms([KRAKEN_BTC_USD, foreign])
        assert catalog["venues"] == ["COINBASE", "KRAKEN"]
        for key in ("provider", "provider_id", "redistribution", "disclaimer"):
            assert catalog[key] is None, key
        assert catalog["provenance"]["terms_claim_basis"] == "catalog_spans_multiple_venues"

    def test_empty_catalog_declares_no_sentence(self):
        catalog = instruments_api._catalog_terms([])
        assert catalog["redistribution"] is None
        assert catalog["provenance"]["terms_claim_basis"] == "catalog_contains_no_instrument"

    def test_venue_key_prefers_the_identity_segment(self):
        assert instrument_venue(KRAKEN_BTC_USD) == "KRAKEN"
        assert instrument_venue(dataclasses.replace(KRAKEN_BTC_USD, venue=None)) == "KRAKEN"
        assert instrument_venue(object()) is None


class TestLicensedDatasetNeedsALicence:
    @pytest.mark.parametrize("license_status", [None, "", "   ", "\t"])
    def test_licensed_without_a_licence_is_refused(
        self, client, admin_headers, license_status
    ):
        before = _dataset_state()
        resp = client.post(
            DATASETS_URL,
            json={
                "name": "Claimed licensed set",
                "provider": "vendor",
                "source_type": "licensed",
                "license_status": license_status,
            },
            headers=admin_headers,
        )
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert detail["reason"] == "licensed_source_requires_license_status"
        assert detail["source_type"] == "licensed"
        # Rejection must not write a row or an audit event.
        assert _dataset_state() == before

    def test_licensed_with_a_named_licence_is_accepted(self, client, admin_headers):
        before = _dataset_state()
        resp = client.post(
            DATASETS_URL,
            json={
                "name": "Licensed set",
                "provider": "vendor",
                "source_type": "licensed",
                "license_status": "Vendor agreement 2026-04, internal use only",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["license_status"].startswith("Vendor agreement")
        rows, events = _dataset_state()
        assert (rows, events) == (before[0] + 1, before[1] + 1)

    @pytest.mark.parametrize("source_type", ["demo", "licensed"])
    def test_the_gate_is_scoped_to_the_rights_claim_not_to_all_writes(
        self, client, admin_headers, source_type
    ):
        """``demo`` keeps its NULL: the refusal fires only where a licence is claimed."""
        payload = {"name": "Set", "provider": "vendor", "source_type": source_type}
        if source_type == "licensed":
            payload["license_status"] = "Signed licence, internal use"
        resp = client.post(DATASETS_URL, json=payload, headers=admin_headers)
        assert resp.status_code == 201, resp.text
        expected = None if source_type == "demo" else "Signed licence, internal use"
        assert resp.json()["license_status"] == expected

    def test_non_admin_still_cannot_write_at_all(self, client, auth_headers):
        resp = client.post(
            DATASETS_URL,
            json={
                "name": "Set",
                "provider": "vendor",
                "source_type": "licensed",
                "license_status": "anything",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 403


class TestStoredLineageDeclaresItsRights:
    def test_single_provider_row_names_the_status_and_basis(self):
        assert _license_declaration(["kraken"]) == (
            f"{KRAKEN_SENTENCE};claim_basis={CLAIM_NOT_REVIEWED}"
        )

    def test_unrecorded_provider_is_not_covered_by_a_neighbour(self):
        assert _license_declaration(["synthetic"]) == (
            f"{TERMS_NOT_RECORDED};claim_basis={CLAIM_NO_RECORD}"
        )

    def test_multi_provider_row_declares_that_no_single_record_applies(self):
        declaration = _license_declaration(["kraken", "twelvedata"])
        assert declaration == "MULTI_PROVIDER_NO_SINGLE_TERMS_RECORD:kraken,twelvedata"
        # Neither venue's clearance is quoted over the other.
        assert KRAKEN_SENTENCE not in declaration
        assert INTERNAL_TEST_SENTENCE not in declaration

    def test_unknown_provider_degrades_rather_than_defaulting_to_kraken(self):
        assert _license_declaration(["unknown"]) != KRAKEN_SENTENCE


def _lab_run(provider_id: str) -> dict:
    """One lab run on fixed bars, in which only the declared source changes."""
    dataset = dataclasses.replace(_flat_dataset(), provider_id=provider_id)
    return run_lab(dataset, settings=NO_COSTS)


class TestLaneDisclosuresKeyOnTheirSource:
    def test_lab_quotes_the_provider_that_served_the_bars(self):
        payload = _lab_run("kraken")
        assert "Kraken" in payload["disclosures"][-1]
        assert "not cleared for external redistribution" in payload["disclosures"][-1]
        assert "Kraken" in payload["disclosures_zh"][-1]
        assert len(payload["disclosures"]) == len(payload["disclosures_zh"])

    def test_lab_does_not_lend_kraken_words_to_an_unrecorded_source(self):
        kraken_line = _lab_run("kraken")["disclosures"][-1]
        payload = _lab_run("warehouse-x")
        sentence = payload["disclosures"][-1]
        # Same bars, same settings: only the declared source moved, so a
        # sentence that did not move would be prose rather than a lookup.
        assert sentence != kraken_line
        assert TERMS_NOT_RECORDED in sentence
        assert "warehouse-x" in sentence
        assert "Kraken" not in sentence
        assert "Kraken" not in payload["disclosures_zh"][-1]

    def test_lab_uses_the_second_recorded_provider_s_own_sentence(self):
        payload = _lab_run("twelvedata")
        assert INTERNAL_TEST_SENTENCE in payload["disclosures"][-1]
        assert "Twelve Data" in payload["disclosures"][-1]
        assert "Kraken" not in payload["disclosures"][-1]
        assert "Kraken" not in payload["disclosures_zh"][-1]

    def test_a_blank_source_admits_it_does_not_know(self):
        sentence = _lab_run("")["disclosures"][-1]
        assert TERMS_NOT_RECORDED in sentence
        assert "Kraken" not in sentence

    def test_plan_sentence_agrees_with_the_source_the_plan_declares(self):
        plan = build_plan()
        assert instrument_venue(get_crypto_instrument(plan["data_plan"]["instrument_id"])) == "KRAKEN"
        assert "Kraken" in plan["limitations"][-1]
        assert "Kraken" in plan["limitations_zh"][-1]
        assert len(plan["limitations"]) == len(plan["limitations_zh"])

    def test_the_plan_lookup_fails_closed_if_the_catalog_loses_that_instrument(self):
        # build_plan keys on the id it declares, so what happens when the
        # catalog stops knowing that id has to be reachable — and must not be
        # the previous venue's sentence.
        ghost = disclosure_sentence(
            instrument_venue(get_crypto_instrument("CRYPTO:GHOST:BTC-USD"))
        )
        assert TERMS_NOT_RECORDED in ghost[0]
        assert "Kraken" not in ghost[0]
        assert "Kraken" not in ghost[1]

    def test_study_lane_states_that_it_cannot_see_the_source(self):
        payload = run_study(_make_bars(320), folds=4)
        sentence = payload["disclosures"][-1]
        assert TERMS_NOT_RECORDED in sentence
        assert "Kraken" not in sentence
        assert len(payload["disclosures"]) == len(payload["disclosures_zh"])

    def test_the_hand_typed_claim_is_gone_from_all_three_lanes(self):
        lab = _lab_run("kraken")
        plan = build_plan()
        study = run_study(_make_bars(320), folds=4)
        blobs = {
            "strategy-lab": " ".join([*lab["disclosures"], *lab["disclosures_zh"]]),
            "strategy-lab-plan": " ".join(
                [*plan["limitations"], *plan["limitations_zh"]]
            ),
            "strategy-research": " ".join([*study["disclosures"], *study["disclosures_zh"]]),
        }
        for name, blob in blobs.items():
            assert UNSOURCED_EN not in blob, name
            assert UNSOURCED_ZH not in blob, name
            # Should-match control: the scan is not blind — each lane still
            # says something about redistribution, just a sourced one.
            assert "redistribution" in blob.lower() or "重新分發" in blob, name
