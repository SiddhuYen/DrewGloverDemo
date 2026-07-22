"""Central configuration for the VC Warm-Intro Pathfinder.

Secrets live in a `.env` file at the project root (never committed).
"""
import os


def _load_dotenv() -> None:
    """Minimal .env loader (no dependency). Existing env vars take precedence."""
    for path in (".env", os.path.join(os.path.dirname(__file__), "..", ".env")):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, val = line.partition("=")
                    key, val = key.strip(), val.strip().strip('"').strip("'")
                    if key and key not in os.environ:
                        os.environ[key] = val
        except OSError:
            continue


_load_dotenv()


def _flag(name: str, default: str = "1") -> bool:
    return os.environ.get(name, default) not in ("0", "false", "False", "")


# --- storage ---------------------------------------------------------------
DB_URL = os.environ.get("VCWI_DB_URL", "sqlite:///./vcwarmintro.db")
CACHE_DB = os.environ.get("VCWI_CACHE_DB", "./vcwarmintro_cache.db")
CACHE_TTL = int(os.environ.get("VCWI_CACHE_TTL", str(30 * 86400)))
CACHE_TTL_SEARCH = CACHE_TTL
CACHE_TTL_PAGE = CACHE_TTL
CACHE_TTL_WIKI = CACHE_TTL

# --- HTTP ------------------------------------------------------------------
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
HTTP_TIMEOUT = float(os.environ.get("VCWI_HTTP_TIMEOUT", "8.0"))
HTTP_RETRIES = int(os.environ.get("VCWI_HTTP_RETRIES", "3"))
HTTP_BACKOFF_BASE = float(os.environ.get("VCWI_HTTP_BACKOFF", "0.4"))
HTTP_RETRY_STATUS = (429, 500, 502, 503, 504)
MAX_PAGE_CHARS = int(os.environ.get("VCWI_MAX_PAGE_CHARS", "20000"))
# Raw HTML retained per fetch. Must comfortably exceed a full portfolio grid.
MAX_HTML_CHARS = int(os.environ.get("VCWI_MAX_HTML_CHARS", "400000"))
# Headless-browser rendering (optional; see providers/browser.py). Used only as
# a fallback when a plain fetch yields a JavaScript shell.
BROWSER_TIMEOUT_S = float(os.environ.get("VCWI_BROWSER_TIMEOUT", "20"))
BROWSER_SETTLE_S = float(os.environ.get("VCWI_BROWSER_SETTLE", "6"))
# Serper returns `num` organic results. Five was too thin for round discovery:
# a firm's announcements are scattered across company blogs and trade press.
RESULTS_PER_QUERY = int(os.environ.get("VCWI_RESULTS_PER_QUERY", "10"))

# --- edge quality rules ----------------------------------------------------
# Rule 1 (mega-hub guard): only materialize pairwise person-person edges inside
# an org when its member count is at or below this. A ~10-partner VC firm gets
# real edges; "both went to Stanford" / "both worked at Google" gets none.
MAX_ORG_MEMBERS_FOR_EDGES = int(os.environ.get("VCWI_MAX_ORG_MEMBERS", "40"))

# Warmth tier -> pathfinding cost. Lower = warmer. Tier 1 is a demonstrated,
# on-the-record relationship; tier 5 is a weak structural affiliation.
WARMTH_TIER_COST = {1: 1.0, 2: 2.0, 3: 3.0, 4: 4.5, 5: 7.0, 6: 14.0}

