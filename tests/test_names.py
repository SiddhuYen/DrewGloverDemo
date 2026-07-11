"""Deterministic name filtering: the junk set prunes, the real set survives."""
import pytest

from app.edges.names import (
    is_noise_name,
    looks_like_person_name,
    org_norm_key,
    person_norm_key,
    strip_role_affixes,
)
from app.graph.builder import clean_person_names

JUNK = [
    "Drew Glover - LinkedIn",       # page-title artifact (spaced separator)
    "Drew Glover | CEO.com",        # separator + domain
    "Drew Glover - CEO.com",
    "https://example.com/drew",     # embedded URL
    "www.fiat.vc",
    "@drewglover",                  # social handle
    "Cookie Policy",
    "Privacy Policy",
    "Terms of Service",
    "Alex, Drew",                   # comma-joined fragment
    "Harris, Alex",                 # inverted name
    "जुलाई",                         # no cased characters
    "Sign In",
    "Read More",
]

REAL = [
    "Marcos Fernandez",
    "Jean-Luc Picard",
    "Drew Glover",
    "Bree Hanson",
    "Vikram Lakhwara",
    "Tae Hea Nahm",
    "Kate Shillo Beardsley",
    "Lan Xuezhao",
]


@pytest.mark.parametrize("name", JUNK)
def test_junk_is_pruned(name):
    assert clean_person_names([name]) == [], f"{name!r} should be pruned"


@pytest.mark.parametrize("name", REAL)
def test_real_names_survive(name):
    assert clean_person_names([name]) == [name], f"{name!r} should survive"


@pytest.mark.parametrize("raw,expected", [
    # Regression: a roster that glues the title onto the name must NOT lose the
    # person. Rejecting these silently deleted Drew's two real co-founders.
    ("Partner Alex Harris", "Alex Harris"),
    ("Partner Marcos Fernandez", "Marcos Fernandez"),
    ("General Partner Drew Glover", "Drew Glover"),
    ("Abhay Mavalankar SVP", "Abhay Mavalankar"),
    ("Managing Director Jane Roe", "Jane Roe"),
    ("Dr. John Smith", "John Smith"),
    # Hyphenated titles: normalize() splits "Co-Founder" into two words, so the
    # single-token affix set missed it and Hustle Fund's roster scraped as
    # "Elizabeth Yin Co-Founder".
    ("Elizabeth Yin Co-Founder", "Elizabeth Yin"),
    ("Shiyan Koh Co-Founder", "Shiyan Koh"),
    ("Co-Founder Eric Bahn", "Eric Bahn"),
])
def test_role_affixes_are_stripped_not_rejected(raw, expected):
    assert strip_role_affixes(raw) == expected
    assert clean_person_names([raw]) == [expected]


ROLE_TITLES = [
    # Regression: each of these was scraped from a firm's /team page and became a
    # PERSON node. Because dedup is by normalised name, one node was reused for
    # every firm printing the role — "Executive Assistant" alone accrued 57 edges
    # bridging Foundry Group, Wing, Uncork and Framework Ventures.
    "Executive Assistant",
    "Chief Executive Assistant",
    "Board Member",
    "Executive Vice",
    "Senior Advisory",
    "Staff Engineer",
    "Finance Manager",
    "Managing Director",
    "Chief Of Staff",
    "Team Member",
    "Current EIR",
    "Entrepreneur In Residence",
    "Program Manager",
    "Product Designer",
]


@pytest.mark.parametrize("role", ROLE_TITLES)
def test_a_name_of_only_role_words_is_not_a_person(role):
    assert is_noise_name(role)
    assert clean_person_names([role]) == []


@pytest.mark.parametrize("name", [
    # One role-ish token does not condemn a name: the test is that EVERY token
    # is a role word. A person may be surnamed Fellow, Board or Chief.
    "Marcus Fellow",
    "Jane Board",
    "Chief Nakamura",
    "Sarah Staff",
    "Ira Matthew Ehrenpreis",
    "Joseph Gebbia",
])
def test_one_role_token_does_not_condemn_a_real_name(name):
    assert not is_noise_name(name)
    assert clean_person_names([name]) == [name]


def test_noise_name_flags():
    assert is_noise_name("Drew Glover - LinkedIn")
    assert is_noise_name("Alex, Drew")
    assert not is_noise_name("Drew Glover")


def test_looks_like_person_name_rejects_role_words_and_orgs():
    assert not looks_like_person_name("Partner Jason Calacanis")  # interior role word
    assert not looks_like_person_name("Fiat Ventures")            # org suffix
    assert not looks_like_person_name("Drew")                     # single token
    assert looks_like_person_name("Jason Calacanis")


def test_person_key_collapses_diminutives_and_initials():
    assert person_norm_key("Tim Cook") == person_norm_key("Timothy Cook")
    assert person_norm_key("John F. Kennedy") == person_norm_key("John Kennedy")


def test_person_key_separates_generational_suffix():
    # "Charles Hudson" and "Hudson Charles E. III" are different people.
    assert person_norm_key("Charles Hudson") != person_norm_key("Charles Hudson III")


def test_org_key_strips_legal_suffixes():
    assert org_norm_key("Acme Inc.") == org_norm_key("Acme Corporation") == "acme"
    assert org_norm_key("Fiat Ventures") == "fiat ventures"


def test_clean_person_names_dedups():
    assert clean_person_names(["Tim Cook", "Timothy Cook"]) == ["Tim Cook"]


@pytest.mark.parametrize("heading", [
    # Regression: Bonfire's /team page yielded exactly one "person" —
    # "Investment Criteria". It is PROPN PROPN, grammatically identical to
    # "Mary Nwokocha", so only a lexicon separates them.
    "Investment Criteria",
    "Key Performance Indicator",
    "Our Approach",
    "Pitch Us",
    "Frequently Asked Questions",
])
def test_site_navigation_headings_are_not_people(heading):
    assert clean_person_names([heading]) == []
