"""LinkedIn CSV import — the roster it hands the UI to pick enrichment from."""
from app.ingest.linkedin_csv import ingest_csv
from app.models import RelationshipEdge

# A real export: a "Notes:" preamble, a repeat row for one person (a job change),
# and a row with no name at all.
EXPORT = '''Notes:
"Some of the fields may be empty."

First Name,Last Name,URL,Email Address,Company,Position
Charles,Hudson,https://linkedin.com/in/ch,,Precursor Ventures,Managing Partner
Ada,Ling,,ada@example.com,Sequoia Capital,Partner
Charles,Hudson,https://linkedin.com/in/ch,,Precursor Ventures,Managing Partner
,,,,Nameless Corp,Ghost
'''


def test_import_returns_one_pick_row_per_person_not_per_row(db):
    result = ingest_csv(db, EXPORT, owner_name="Drew Glover")

    # Two people, though Charles is listed twice and a nameless row exists.
    assert [p["name"] for p in result["people"]] == ["Charles Hudson", "Ada Ling"]
    assert result["skipped"] == 1

    # The repeat must not inflate the link count past the people actually linked.
    assert result["edges"] == 2
    assert db.query(RelationshipEdge).count() == 2


def test_pick_rows_carry_the_detail_the_ui_shows(db):
    people = ingest_csv(db, EXPORT, owner_name="Drew Glover")["people"]

    assert people[0] == {"name": "Charles Hudson", "company": "Precursor Ventures",
                         "title": "Managing Partner", "enriched": False}
    # Nobody is enriched by importing — that stays the user's explicit choice.
    assert all(p["enriched"] is False for p in people)


def test_reimporting_the_same_export_adds_no_duplicate_links(db):
    first = ingest_csv(db, EXPORT, owner_name="Drew Glover")
    second = ingest_csv(db, EXPORT, owner_name="Drew Glover")

    assert second["edges"] == first["edges"] == 2
    assert db.query(RelationshipEdge).count() == 2
    assert [p["name"] for p in second["people"]] == ["Charles Hudson", "Ada Ling"]


def test_headerless_csv_reports_an_error_and_an_empty_roster(db):
    result = ingest_csv(db, "", owner_name="Drew Glover")

    assert result["error"] == "empty or headerless CSV"
    assert result["people"] == []