# Surcharge for routing through — or suggesting — someone famous we do not
# actually know. An edge to Samuel L. Jackson can be perfectly real (Rule 0 is
# about whether a source asserts the tie, not about whether the tie is useful)
# and still be worthless as an intro: he will not take the call. Warmth tiers
# measure how well two people know each other, which is not the same question.
#
# The signal is a stored Wikidata QID, which is what graph/bridge.py already
# calls "the signal that actually matters for reachability" when it walks DOWN
# the fame gradient during expansion. This applies the same idea to ROUTING,
# which is where it was missing. It separates the two populations cleanly in the
# bundled graph: Samuel L. Jackson, Joe Rogan and Elon Musk carry a QID; Charles
# Hudson and Sheel Mohnot — the people you actually want introduced to — do not.
#
# `is_warm` is exempt, and that exemption is load-bearing: Harry Stebbings has a
# QID and is also Drew's real first-degree contact. Someone Drew genuinely knows
# is reachable no matter how famous they are.
#
# Finite, never infinite: a path through a celebrity still beats no path, so
# this re-ranks rather than censors. 0.0 disables it.
UNREACHABLE_FAME_PENALTY = float(os.environ.get("VCWI_FAME_PENALTY", "6.0"))

# How many Wikidata sitelinks (language Wikipedia pages) someone needs before
# fame_penalty treats them as famous-enough-to-be-implausible, rather than just
# "has a QID at all". A thin stub — a locally-known founder with one or two
# language pages — clears Wikidata's notability bar exactly like Samuel L.
# Jackson does, but only one of them will actually decline to relay a
# stranger's intro. Starting value, not a calibrated one — tune once this runs
# against real queries, same as MEGA_HUB_DEGREE and HOP_SURCHARGE were.
#
# 0 sitelinks is NOT "measured and obscure" — it means "not yet measured"
# (e.g. a QID adopted before this field existed) and fame_penalty fails
# TOWARD caution in that case, treating it the same as clearing the
# threshold. Otherwise every already-enriched celebrity in the bundled graph
# would silently lose protection until re-enriched.
FAME_SITELINK_THRESHOLD = int(os.environ.get("VCWI_FAME_SITELINK_THRESHOLD", "8"))

# Flat cost added to every hop, on top of that hop's tier cost. Encodes the
# thing tier costs alone cannot: each hop is another person who has to agree to
# pass the intro along. At 0.0 (the pre-web behaviour) three tier-1 hops tie one
# tier-3 hop and two tier-1 hops beat it — the search would route through two
# strangers rather than ask one investor directly.
#
# 1.0 was the first value tried and only just gets there: a direct tier-3 hop
# (cost 3) and a 2-hop tier-1 relay (cost 2x1 = 2, +1 surcharge per hop = 4)
# land EXACTLY tied at 4 either way, so which one "wins" is decided by
# whichever the search happens to discover first, not by a real preference for
# the shorter chain — fragile, and not the deliberate "one introduction beats
# three" rule the README claims.
#
# 2.0 clears that tie with real margin (direct: 3+2=5, relay: 2x(1+2)=6) while
# staying well under the point where a single WEAK hop would start beating a
# genuinely warm 2-hop chain (that crossover is at 5.0, where a lone tier-5 hop
# ties two tier-1 hops) — so short chains are preferred without warmth quality
# stopping mattering. Raise further to bias harder toward short chains still;
# 0.0 restores the old, unsurcharged ranking.
HOP_SURCHARGE = float(os.environ.get("VCWI_HOP_SURCHARGE", "2.0"))

# --- opt-in weak co-occurrence tier (the hybrid) ---------------------------
# OFF by default: the graph stays Rule-0 pure. When enabled, enrichment mines
# co_mention edges (two people named together on a page) as a tier-6 last
# resort. Even when created, they are only TRAVERSED when a query passes
# include_weak=True (`connect --weak`). Double-gated on purpose.
CO_MENTION_ENABLED = _flag("VCWI_CO_MENTION_ENABLED", "0")  # OFF by default
CO_MENTION_MAX_PER_PERSON = int(os.environ.get("VCWI_CO_MENTION_MAX", "25"))
CO_MENTION_MAX_PAGES = int(os.environ.get("VCWI_CO_MENTION_PAGES", "4"))

