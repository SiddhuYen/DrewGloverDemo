"""Provider parsing rules that protect edge quality. No network."""
import pytest

from app.providers.edgar import _filed_by, _person_display
from app.providers.firms import is_roster_url
from app.providers.podcasts import _guest_from_title, _hosts_from_author
from app.edges.names import person_norm_key


# --- podcasts: host must be a named human ---------------------------------
def test_named_human_hosts_are_split():
    assert _hosts_from_author("Bree Hanson & Vikram Lakhwara") == [
        "Bree Hanson", "Vikram Lakhwara"]


def test_a_company_host_yields_no_hosts():
    """Amplitude cannot be one end of a personal relationship, so its feed
    must produce no edges at all."""
    assert _hosts_from_author("Amplitude") == []
    assert _hosts_from_author("") == []


@pytest.mark.parametrize("title,guest", [
    ("DWAVC: Drew Glover | Ep. 37", "Drew Glover"),
    ("DWAVC Episode 38 | Tae Hea Nahm (Storm Ventures)", "Tae Hea Nahm"),
    ("DWAVC: Jeremy Kaufmann | Ep: 33  ", "Jeremy Kaufmann"),
    (" DWAVC: Matt McCall | Ep. 16", "Matt McCall"),
    ("DWALP?: Eric Sippel | Ep. 19", "Eric Sippel"),
    ("DWAVC: Kate Shillo Beardsley | Ep. 20", "Kate Shillo Beardsley"),
])
def test_guest_parses_out_of_episode_title(title, guest):
    assert _guest_from_title(title)["guest"] == guest


def test_guest_firm_is_captured_from_parentheses():
    assert _guest_from_title(
        "DWAVC Episode 38 | Tae Hea Nahm (Storm Ventures)")["org"] == "Storm Ventures"


@pytest.mark.parametrize("title", [
    "DrinksWithAVC: Best of (so far)",   # not a person
    "DWAVC: Ep. 12",                     # no guest
    "",
])
def test_non_person_titles_yield_no_guest(title):
    assert _guest_from_title(title) is None


# --- Layer C: a title must ASSERT a guest, not merely mention a name -------
@pytest.mark.parametrize("title", [
    # Company-history episodes. spaCy tags "Novo Nordisk" and "Lockheed Martin"
    # as PERSON, so no classifier saves us here — only the missing "with" does.
    "Acquired: Novo Nordisk",
    "Acquired: Lockheed Martin",
    "Acquired: Ferrari",
    "Acquired: Indian Premier League Cricket",
    # Headlines. These people are the TOPIC, never the guest. Minting an edge
    # from one would be co-occurrence in our warmest tier.
    "20VC: Sam Altman Offers Trump 5% of OpenAI: Fool or Genius?",
    "20VC: How We Got Fred Wilson, Benchmark and Index to Invest $94M",
    "20VC: Corgi Insurance",
])
def test_a_name_in_a_title_is_not_a_guest(title):
    from app.providers.podcasts import guest_by_marker
    assert guest_by_marker(title) is None
    # ...and with no template convention, the feed yields nothing from it.
    assert _guest_from_title(title, allow_template=False) is None


@pytest.mark.parametrize("title,guest", [
    ("10 Years of Acquired (with Michael Lewis)", "Michael Lewis"),
    ("Tech Shift Survival Guide with Aparna Sinha, SVP of Product", "Aparna Sinha"),
    ("When AI Meets Legal feat. Eleanor Lightbody, CEO of Luminance",
     "Eleanor Lightbody"),
])
def test_an_explicit_with_clause_asserts_a_guest(title, guest):
    from app.providers.podcasts import guest_by_marker
    assert guest_by_marker(title)["guest"] == guest


def test_topic_name_loses_to_the_with_clause():
    """"...How We Got Fred Wilson... to Invest | ... with Paul Erlanger" — the
    guest is the one the clause names, not the one the headline is about."""
    from app.providers.podcasts import guest_by_marker
    title = ("20VC: How We Got Fred Wilson, Benchmark and Index to Invest $94M "
             "| Scaling with Paul Erlanger")
    assert guest_by_marker(title)["guest"] == "Paul Erlanger"


def test_feed_template_is_detected_only_when_consistent():
    from app.providers.podcasts import _feed_uses_guest_template
    dwavc = [f"DWAVC: Person{i} Surname | Ep. {i}" for i in range(10)]
    assert _feed_uses_guest_template(dwavc)
    acquired = ["Ferrari", "Rolex", "Costco", "Vanguard", "The NFL"]
    assert not _feed_uses_guest_template(acquired)
    # One template-shaped title among many headlines proves nothing.
    assert not _feed_uses_guest_template(acquired + ["Show: Jane Doe | Ep. 1"])


