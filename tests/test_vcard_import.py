"""vCard (.vcf) address-book import — tier-1 edges + phone-name resolution."""
import pytest

from app.ingest import vcard
from app.ingest.vcard import ingest_vcf
from app.models import Person, RelationshipEdge
from app.providers import trestle

# A real-ish export: folded lines, an Apple grouped item.TEL, a structured N with
# no FN, a `tel:`-prefixed URI value, a contact with a number but no name, and a
# bare BEGIN/END with nothing usable.
EXPORT = '''BEGIN:VCARD
VERSION:3.0
FN:Charles Hudson
ORG:Precursor Ventures
TITLE:Managing Partner
item1.TEL;type=CELL:(415) 555-0100
EMAIL:charles@example.com
END:VCARD
BEGIN:VCARD
VERSION:4.0
N:Ling;Ada;;;
TEL;TYPE=cell;VALUE=uri:tel:+14155550111
END:VCARD
BEGIN:VCARD
VERSION:3.0
TEL;TYPE=CELL:+1 415-555-0199
END:VCARD
BEGIN:VCARD
VERSION:3.0
END:VCARD
'''


@pytest.fixture(autouse=True)
def _no_trestle(monkeypatch):
    """Default: reverse lookup finds nothing (no key configured in tests)."""
    monkeypatch.setattr(trestle, "reverse_phone", lambda number: None)


def test_named_contacts_become_tier1_first_degree_links(db):
    result = ingest_vcf(db, EXPORT, owner_name="Drew Glover")

    names = [p["name"] for p in result["people"]]
    assert "Charles Hudson" in names
    assert "Ada Ling" in names          # assembled from the structured N field
    # Every returned link is a real edge from the owner.
    assert result["edges"] == len(result["people"])
    assert db.query(RelationshipEdge).filter_by(
        relationship_type="address_book").count() == result["edges"]


def test_pick_rows_carry_the_detail_the_ui_shows(db):
    people = ingest_vcf(db, EXPORT, owner_name="Drew Glover")["people"]
    charles = next(p for p in people if p["name"] == "Charles Hudson")
    assert charles["company"] == "Precursor Ventures"
    assert charles["title"] == "Managing Partner"
    assert charles["resolved"] == "name"


def test_nameless_number_falls_back_to_unknown_placeholder(db):
    result = ingest_vcf(db, EXPORT, owner_name="Drew Glover")

    assert result["unresolved"] == 1
    assert result["resolved_via_phone"] == 0
    # The bare number is kept as a named placeholder, still first-degree.
    unknowns = [p for p in result["people"] if p["resolved"] == "unknown"]
    assert len(unknowns) == 1
    assert "4155550199" in unknowns[0]["name"].replace(" ", "")
    # The empty card anchors nothing.
    assert result["skipped"] == 1


def test_trestle_resolves_a_nameless_number_to_a_name(db, monkeypatch):
    monkeypatch.setattr(trestle, "reverse_phone",
                        lambda number: "Jane Investor" if "0199" in number else None)
    result = ingest_vcf(db, EXPORT, owner_name="Drew Glover")

    assert result["resolved_via_phone"] == 1
    assert result["unresolved"] == 0
    assert any(p["name"] == "Jane Investor" and p["resolved"] == "phone"
               for p in result["people"])


def test_reimport_adds_no_duplicate_links(db):
    first = ingest_vcf(db, EXPORT, owner_name="Drew Glover")
    second = ingest_vcf(db, EXPORT, owner_name="Drew Glover")

    # The link count is re-reported, but no new rows are written.
    assert second["edges"] == first["edges"]
    assert db.query(RelationshipEdge).filter_by(
        relationship_type="address_book").count() == first["edges"]


def test_empty_input_reports_an_error(db):
    result = ingest_vcf(db, "", owner_name="Drew Glover")
    assert result["error"] == "no vCard entries found"
    assert result["people"] == []


# --- pure-unit coverage of the tricky parsing / resolution helpers ---------
def test_line_folding_rejoins_wrapped_values():
    cards = vcard._parse_cards(
        "BEGIN:VCARD\nFN:Alexandra\n  Rivera-Long\nEND:VCARD\n")
    assert cards[0]["fn"] == "Alexandra Rivera-Long"


def test_normalize_number_defaults_us_country_code():
    assert trestle.normalize_number("(415) 555-0100") == "+14155550100"
    assert trestle.normalize_number("+44 20 7946 0000") == "+442079460000"
    assert trestle.normalize_number("1-415-555-0100") == "+14155550100"


def test_best_owner_name_prefers_a_person_over_a_business():
    payload = {"owners": [
        {"name": "Acme LLC", "type": "Business"},
        {"name": "Dana Lee", "type": "Person"},
    ]}
    assert trestle._best_owner_name(payload) == "Dana Lee"