# --- DEEP SEARCH (the "ArtemisV2 for two hops" mode) -----------------------
# When on, a query aggressively web-mines a dense 2-hop neighbourhood around the
# target: co_mention (co-occurrence) is enabled, the frontier fan-out jumps from
# a few to DEEP_FANOUT per hop, and pathfinding TRAVERSES the weak co-occurrence
# tier so those pulled-in people are actually reachable. Costs a lot more Serper
# quota per query; off by default so the core demo stays Rule-0 pure.
DEEP_SEARCH = _flag("VCWI_DEEP_SEARCH", "0")
DEEP_FANOUT = int(os.environ.get("VCWI_DEEP_FANOUT", "25"))
DEEP_TIME_BUDGET_S = float(os.environ.get("VCWI_DEEP_TIME_BUDGET", "150"))

# SSE keepalive: a long enrichment step can run this many seconds without
# emitting a progress line. With no bytes flowing, an idle proxy / port-forward /
# browser drops the connection mid-deep-search ("connection lost"). We send an
# SSE comment on this interval so the socket never goes idle. Must be shorter
# than the tightest idle timeout in the path (GitHub Codespaces port-forwarding
# is ~60s), so 15s leaves generous margin.
SSE_HEARTBEAT_S = float(os.environ.get("VCWI_SSE_HEARTBEAT_S", "15"))

# --- Claude API access ------------------------------------------------------
# A single real Anthropic key, spend-capped in the Anthropic Console (set a
# dollar limit on this specific key) so a worst-case extraction from the
# shipped desktop build is bounded, not open-ended. No proxy in front of it
# — traded a little security margin for zero hosting infrastructure, since
# this ships to one trusted person. See DESKTOP.md.
# Shared by every caller — the CLI, the web app, tests. One key, one bill,
# set once by whoever deploys the app.
CLAUDE_API_KEY = os.environ.get("CLAUDE_API_KEY", "").strip()

# --- Claude relationship-strength classification (co_mention tier ONLY) ----
# Claude labels what kind of tie a co-mention's article text implies
# (cofounder-sounding vs. gala-photo-sounding) plus a confidence score. This
# is metadata on top of an already-weak edge, never a promotion: it NEVER
# changes relationship_type away from "co_mention" or touches Rule 0 (see
# edges/taxonomy.py). Auto-enabled but a transparent no-op when no API key
# is configured, so the pipeline is unaffected without it running.
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5")
LLM_CLASSIFY_ENABLED = _flag("VCWI_LLM_CLASSIFY", "1")
LLM_CLASSIFY_BATCH = int(os.environ.get("VCWI_LLM_CLASSIFY_BATCH", "20"))

# Homonym guard: when the target's name matches a Wikidata page, reject that
# identity if the LLM says "different" at or above this confidence, or (keyless)
# if the web background and the Wikidata description anchor in conflicting
# professional domains. Keeps a searched person from inheriting a same-named
# stranger's connections.
IDENTITY_VERIFY_ENABLED = _flag("VCWI_IDENTITY_VERIFY", "1")
IDENTITY_MISMATCH_MIN_CONF = float(os.environ.get("VCWI_IDENTITY_MISMATCH_MIN_CONF", "0.6"))

# --- LLM-triggered structural verification (co_mention -> a REAL tie) ------
# When a co-mention's LLM label is confident AND high-tier enough, spend
# ONE extra targeted lookup: pull a candidate org out of the evidence text
# and check whether that org's OWN team roster (a structural source, same
# path as _from_firm_rosters) lists both people. The LLM only points at
# WHERE to look; the roster page — not the LLM — is what asserts the tie, so
# Rule 0 stays intact. The resulting edge's type comes from what the roster
# actually supports (same_firm_partner), not from the LLM's guessed label.
# Skipped entirely when a structural edge for the pair already exists —
# Wikidata/EDGAR/OpenCorporates/rosters all run before co_mention in
# enrich_person, so that check is a free DB lookup, not a second search.
LLM_VERIFY_MIN_TIER = int(os.environ.get("VCWI_LLM_VERIFY_MIN_TIER", "2"))
LLM_VERIFY_MIN_CONFIDENCE = float(os.environ.get("VCWI_LLM_VERIFY_MIN_CONF", "0.75"))
# Only labels checkable via "does this org's roster list both people" trigger
# a verification search. family_member/bandmate/teammate/coauthor already
# have their own dedicated providers (wikidata, openalex) earlier in the same
# enrichment pass, so a miss there is a real miss, not an under-search.
LLM_VERIFY_GROUNDABLE_LABELS = frozenset({
    "cofounder", "same_firm_partner", "fiat_colleague", "colleague", "board_member",
})