# --- the description's opening bio asserts a guest -------------------------
@pytest.mark.parametrize("title,description,guest", [
    ("20VC: Sam Altman on The Trajectory of Model Capability Improvements",
     "Sam Altman is the CEO of OpenAI, one of the most important companies.",
     "Sam Altman"),
    ("20VC: Max Altman on The New Seed War",
     "Max Altman is Co-Founder & Managing Partner at Saga Ventures.",
     "Max Altman"),
    ("Jack Altman on going from Lattice to Alt Capital",
     "Jack Altman is the founder of Lattice and now runs Alt Capital.",
     "Jack Altman"),
])
def test_an_opening_bio_asserts_the_guest(title, description, guest):
    """20VC's dominant guest shape is "<Name> on <topic>", which alone asserts
    nothing. The description settles it."""
    from app.providers.podcasts import guest_by_bio
    assert guest_by_bio(title, description)["guest"] == guest


@pytest.mark.parametrize("title,description,guest", [
    # Regression: real long-form interviews whose title is not a clean name slot
    # were dropped, so Elon Musk had zero podcast appearances and stayed on his
    # SEC-filing island. The description OPENS with the guest's bio; read it there
    # and require the name to appear in the title.
    ("#2404 - Elon Musk",
     "Elon Musk is a business magnate, designer, and engineer.", "Elon Musk"),
    ('Elon Musk — "In 36 months"',
     "Elon Musk is the CEO of Tesla and SpaceX. In this deep dive...", "Elon Musk"),
    ("#2044 - Sam Altman",
     "Sam Altman is the CEO of OpenAI, one of the most important companies.",
     "Sam Altman"),
])
def test_a_title_format_guest_is_read_from_the_opening_bio(title, description, guest):
    from app.providers.podcasts import guest_by_bio
    assert guest_by_bio(title, description)["guest"] == guest


@pytest.mark.parametrize("title,description", [
    # A bio buried mid-description, or a title the person isn't the subject of,
    # must NOT pass the title-format fallback.
    ("Tesla replaces car lines with robots",
     "Elon Musk is a business magnate. Today Tesla replaced its lines."),
    ("Elon Musk vs Sam Altman: The Battle",
     "The rivalry between two AI titans escalates as OpenAI..."),
])
def test_bio_fallback_requires_the_subject_in_the_title(title, description):
    from app.providers.podcasts import guest_by_bio
    # "Tesla replaces car lines" doesn't contain "Elon Musk"; the vs-title has no
    # bio opening. Neither yields a guest.
    got = guest_by_bio(title, description)
    assert got is None or got["guest"] != "Elon Musk"


@pytest.mark.parametrize("title,description", [
    # News episodes: the description is a running order, not an introduction.
    ("20VC: Sam Altman Offers Trump 5% of OpenAI: Fool or Genius?",
     "AGENDA: 05:00 Washington Just Put Frontier AI on a Leash 06:30 Sam Altman's Idea"),
    ("20VC: GPT5: Sam Altman's Masterplan or a Gift To Anthropic",
     "AGENDA: 00:04 Was GPT-5 the Biggest AI Letdown Yet? 00:17 OpenAI's Real Target"),
    ("20VC: Sam Altman vs Elon Musk: The $100BN Battle",
     "AGENDA: 03:30 Can VC Survive With Public Market Prices Today"),
    # A bio buried deep is discussing him, not introducing him.
    ("20VC: The AI Bubble | Sam Altman on Scaling",
     "AGENDA: 00:00 intro 05:00 markets 10:00 bubbles 20:00 padding padding "
     "padding padding Sam Altman is the CEO of OpenAI"),
    ("20VC: Sam Altman on Scaling", ""),          # no description at all
])
def test_a_news_agenda_asserts_no_guest(title, description):
    from app.providers.podcasts import guest_by_bio
    assert guest_by_bio(title, description) is None


