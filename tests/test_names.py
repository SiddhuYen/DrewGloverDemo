"""Deterministic name filtering: the junk set prunes, the real set survives."""
import pytest

from app.edges.names import (
    is_noise_name,
    looks_like_person_name,
    org_norm_key,
    person_norm_key,
    person_search_keys,
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


ORG_AS_PERSON = [
    # Regression: "Armchair Umbrella" is Dax Shepard's PRODUCTION COMPANY. It
    # passed the person-name shape test, became a podcast "host", and fused every
    # guest of the show — manufacturing a Bill Gates <-> Monica Lewinsky path.
    "Armchair Umbrella",
    "The Toboni Team",                 # a real-estate team brand
    "Jeff Earl Warren Real Estate Team",
    "fiat.ventures",                   # a firm handle, not a person
    "Andreessen Horowitz Media",
    "Dr. Maya Angelou Foundation",
    "Chef Lowell, LLC",
]


@pytest.mark.parametrize("name", ORG_AS_PERSON)
def test_an_organisation_name_is_not_a_person(name):
    assert is_noise_name(name)
    assert clean_person_names([name]) == []
    assert not looks_like_person_name(name)


TITLE_GLUED = [
    # A job title left in the INTERIOR of a scraped name (the ends are stripped,
    # so a title here means the string mashed an org + a person together).
    "Xbox Co-Founder Ed Fries",
    "Seth Levine Co-Author",
    "Samooha Co-Founder Kamakshi Sivaramakrishnan",
]


@pytest.mark.parametrize("name", TITLE_GLUED)
def test_a_title_glued_into_the_interior_of_a_name_is_rejected(name):
    assert is_noise_name(name)


def test_a_production_company_never_becomes_a_podcast_host():
    # The exact bug: looks_like_person_name gates who can be a host/guest.
    assert not looks_like_person_name("Armchair Umbrella")
    assert looks_like_person_name("Dax Shepard")       # the real host survives


@pytest.mark.parametrize("name", ["Co-Founder Jane Doe", "Ed Fries",
                                  "Sophia Amoruso", "David Neeleman"])
def test_org_rule_spares_real_people(name):
    # The person under a stripped edge-title, and plain real names, survive.
    assert not is_noise_name(strip_role_affixes(name))


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


# --- resolving a typed name -------------------------------------------------
#
# Regression: after importing a LinkedIn export, searching for someone ON that
# export returned "not in the graph". LinkedIn stores "José Álvarez", "Robert
# Chen Jr." and "Sheel Mohnot (BTV)"; people type "Jose Alvarez", "Robert Chen",
# "Sheel Mohnot". Six of nine realistic variants failed to resolve.

@pytest.mark.parametrize("typed, stored", [
    ("Jose Alvarez", "José Álvarez"),            # nobody types accents
    ("JOSE ALVAREZ", "José Álvarez"),
    ("Robert Chen", "Robert Chen Jr."),          # generational suffix
    ("John Smith", "John Andrew Smith"),         # spelled-out middle name
    ("John Smith", "John Smith, CFA"),           # credentials after a comma
    ("Mary Kate OBrien", "Mary-Kate O'Brien"),   # punctuation disagreement
    ("Sheel Mohnot", "Sheel Mohnot (BTV)"),      # parenthetical note
    ("sarah  kim", "Sarah Kim"),                 # sloppy whitespace
])
def test_typed_name_resolves_to_stored_name(typed, stored):
    assert person_search_keys(typed) & person_search_keys(stored)


@pytest.mark.parametrize("a, b", [
    ("John Smith", "Jane Smith"),
    ("Robert Chen", "Robert Cheng"),
    ("Bree Hanson", "Bree Hansen"),
    ("Sheel Mohnot", "Sheel Mehta"),
])
def test_different_people_do_not_resolve_to_each_other(a, b):
    assert not (person_search_keys(a) & person_search_keys(b))


def test_search_keys_never_loosen_the_dedup_key():
    """The strict key decides whether two NODES are one person, so it must stay
    strict — loosening it would merge strangers into a false bridge. Only the
    search key is allowed to be generous."""
    assert person_norm_key("Jose Alvarez") != person_norm_key("José Álvarez")
    assert person_search_keys("Jose Alvarez") & person_search_keys("José Álvarez")


@pytest.mark.parametrize("junk", ["", "   ", ",", "(BTV)", "Jr."])
def test_unnameable_input_yields_no_search_keys(junk):
    assert person_search_keys(junk) == set()