# Per-node routing surcharge = coefficient x ln(degree), added when a path
# TRANSITS a person. Discourages funnelling every route through the same few
# mega-hubs (a podcast host with hundreds of guests) when a lower-degree
# alternative exists. 0 disables it.
DEGREE_PENALTY_COEF = float(os.environ.get("VCWI_DEGREE_PENALTY_COEF", "0.6"))
# Only nodes ABOVE this degree are treated as mega-hubs and penalised (on the
# excess). A recognisable connector below it pays nothing, so paths run through
# people you'd actually ask, not obscure low-traffic nodes.
MEGA_HUB_DEGREE = int(os.environ.get("VCWI_MEGA_HUB_DEGREE", "50"))

# --- pathfinding -----------------------------------------------------------
CONNECT_DEPTH = int(os.environ.get("VCWI_CONNECT_DEPTH", "2"))
# Longest path returned. 0 means UNLIMITED: a path of any length is reported as
# long as every hop is structurally asserted. Warmth already penalises distance
# (cost is summed, so a 7-hop chain scores far below a 2-hop one), and a refusal
# based on hop count hid real chains — Drew reaches Alexis Ohanian in six.
MAX_HOPS = int(os.environ.get("VCWI_MAX_HOPS", "0"))


def hop_limit(explicit: int = 0) -> float:
    """Hop ceiling as a comparable number; infinity when unlimited."""
    limit = explicit or MAX_HOPS
    return float(limit) if limit and limit > 0 else float("inf")
CONNECT_MAX_PATHS = int(os.environ.get("VCWI_CONNECT_MAX_PATHS", "3"))
# How many routes to show when EVERY chain that exists needs a famous stranger
# to relay the intro. Below CONNECT_MAX_PATHS: connect() only reaches this
# fallback when there is no usable route at all, and in that state the useful
# answer is the real chain(s) plus the reason they will not work.
#
# Was capped at 1 on the reasoning that three of them is the same dead end
# told three ways. That held when every unusable route was ranked by tier
# cost alone, with no way to tell "needs a moderately-known operator" apart
# from "needs an actual household name" — but sitelink magnitude (see
# fame_penalty / _serialize's worst_blocker_fame) now makes that a real
# distinction, so more than one dead end can be a genuinely different answer:
# the least-implausible one is worth seeing even when it is not warmest by
# tier cost. Never used while a usable route exists.
CONNECT_MAX_UNUSABLE_PATHS = int(
    os.environ.get("VCWI_CONNECT_MAX_UNUSABLE_PATHS", "3"))