def test_episode_url_falls_back_to_the_enclosure_page():
    """Regression: DrinksWithAVC's items carry no <link>, so every citation
    pointed at the show's homepage instead of the episode being cited."""
    import xml.etree.ElementTree as ET
    from app.providers.podcasts import episode_url

    item = ET.fromstring(
        '<item><title>DWAVC: Drew Glover | Ep. 37</title>'
        '<enclosure url="https://www.buzzsprout.com/1525162/episodes/'
        '17361266-dwavc-drew-glover-ep-37.mp3"/></item>')
    assert episode_url(item, "https://show.example") == (
        "https://www.buzzsprout.com/1525162/episodes/17361266-dwavc-drew-glover-ep-37")


def test_episode_url_prefers_an_explicit_link():
    import xml.etree.ElementTree as ET
    from app.providers.podcasts import episode_url

    item = ET.fromstring('<item><link>https://show.example/ep1</link>'
                         '<enclosure url="https://cdn.example/a.mp3"/></item>')
    assert episode_url(item) == "https://show.example/ep1"


def test_episode_url_falls_back_to_the_show_when_nothing_is_available():
    import xml.etree.ElementTree as ET
    from app.providers.podcasts import episode_url

    item = ET.fromstring("<item><title>x</title></item>")
    assert episode_url(item, "https://show.example") == "https://show.example"


# --- Layer C: a show's own brand is not its host --------------------------
@pytest.mark.parametrize("author,show", [
    ("Riding Unicorns", "Riding Unicorns: Venture Capital | Entrepreneurship"),
    ("Startup Insider", "Startup Insider"),
    ("Amplitude", "Next Gen Builders"),
])
def test_a_brand_is_not_a_host(author, show):
    """"Riding Unicorns" is two proper nouns and passes every name and POS test.
    Only structure separates it from a person: the show starts with its name."""
    assert _hosts_from_author(author, show) == []


@pytest.mark.parametrize("author,show,hosts", [
    ("Bree Hanson & Vikram Lakhwara", "DrinksWithAVC (DWAVC)",
     ["Bree Hanson", "Vikram Lakhwara"]),
    ("Harry Stebbings", "The Twenty Minute VC (20VC)", ["Harry Stebbings"]),
    ("Ben Gilbert and David Rosenthal", "Acquired",
     ["Ben Gilbert", "David Rosenthal"]),
    # Her name appears in the show title but not as its prefix.
    ("Sarah Chen-Spellings", "Billion Dollar Moves with Sarah Chen-Spellings",
     ["Sarah Chen-Spellings"]),
])
def test_named_human_hosts_survive(author, show, hosts):
    assert _hosts_from_author(author, show) == hosts


# --- EDGAR: the filing must actually be FILED BY the person ---------------
@pytest.mark.parametrize("display,expected", [
    ("Benioff Marc  (CIK 0001294693)", "Marc Benioff"),
    ("Hudson Charles E. III  (CIK 0001823384)", "Charles E. Hudson Iii"),
    ("DONALD ARNOLD W  (CIK 0001083206)", "Arnold W Donald"),
])
def test_person_display_reorders_last_first(display, expected):
    assert _person_display(display) == expected


def test_filed_by_rejects_a_homonym():
    """Regression: EDGAR full-text search for "Charles Hudson" returns a Form 4
    filed by "Hudson Charles E. III" — an unrelated Joby Aviation insider.
    Accepting it wired a seed-stage VC into Joby's board (66 fabricated edges)."""
    display_names = ["Hudson Charles E. III  (CIK 0001823384)",
                     "Joby Aviation, Inc.  (JOBY, JOBY-WT)  (CIK 0001819848)"]
    assert not _filed_by(display_names, person_norm_key("Charles Hudson"))


def test_filed_by_accepts_the_real_filer():
    display_names = ["Benioff Marc  (CIK 0001294693)",
                     "Salesforce, Inc.  (CRM)  (CIK 0001108524)"]
    assert _filed_by(display_names, person_norm_key("Marc Benioff"))


# --- OpenAlex: coauthors, guarded against academic namesakes --------------
def test_openalex_declines_without_org_corroboration():
    """"Vinod Khosla" resolves to a prolific academic namesake. With no known
    org to corroborate identity, we must not merge the VC with the academic."""
    from app.providers.openalex import OpenAlexProvider
    o = OpenAlexProvider()
    # org_tokens empty -> _resolve_author declines outright, no network.
    assert o._resolve_author("Vinod Khosla", person_norm_key("Vinod Khosla"),
                             set()) is None


def test_openalex_org_tokens_drop_generic_words():
    from app.providers.openalex import _org_tokens
    assert _org_tokens(["Stanford University", "Insitro"]) == {"stanford", "insitro"}
    assert _org_tokens(["Khosla Ventures"]) == {"khosla"}


