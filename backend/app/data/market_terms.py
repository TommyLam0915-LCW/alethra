"""Market-data terms records, keyed by the source whose terms they describe.

A rights sentence is a claim about a contract with somebody. Two ways to get it
wrong are visible in this repository's history:

* One constant reused for every instrument. The crypto catalog used to print
  "Kraken 公開行情…" over each row it returned, whatever venue that row's own
  ``exchange`` named. The sentence was true of one venue and attached to all of
  them, which is how a venue that has never been read ends up covered by
  somebody else's clearance.
* A status with no source behind it. ``NOT_CLEARED_FOR_EXTERNAL_REDISTRIBUTION``
  is the correct fail-closed answer when nobody has read the terms — but a
  payload that says only that leaves a reader unable to tell "we checked and it
  is not cleared" from "we have not checked". This module carries both halves:
  the status, and the document/date/reviewer the status was derived from, which
  are ``None`` here because no market-data terms document has been read.

So the rule the rest of the codebase follows: ask for the record of the provider
that actually served the data, and emit the provenance with the status. A
provider with no record gets ``TERMS_NOT_RECORDED``, never a neighbour's sentence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional


#: Emitted when the provider serving a series has no terms record at all. This
#: is not a rights determination; it says this platform has not recorded one.
TERMS_NOT_RECORDED = "TERMS_NOT_RECORDED"

#: What the status rests on. Only the first is a reviewed claim, and nothing in
#: this module currently reaches it.
CLAIM_TERMS_READ = "terms_document_read"
CLAIM_NOT_REVIEWED = "market_data_terms_never_reviewed"
CLAIM_NO_RECORD = "no_terms_record_for_this_provider"


@dataclass(frozen=True)
class TermsRecord:
    provider_id: str
    provider_name: Optional[str]
    redistribution: str
    use: Optional[str]
    note_zh: Optional[str]
    disclaimer: Optional[str]
    disclaimer_zh: Optional[str]
    terms_source: Optional[str]
    reviewed_at: Optional[str]
    reviewed_by: Optional[str]
    recorded: bool

    @property
    def claim_basis(self) -> str:
        if not self.recorded:
            return CLAIM_NO_RECORD
        if self.terms_source and self.reviewed_at:
            return CLAIM_TERMS_READ
        return CLAIM_NOT_REVIEWED

    @property
    def sourced(self) -> bool:
        """True only when the status names the document it was read from."""
        return self.claim_basis == CLAIM_TERMS_READ

    def provenance(self) -> Dict[str, Any]:
        """The fields that let a reader judge the claim instead of trusting it."""
        return {
            "terms_provider_id": self.provider_id,
            "terms_recorded": self.recorded,
            "terms_source": self.terms_source,
            "terms_reviewed_at": self.reviewed_at,
            "terms_reviewed_by": self.reviewed_by,
            "terms_claim_basis": self.claim_basis,
        }

    def payload(self) -> Dict[str, Any]:
        return {"redistribution": self.redistribution, **self.provenance()}


def _unrecorded(provider_id: Optional[str]) -> TermsRecord:
    return TermsRecord(
        provider_id=provider_id or "unknown",
        provider_name=None,
        redistribution=TERMS_NOT_RECORDED,
        use=None,
        note_zh=None,
        disclaimer=None,
        disclaimer_zh=None,
        terms_source=None,
        reviewed_at=None,
        reviewed_by=None,
        recorded=False,
    )


#: Kraken public spot. The three sentences below are the strings this lane has
#: been serving, moved here so they live in exactly one place and are attached to
#: exactly one venue. ``terms_source`` is None because no Kraken market-data
#: terms document has been read: what is actually known is that display rights
#: have not been checked, so the fail-closed status stands.
_KRAKEN = TermsRecord(
    provider_id="kraken",
    provider_name="Kraken",
    redistribution="NOT_CLEARED_FOR_EXTERNAL_REDISTRIBUTION",
    use="internal_research_and_simulated_fills",
    note_zh="Kraken 公開行情只供本機／內部研究及模擬交易使用，未經核對不得對外重新分發。",
    disclaimer=(
        "Live public venue data (Kraken). Internal research and simulated "
        "fills only — not cleared for external redistribution."
    ),
    disclaimer_zh="Kraken 公開即時行情，只供內部研究及模擬成交，未經核可不得對外重新分發。",
    terms_source=None,
    reviewed_at=None,
    reviewed_by=None,
    recorded=True,
)

#: The REST intraday lane's provider. An API key is an access entitlement, not a
#: publication licence, which is the whole content of this record.
_TWELVEDATA = TermsRecord(
    provider_id="twelvedata",
    provider_name="Twelve Data",
    redistribution="INTERNAL_TEST_ONLY_NOT_CLEARED_FOR_REDISTRIBUTION",
    use="internal_test_only",
    note_zh=(
        "現階段只供已授權之本機／內部測試使用。擁有 API key 不等於可對外分發行情；"
        "任何公開頁面或收費展示前，須先取得該供應商之市場數據授權。"
    ),
    disclaimer=None,
    disclaimer_zh=None,
    terms_source=None,
    reviewed_at=None,
    reviewed_by=None,
    recorded=True,
)

_RECORDS: Dict[str, TermsRecord] = {
    record.provider_id: record for record in (_KRAKEN, _TWELVEDATA)
}


def terms_for_provider(provider_id: Optional[str]) -> TermsRecord:
    """The recorded terms for one provider, or an explicit no-record."""
    record = _RECORDS.get((provider_id or "").strip().lower())
    return record if record is not None else _unrecorded(provider_id)


def instrument_venue(instrument: Any) -> Optional[str]:
    """The venue a crypto instrument is identified by, in its own words.

    ``venue`` is the segment that spells the instrument id
    (``CRYPTO:KRAKEN:BTC-USD``), so it is the key that travels with the price;
    ``exchange`` is the enum it is built from. Prefering the first means a row
    quoted on a venue this module has never heard of is looked up under *that*
    name, not under whichever enum member happened to be compiled in.
    """
    venue = getattr(instrument, "venue", None)
    if venue:
        return str(venue)
    exchange = getattr(instrument, "exchange", None)
    value = getattr(exchange, "value", exchange)
    return str(value) if value else None


def terms_for_instrument(instrument: Any) -> TermsRecord:
    """Terms keyed on the venue the instrument itself names.

    The venue is part of a crypto instrument id, so it is the only honest key
    available here; reading a module constant instead would let one venue's
    clearance cover another venue's prices.
    """
    return terms_for_provider((instrument_venue(instrument) or "").lower() or None)


def venue_labels(instruments: Iterable[Any]) -> list:
    """Distinct venues actually present in a catalog, sorted and stable."""
    return sorted({label for label in map(instrument_venue, instruments) if label})


def disclosure_sentence(provider_id: Optional[str]) -> tuple:
    """The rights line a lane puts in its own ``disclosures`` list.

    Research lanes used to write one hand-typed sentence — "Market-data
    redistribution is NOT_CLEARED" — whatever their bars' source turned out to
    be, so the same three words spoke for Kraken, for a warehouse and for a
    synthetic feed at once. This returns the sentence of the provider actually
    named, and when no provider can be named it says that the terms were never
    read instead of lending somebody else's clearance.
    """
    if provider_id is None or not str(provider_id).strip():
        return (
            f"The source of these bars is not identified to this lane, so its "
            f"market-data terms are {TERMS_NOT_RECORDED}: no redistribution right "
            "is claimed here.",
            f"本車道未識別這些資料之來源，其市場數據條款為 {TERMS_NOT_RECORDED}；"
            "此處不宣稱任何對外重新分發之權利。",
        )
    record = terms_for_provider(provider_id)
    if not record.recorded:
        return (
            f"Market-data terms for provider {record.provider_id!r} are "
            f"{TERMS_NOT_RECORDED}: they have never been read, so no "
            "redistribution right is claimed.",
            f"供應商「{record.provider_id}」之市場數據條款為 {TERMS_NOT_RECORDED}："
            "本平台從未核對該條款，因此不宣稱任何對外重新分發之權利。",
        )
    english = record.disclaimer or (
        f"Market-data redistribution for {record.provider_name} is "
        f"{record.redistribution}; internal research and simulated fills only."
    )
    chinese = record.disclaimer_zh or record.note_zh
    return english, chinese