# How many routes Yen's may GENERATE while looking for CONNECT_MAX_PATHS worth
# showing. Far above it on purpose: the cheapest alternates are mostly detours
# around the best route ("Drew -> Atlas Berry -> Bryce Johnson" when Drew knows
# Bryce), and _routes has to walk past those to reach a genuinely different
# intro. Every extra route costs roughly one Dijkstra per hop, so this is the
# knob that bounds a warm query's worst case; measured at ~0.2s for the default
# on the bundled 26k-edge graph. Lower it to cap latency, raise it if a dense
# neighbourhood is returning fewer routes than it should.
ROUTE_SEARCH_LIMIT = int(os.environ.get("VCWI_ROUTE_SEARCH_LIMIT", "24"))
# Wall-clock ceiling on that generation loop. The limit above bounds the WORK,
# which is not the same as bounding the wait: a target reachable through exactly
# one bridge has nothing but detours to offer, so the search spends the whole
# allowance proving a second route does not exist. That case measured 3.4s to
# return one route. Unlike the enrichment budgets this one cannot cost coverage
# — it only stops re-searching a graph already in memory, and the routes it
# gives up on are the ones ranked worst — so bounding it does not trade away the
# accuracy-first posture. 0 disables it.
ROUTE_SEARCH_BUDGET_S = float(os.environ.get("VCWI_ROUTE_SEARCH_BUDGET", "1.0"))

# --- providers -------------------------------------------------------------
SERPER_API_KEY = os.environ.get("SERPER_API_KEY", "").strip()
SERPER_ENDPOINT = "https://google.serper.dev/search"
SERPER_QPS = float(os.environ.get("VCWI_SERPER_QPS", "5.0"))
SERPER_MONTHLY_QUOTA = int(os.environ.get("VCWI_SERPER_QUOTA", "2500"))

WIKI_MIN_INTERVAL = float(os.environ.get("VCWI_WIKI_MIN_INTERVAL", "0.1"))
# Wikimedia's robot policy (https://w.wiki/4wJS) 403s a spoofed browser UA.
# It requires a descriptive agent naming the tool and a contact address.
WIKI_USER_AGENT = os.environ.get(
    "VCWI_WIKI_USER_AGENT",
    "VCWarmIntroDemo/1.0 (https://github.com/vc-warmintro; research@example.com)")

DDG_MIN_INTERVAL = float(os.environ.get("VCWI_DDG_MIN_INTERVAL", "0.6"))
DDG_JITTER = float(os.environ.get("VCWI_DDG_JITTER", "0.4"))
DDG_BUCKET_CAPACITY = int(os.environ.get("VCWI_DDG_BUCKET", "4"))
DDG_BREAKER_THRESHOLD = int(os.environ.get("VCWI_DDG_BREAKER_THRESHOLD", "3"))
DDG_BREAKER_COOLDOWN = float(os.environ.get("VCWI_DDG_BREAKER_COOLDOWN", "300"))

OPENCORPORATES_API_TOKEN = os.environ.get("OPENCORPORATES_API_TOKEN", "").strip()
OPENCORP_MIN_INTERVAL = float(os.environ.get("VCWI_OPENCORP_MIN_INTERVAL", "0.5"))

EDGAR_ENABLED = _flag("VCWI_EDGAR_ENABLED")
EDGAR_USER_AGENT = os.environ.get(
    "VCWI_EDGAR_USER_AGENT", "VC WarmIntro Demo research@example.com")
EDGAR_MIN_INTERVAL = float(os.environ.get("VCWI_EDGAR_MIN_INTERVAL", "0.2"))

# --- accuracy-vs-latency posture -------------------------------------------
# This app answers "is there a REAL path?", and here a miss costs more than a
# wait: an under-searched query reports "no path exists" for a connection that
# does, and the caller cannot tell that apart from a true negative. So the two
# frontier budgets that were explicitly cut for latency (see each) default back
# to the wide end of their measured range.
#
# This buys coverage, not correctness: it widens where we look, and every edge
# still has to clear Rule 0 to exist. It cannot manufacture a path that isn't
# there — it only stops us from missing one.
#
# The cost is real and is the whole point of the trade: a cold connect goes from
# roughly 2 minutes to roughly 4-5. Set VCWI_ACCURACY_FIRST=0 for the old
# latency-tuned defaults; either way the per-knob env vars still win.
ACCURACY_FIRST = _flag("VCWI_ACCURACY_FIRST", "1")