# --- ProPublica: officer names + the on-the-filing identity guard ----------
def test_propublica_declines_without_org_hints():
    from app.providers.propublica import ProPublicaProvider
    p = ProPublicaProvider()
    assert p.board_colleagues("Mark Suzman", org_hints=None) == []
    assert p.board_colleagues("Mark Suzman", org_hints=[]) == []


def test_propublica_officer_parsing(monkeypatch):
    """Names come from `tr.employee-row` cells; the trailing role is stripped."""
    from app.providers import propublica

    html = ('<table><tr class="employee-row"><td class="padded-right">'
            'Mark Suzman <span>(Chief Executive Officer, Board Member)</span>'
            '</td></tr>'
            '<tr class="employee-row"><td>Trevor Mundel (President)</td></tr>'
            '</table>')

    class R:
        status_code = 200
        text = html

    monkeypatch.setattr(propublica, "request_with_retry",
                        lambda *a, **k: R())
    officers = propublica.ProPublicaProvider()._officers("562618866")
    assert "Mark Suzman" in officers and "Trevor Mundel" in officers


# --- wikidata: family claims are direct pairwise ties ---------------------
def test_family_props_are_kept_and_are_person_targets():
    """Family claims (spouse/sibling/parent/child) point at another PERSON's
    QID, so each names both people — no Rule 1 cap, no org to scrape."""
    from app.providers import wikidata
    assert "P26" in wikidata._FAMILY_PROPS      # spouse
    assert "P3373" in wikidata._FAMILY_PROPS    # sibling
    # every family prop maps to a human phrase, none is an org relationship
    assert all(isinstance(v, str) for v in wikidata._FAMILY_PROPS.values())


def test_family_member_is_a_structural_tier_3_type():
    from app.edges import taxonomy
    assert taxonomy.is_structural("family_member")
    assert taxonomy.warmth_tier("family_member") == 3


# --- wikidata: a "member of" target must be a real body -------------------
@pytest.mark.parametrize("kinds,expected", [
    # Regression: Trump's P463 claims are a rich-list and a union. Either would
    # assert he "served on the same board" as Musk and Buffett.
    (["order"], False),                              # The World's Billionaires
    (["political coalition"], False),                # SAG-AFTRA
    (["trade union"], False),
    (["award"], False),
    (["Wikimedia list article"], False),
    # Real bodies whose membership implies a personal tie.
    (["learned society", "nonprofit organization"], True),   # Academy of A&S
    (["academy of sciences", "publishing house"], True),     # Royal Society
    (["enterprise"], True),                                   # OpenAI
    (["business", "company"], True),
    ([], False),                                     # unknown -> fail closed
])
def test_org_is_board_like(monkeypatch, kinds, expected):
    from app.providers.wikidata import WikidataProvider
    wd = WikidataProvider()
    monkeypatch.setattr(wd, "org_kinds", lambda qid: kinds)
    assert wd.org_is_board_like("Qtest") is expected


# --- wikipedia: the page title must BE the person -------------------------
def test_best_title_rejects_a_mismatched_top_hit(monkeypatch):
    """Regression: Wikipedia's top hit for "Drew Glover" resolves to Nikolas
    Cruz's entity — a human, so an is_human check passes. Stamping that QID on
    Drew would merge him with a mass shooter."""
    from app.providers.base import SearchResult
    from app.providers.wikipedia import WikipediaProvider

    wp = WikipediaProvider()
    monkeypatch.setattr(wp, "search", lambda q: [
        SearchResult("Stoneman Douglas High School shooting", "u", "", "wikipedia"),
        SearchResult("Nikolas Cruz", "u", "", "wikipedia"),
    ])
    assert wp.best_title("Drew Glover") is None


def test_best_title_rejects_a_disambiguation_or_podcast_page(monkeypatch):
    from app.providers.base import SearchResult
    from app.providers.wikipedia import WikipediaProvider

    wp = WikipediaProvider()
    monkeypatch.setattr(wp, "search", lambda q: [
        SearchResult("The Pitch (podcast)", "u", "", "wikipedia")])
    assert wp.best_title("Sheel Mohnot") is None


def test_best_title_accepts_an_exact_name_match(monkeypatch):
    from app.providers.base import SearchResult
    from app.providers.wikipedia import WikipediaProvider

    wp = WikipediaProvider()
    monkeypatch.setattr(wp, "search", lambda q: [
        SearchResult("Salesforce", "u", "", "wikipedia"),
        SearchResult("Marc Benioff", "u", "", "wikipedia"),
    ])
    assert wp.best_title("Marc Benioff") == "Marc Benioff"