# --- enrichment budget -----------------------------------------------------
# Caps per enriched person, so one hub doesn't blow up the graph or the latency.
MAX_FIRMS_PER_PERSON = int(os.environ.get("VCWI_MAX_FIRMS_PER_PERSON", "3"))
# A node WITHOUT a Wikidata QID sitting on this many distinct firm rosters is the
# signature of a name-merge ("Executive Assistant" appeared on four). It is an
# audit threshold only — never an automatic delete, because real people are at
# two or three firms (Sheel Mohnot: BTV and 500 Startups).
MAX_FIRMS_PER_UNVERIFIED_PERSON = int(
    os.environ.get("VCWI_MAX_FIRMS_PER_UNVERIFIED_PERSON", "3"))
MAX_ROSTER_MEMBERS = int(os.environ.get("VCWI_MAX_ROSTER_MEMBERS", "40"))
# Firms list 50-300 portfolio companies. A cap of 25 truncated every large book
# to its first page, which is precisely where co-investment overlap is NOT.
MAX_PORTFOLIO_COMPANIES = int(os.environ.get("VCWI_MAX_PORTFOLIO", "400"))
# How many frontier people get their own enrichment pass on the 2nd hop.
# Each costs ~3 search calls plus page fetches, so this is the dominant term in
# cold-query latency: at 6 per endpoint a cold connect took 4m30s. It is also
# the dominant term in COVERAGE — an unenriched frontier person contributes no
# edges, so a path through them is not missed, it is invisible. 4m30s is the
# price of the accuracy-first trade, so ACCURACY_FIRST puts it back to 6.
ENRICH_FRONTIER_FANOUT = int(os.environ.get(
    "VCWI_ENRICH_FRONTIER_FANOUT", "6" if ACCURACY_FIRST else "3"))
# Wall-clock ceiling for widening one neighborhood. On expiry we stop and SAY SO
# rather than silently returning a thinner graph than the caller believes.
# Raised under ACCURACY_FIRST so the wider fanout above can actually finish:
# leaving it at 45s would let the extra frontier people time out mid-pass, which
# buys the latency back by throwing away the coverage it was spent on.
ENRICH_TIME_BUDGET_S = float(os.environ.get(
    "VCWI_ENRICH_TIME_BUDGET", "120" if ACCURACY_FIRST else "45"))
# Total wall-clock a single cold connect() may spend widening both sides. Must
# stay above 2x ENRICH_TIME_BUDGET_S or it, not the per-side budget, becomes the
# real ceiling and the second endpoint is the one that gets starved.
CONNECT_WORK_BUDGET_S = float(os.environ.get(
    "VCWI_CONNECT_WORK_BUDGET", "300" if ACCURACY_FIRST else "180"))
# How many hops to walk OUTWARD from a cold target (its island is small and far;
# the fixed seed's neighbourhood is already dense, so it stays at CONNECT_DEPTH).
CONNECT_TARGET_DEPTH = int(os.environ.get("VCWI_CONNECT_TARGET_DEPTH", "4"))
# Candidates ranked per expansion hop. Ranking costs one Wikipedia notability
# lookup each, so a hop that discovers hundreds must not check them all.
ENRICH_MAX_FRONTIER = int(os.environ.get("VCWI_ENRICH_MAX_FRONTIER", "40"))
# Funding announcements read per firm (each costs a search plus page fetches).
MAX_ROUNDS_PER_FIRM = int(os.environ.get("VCWI_MAX_ROUNDS_PER_FIRM", "5"))
# New firms whose rosters we will fetch because a round named them as investors.
MAX_COINVESTOR_FIRMS = int(os.environ.get("VCWI_MAX_COINVESTOR_FIRMS", "6"))

# --- the demo subject ------------------------------------------------------
DEMO_SEED_NAME = os.environ.get("VCWI_DEMO_SEED_NAME", "Drew Glover")

# Fiat's site is a single-page Wix build with no /team or /portfolio route, so
# the firm page IS the homepage. `firms.py` scrapes it for a roster.
FIAT_FIRM_NAME = "Fiat Ventures"
FIAT_SITE_URL = "https://www.fiat.vc"

# Podcast feeds -> tier-1 `podcast_guest` edges between the HOST and each GUEST.
#
# Drew is a GUEST on both of these shows, not the host. That distinction is
# load-bearing: an episode structurally asserts "this host interviewed this
# guest", so host<->guest is a real, demonstrated relationship. It asserts
# NOTHING about two guests of the same show, who have typically never met —
# minting guest<->guest edges would be precisely the co-occurrence fallacy that
# Rule 0 exists to prevent.
#
# Consequence, enforced in podcasts.py: a feed whose host is not a NAMED HUMAN
# produces no edges at all. DrinksWithAVC is hosted by two named people, so it
# yields Drew -> Bree Hanson / Vikram Lakhwara (tier 1) and, at two hops, the
# 36 other VCs they have interviewed. Next Gen Builders is hosted by the company
# Amplitude, so it yields no person edges and is listed here only for the record.
PODCAST_FEEDS = [
    {"show": "DrinksWithAVC (DWAVC)",
     "rss": "https://drinkswithavc.buzzsprout.com/1525162.rss",
     "page": "https://drinkswithavc.buzzsprout.com"},
    {"show": "Next Gen Builders",
     "rss": "https://feeds.simplecast.com/S03_arW2",
     "page": "https://amplitude.com/next-gen-builders-podcast"},
]

# Layer C — discover more human-hosted VC shows via the free iTunes Search API.
# Each named host becomes a hub: they personally interviewed every guest, so
# host<->guest is tier 1 and the guests sit two honest hops from each other.
# Kept tight on purpose. "seed investing" drifts to "The Mustard Seed Bitcoin
# Podcast" and "Money Seed" — real shows, wrong world.
PODCAST_SEARCH_TERMS = [
    "venture capital", "startup investing", "founders and investors",
    "fintech venture", "vc podcast",
]
PODCAST_DISCOVER_LIMIT = int(os.environ.get("VCWI_PODCAST_DISCOVER_LIMIT", "25"))
PODCAST_MAX_FEEDS = int(os.environ.get("VCWI_PODCAST_MAX_FEEDS", "30"))
# 20VC alone has ~1,200 episodes; cap what one feed contributes.
PODCAST_MAX_EPISODES = int(os.environ.get("VCWI_PODCAST_MAX_EPISODES", "300"))
# Person-first lookup: "which shows has this person been a guest on?" Apple's
# episode search is one free call; each distinct feed then costs one fetch.
PODCAST_EPISODE_SEARCH_LIMIT = int(
    os.environ.get("VCWI_PODCAST_EPISODE_SEARCH_LIMIT", "50"))
MAX_PODCAST_APPEARANCES = int(os.environ.get("VCWI_MAX_PODCAST_APPEARANCES", "6"))
# When enrichment reaches a podcast host, ingest up to this many of their show's
# guests, turning the host into the hub they are (Rogan interviewed Musk AND
# Altman). Each is a separate asserted interview; caps the fan-out per feed.
MAX_HOST_FEED_GUESTS = int(os.environ.get("VCWI_MAX_HOST_FEED_GUESTS", "60"))

# --- events / conference speaker silo ---------------------------------------
# How many event pages a person->events search will scrape. Each is one page
# fetch + a JSON-LD parse.
MAX_EVENTS_PER_PERSON = int(os.environ.get("VCWI_MAX_EVENTS_PER_PERSON", "4"))
# A lineup larger than this is a mega-conference (thousands of attendees pass as
# "speakers" on some pages); its speaker clique is NOT closeness, so like a
# mega-firm it yields no speaker<->speaker edges — only the organizer pivot
# survives. Kept at the Rule-1 org cap so both fan-out guards agree.
MAX_EVENT_SPEAKERS = int(os.environ.get("VCWI_MAX_EVENT_SPEAKERS", "40"))