# --- roster extraction: block boundaries + grammar ------------------------
def test_text_blocks_keep_element_boundaries():
    """Regression: flattening the DOM glued Storm Ventures' UI labels onto
    names, inventing "Email Hoefler" and "Floyd Ryan Floyd"."""
    from app.providers.htmltext import html_to_text, text_blocks

    html = ("<div>Floyd</div><div>Ryan Floyd</div><div>Partner</div>"
            "<div>Linkedin</div><div>Email</div><div>Taylor Hoefler</div>")
    assert "Email Taylor Hoefler" in html_to_text(html)   # the old, broken view
    blocks = text_blocks(html)
    assert "Ryan Floyd" in blocks and "Taylor Hoefler" in blocks
    assert not any("Email" in b and len(b.split()) > 1 for b in blocks)


def test_text_blocks_drop_prose():
    from app.providers.htmltext import text_blocks

    long = "x" * 200
    assert text_blocks(f"<p>{long}</p><div>Ryan Floyd</div>") == ["Ryan Floyd"]


@pytest.mark.parametrize("junk", [
    "RESERVE YOUR SPOT",        # all-caps CTA
    "PITCH US!",                # sentence punctuation
    "Brainstorming Session...",  # ellipsis
])
def test_shouted_and_punctuated_ui_copy_is_noise(junk):
    from app.edges.names import is_noise_name
    assert is_noise_name(junk)


def test_filter_person_blocks_uses_pos_not_a_person_tag():
    """POS accepts, NER vetoes. Requiring a PERSON entity (as the prior engine
    did) drops "Sheel Mohnot", which en_core_web_sm does not tag."""
    from app import extract
    if not extract.available():
        pytest.skip("spaCy model not installed")

    kept = extract.filter_person_blocks([
        "Sheel Mohnot",        # no NER tag at all -> kept via all-PROPN
        "Tae Hea Nahm",
        "JC Bahr-de Stefano",  # 'de' is a surname particle, not PROPN
        "Elizabeth Yin",
        "Current EIR",         # ADJ + PROPN
        "Our Portfolio",       # PRON + PROPN
        "Read More",           # VERB + ADJ
        "Meet The Team",       # VERB DET NOUN
        "Silicon Valley",      # all PROPN, but NER says LOC
    ])
    assert kept == ["Sheel Mohnot", "Tae Hea Nahm", "JC Bahr-de Stefano",
                    "Elizabeth Yin"]


# --- funding announcements: only a cue governs an investor list -----------
def test_lead_investor_stated_before_the_verb_is_captured():
    """Regression: "Fiat Ventures led the investment round with participation
    from ..." names the lead BEFORE the verb. A cue-only scan missed it, and the
    firm's own round then failed the self-guard and was discarded."""
    from app.providers.funding import parse_round
    text = ("Splitero Secures $11.7M in Series A Funding. Fiat Ventures led the "
            "investment round with additional participation from Gemini Ventures, "
            "PBJ Capital, and Link Ventures.")
    parsed = parse_round(text)
    assert parsed["company"] == "Splitero"
    assert parsed["amount"] == "$11.7M"
    assert "Fiat Ventures" in parsed["investors"]
    assert "Gemini Ventures" in parsed["investors"]
    # a sentence remnant must not be glued on
    assert "Funding. Fiat Ventures" not in parsed["investors"]


def test_a_cue_left_inside_a_candidate_is_stripped():
    from app.providers.funding import parse_round
    parsed = parse_round("Odynn raised $9.5M seed, led by Bonfire Ventures and "
                         "co-led by Fiat Ventures.")
    assert parsed["investors"] == ["Bonfire Ventures", "Fiat Ventures"]


@pytest.mark.parametrize("text", [
    # "led by" governs a PERSON, not an investor.
    "Fiat Ventures, and its team, led by managing partner, Marcos Fernandez, vet deals.",
    # A fund launch, not a round: no cue at all.
    "Fiat Ventures, with $25M for first fund, brings an insider approach to fintech.",
    # Plain prose naming firms and people together.
    "Fiat Ventures is a fintech VC. Marcos Fernandez and Drew Glover founded it.",
])
def test_prose_without_a_governing_cue_names_no_investors(text):
    from app.providers.funding import parse_round
    assert parse_round(text)["investors"] == []


def test_a_person_leading_a_round_is_not_an_investor():
    from app.providers.funding import parse_round
    parsed = parse_round("Acme raised $5M in a round led by Jane Doe and Sequoia Capital.")
    assert parsed["investors"] == ["Sequoia Capital"]


@pytest.mark.parametrize("name,expected", [
    ("Fiat Ventures", True), ("PBJ Capital", True), ("Verraki Partners", True),
    ("Marcos Fernandez", False),     # a person
    ("managing partner", False),     # a role
    ("existing investors", False),   # a qualifier
])
def test_looks_like_investor(name, expected):
    from app.providers.funding import looks_like_investor
    assert looks_like_investor(name) is expected


def test_a_newsletter_roundup_yields_no_investors():
    """Regression: a digest page pooled Fiat Ventures with Northzone, Accel and
    EQT — named in a different round on the same page — and produced 81
    fabricated tier-3 edges."""
    from app.providers.funding import parse_round
    from app.edges.names import org_norm_key

    gap = " filler text. " * 40   # > _CLUSTER_GAP_CHARS between rounds
    text = ("Alpha raised $5M led by Fiat Ventures with participation from "
            "Gemini Ventures." + gap +
            "Beta raised $20M led by Northzone and was joined by Accel." + gap +
            "Gamma raised $8M led by EQT Ventures." + gap +
            "Delta raised $3M led by Sequoia Capital.")
    assert parse_round(text, target_key=org_norm_key("Fiat Ventures"))["investors"] == []


def test_investors_are_scoped_to_the_round_naming_the_firm():
    from app.providers.funding import parse_round
    from app.edges.names import org_norm_key

    gap = " filler text. " * 40
    text = ("Alpha raised $5M led by Fiat Ventures with participation from "
            "Gemini Ventures." + gap +
            "Beta raised $20M led by Northzone and was joined by Accel Partners.")
    parsed = parse_round(text, target_key=org_norm_key("Fiat Ventures"))
    assert parsed["investors"] == ["Fiat Ventures", "Gemini Ventures"]
    assert "Northzone" not in parsed["investors"]
    assert "Accel Partners" not in parsed["investors"]


def test_a_run_on_sentence_is_truncated_at_the_org_suffix():
    """"Co-Led by Fiat Ventures Today Odynn, the AI-powered platform..." """
    from app.providers.funding import parse_round
    parsed = parse_round("Odynn Raises $9.5M Led by Bonfire Ventures and Co-Led "
                         "by Fiat Ventures Today Odynn , the AI-powered platform.")
    assert parsed["investors"] == ["Bonfire Ventures", "Fiat Ventures"]


# --- person-first appearances: identity must be corroborated --------------
def _fake_itunes(monkeypatch, episodes):
    """Stub Apple's episode search behind `appearances`, and the on-disk cache.

    `appearances` is cache-first, so without stubbing `cache.get` the test reads
    whatever a previous real run persisted and never exercises the code at all.
    """
    import app.providers.podcasts as pod

    def fake_request(method, url, **kw):
        class R:
            status_code = 200

            @staticmethod
            def json():
                return {"results": episodes}
        return R()

    monkeypatch.setattr(pod, "request_with_retry", fake_request)
    monkeypatch.setattr(pod.cache, "get", lambda *a, **k: None)
    monkeypatch.setattr(pod.cache, "set", lambda *a, **k: None)
    return pod


def test_appearances_rejects_a_homonym_when_the_org_is_not_corroborated(monkeypatch):
    """Regression: an episode literally titled "Drew Glover" on a local news
    show is a DIFFERENT Drew Glover. Merging it wires him to that show's host."""
    pod = _fake_itunes(monkeypatch, [
        {"feedUrl": "https://news.example/rss", "trackName": "Drew Glover",
         "collectionName": "NL Newsday"},
    ])
    provider = pod.PodcastProvider()
    monkeypatch.setattr(provider, "_feed_hosts_and_items", lambda rss: (
        ["Jeff Andreas"],
        {"Drew Glover": {"description": "Drew Glover is a city councilmember.",
                         "link": "https://news.example/ep", "show": "NL Newsday"}},
    ))

    assert provider.appearances("Drew Glover", known_orgs=["Fiat Ventures"]) == []
    # With no known org there is nothing to contradict, so it is accepted.
    assert len(provider.appearances("Drew Glover")) == 1


def test_appearances_accepts_when_the_episode_corroborates_a_known_org(monkeypatch):
    pod = _fake_itunes(monkeypatch, [
        {"feedUrl": "https://vc.example/rss",
         "trackName": "Fiat Ventures: Revitalizing Fintech with Drew Glover",
         "collectionName": "WEInvested"},
    ])
    provider = pod.PodcastProvider()
    monkeypatch.setattr(provider, "_feed_hosts_and_items", lambda rss: (
        ["Wesley Earp"],
        {"Fiat Ventures: Revitalizing Fintech with Drew Glover": {
            "description": "", "link": "https://vc.example/ep",
            "show": "WEInvested"}},
    ))
    found = provider.appearances("Drew Glover", known_orgs=["Fiat Ventures"])
    assert len(found) == 1 and found[0]["hosts"] == ["Wesley Earp"]


def test_appearances_ignore_a_show_the_person_hosts(monkeypatch):
    """Hosting your own show is not a guest appearance."""
    pod = _fake_itunes(monkeypatch, [
        {"feedUrl": "https://own.example/rss",
         "trackName": "Episode 1 with Jane Doe", "collectionName": "My Show"},
    ])
    provider = pod.PodcastProvider()
    monkeypatch.setattr(provider, "_feed_hosts_and_items", lambda rss: (
        ["Harry Stebbings"],
        {"Episode 1 with Jane Doe": {"description": "", "link": "",
                                     "show": "My Show"}},
    ))
    assert provider.appearances("Harry Stebbings") == []


def test_appearances_ignore_a_show_with_no_named_human_host(monkeypatch):
    pod = _fake_itunes(monkeypatch, [
        {"feedUrl": "https://corp.example/rss",
         "trackName": "AI today with Sam Altman", "collectionName": "Amplitude"},
    ])
    provider = pod.PodcastProvider()
    monkeypatch.setattr(provider, "_feed_hosts_and_items",
                        lambda rss: ([], {"AI today with Sam Altman": {}}))
    assert provider.appearances("Sam Altman") == []


def test_aggregators_are_not_announcements():
    from app.providers.funding import _is_blocked
    assert _is_blocked("https://www.zoominfo.com/c/fiat-ventures/398732480")
    assert _is_blocked("https://tracxn.com/d/venture-capital/fiatventures/x")
    assert not _is_blocked("https://www.splitero.com/blog/series-a-funding")


# --- firms: only a roster page asserts a roster ---------------------------
@pytest.mark.parametrize("url", [
    "https://a16z.com/team/",
    "https://example.vc/our-team",
    "https://example.vc/about/people",
    "https://example.vc/leadership",
])
def test_roster_urls_are_accepted(url):
    assert is_roster_url(url)


@pytest.mark.parametrize("url", [
    "https://www.fiat.vc",              # a homepage is not a roster
    "https://www.fiat.vc/",
    "https://example.vc/portfolio",
    "https://example.vc/blog/our-team-is-hiring",
    "https://example.vc/careers",
    "https://example.vc/about",         # about pages interleave portfolio cos
    "",
])
def test_non_roster_urls_are_refused(url):
    assert not is_roster_url(url)


@pytest.mark.parametrize("url", [
    # Regression: searching "Homebrew team page" returned the Homebrew package
    # manager's cask listing; its path contains "teams".
    "https://formulae.brew.sh/cask/microsoft-teams",
    "https://www.linkedin.com/company/slow-ventures",
    "https://www.reddit.com/r/Homebrewing/comments/x/great_homebrew_teams",
    "https://www.crunchbase.com/organization/uncork-capital/people",
])
def test_aggregators_and_lookalikes_are_refused(url):
    assert not is_roster_url(url)


# --- firms: the page must belong to the firm (Guard 2) --------------------
def test_page_belongs_to_firm_matches_on_domain():
    from app.providers.firms import page_belongs_to_firm
    assert page_belongs_to_firm("https://uncorkcapital.com/team", "<html></html>",
                                "Uncork Capital")


def test_page_belongs_to_firm_rejects_a_different_firm():
    from app.providers.firms import page_belongs_to_firm
    html = "<html><title>Team | Uncork Capital</title></html>"
    assert not page_belongs_to_firm("https://uncorkcapital.com/team", html,
                                    "Precursor Ventures")


def test_generic_tokens_alone_do_not_identify_a_firm():
    from app.providers.firms import firm_tokens
    assert firm_tokens("Uncork Capital") == {"uncork"}
    assert firm_tokens("Ventures Capital Partners") == set()


def test_a_rival_firm_whose_name_contains_ours_is_rejected():
    """Regression: "Storm Ventures portfolio" returns calmstorm.vc, whose page
    reads "Calm Storm Ventures" — containing both the token "storm" and the
    whole phrase "storm ventures"."""
    from app.providers.firms import page_belongs_to_firm
    html = "<html><title>Portfolio | Calm Storm Ventures</title></html>"
    assert not page_belongs_to_firm("https://www.calmstorm.vc/portfolio", html,
                                    "Storm Ventures")
    assert page_belongs_to_firm("https://www.stormventures.com/portfolio", html,
                                "Storm Ventures")


def test_a_bare_domain_match_does_not_settle_a_multi_word_firm():
    """Regression: "Invesco Private Capital" matched
    invesco.com/.../invesco-private-CREDIT/team.html, attaching a different
    business unit's 28 staff to the VC arm that made the investment."""
    from app.providers.firms import page_belongs_to_firm
    url = "https://www.invesco.com/us/en/alternatives/invesco-private-credit/team.html"
    html = "<html><title>Private Credit Team | Invesco US</title></html>"
    assert not page_belongs_to_firm(url, html, "Invesco Private Capital")


def test_a_bare_domain_match_settles_a_single_token_firm():
    from app.providers.firms import page_belongs_to_firm
    assert page_belongs_to_firm("https://homebrew.co/team", "<html></html>",
                                "Homebrew")
    assert page_belongs_to_firm("https://www.hustlefund.vc/team", "<html></html>",
                                "Hustle Fund")


def test_an_initialism_domain_still_identifies_its_firm():
    from app.providers.firms import page_belongs_to_firm
    html = "<html><title>BTV | Team</title></html>"
    assert page_belongs_to_firm("https://btv.vc/team", html,
                                "Better Tomorrow Ventures")


def test_host_strips_the_www_prefix_not_leading_characters():
    from app.providers.firms import _host
    assert _host("https://www.wework.com/x") == "wework.com"
    assert _host("https://wework.com/x") == "wework.com"


# --- Layer D: portfolio index vs one company ------------------------------
@pytest.mark.parametrize("url,expected", [
    ("https://foundry.vc/portfolio/", True),
    ("https://uncorkcapital.com/companies", True),
    ("https://x.vc/our-portfolio", True),
    ("https://x.vc/investments", True),
    ("https://x.vc/portfolio/acme", False),   # one company, not the index
    ("https://www.crunchbase.com/portfolio", False),
    ("https://x.vc/", False),
    ("", False),
])
def test_is_portfolio_url(url, expected):
    from app.providers.firms import is_portfolio_url
    assert is_portfolio_url(url) is expected


def test_company_name_prefers_the_logo_alt_over_link_text():
    """Foundry's anchors carry alt="Airship" and link text "Portland, OR"."""
    from bs4 import BeautifulSoup
    from app.providers.firms import _company_name_from_anchor

    anchor = BeautifulSoup(
        '<a href="https://airship.com"><img alt="Airship"/>Portland, OR</a>',
        "html.parser").a
    assert _company_name_from_anchor(anchor) == "Airship"


def test_company_name_rejects_a_location_and_empty_anchor():
    from bs4 import BeautifulSoup
    from app.providers.firms import _company_name_from_anchor

    soup = BeautifulSoup(
        '<a href="https://x.com">San Francisco, CA</a><a href="https://y.com"></a>',
        "html.parser")
    assert all(_company_name_from_anchor(a) == "" for a in soup.find_all("a"))


# --- firms: the firm's NAME is verified against its domain ----------------
@pytest.mark.parametrize("url,title,expected", [
    ("https://www.stormventures.com/our-team", "Our Team - Storm Ventures",
     "Storm Ventures"),
    ("https://btv.vc/team", "BTV | Team", "BTV"),
    ("https://homebrew.co/team", "Team | Homebrew.co", "Homebrew"),
    # Regression: naming an organization after a person corrupts the graph.
    ("https://btv.vc/team-members/sheel-mohnot", "BTV | Sheel Mohnot", "BTV"),
    ("https://www.hustlefund.vc/team",
     "A team of good humans supporting great founders. | Hustle Fund",
     "Hustle Fund"),
])
def test_firm_name_is_derived_from_the_domain_not_the_longest_segment(
        url, title, expected):
    from app.providers.firms import firm_name_from_page
    assert firm_name_from_page(f"<html><title>{title}</title></html>", url) == expected
