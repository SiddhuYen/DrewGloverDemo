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

# --- opt-in weak co-occurrence tier (the hybrid) ---------------------------
# OFF by default: the graph stays Rule-0 pure. When enabled, enrichment mines
# co_mention edges (two people named together on a page) as a tier-6 last
# resort. Even when created, they are only TRAVERSED when a query passes
# include_weak=True (`connect --weak`). Double-gated on purpose.
CO_MENTION_ENABLED = _flag("VCWI_CO_MENTION_ENABLED", "0")  # OFF by default
CO_MENTION_MAX_PER_PERSON = int(os.environ.get("VCWI_CO_MENTION_MAX", "25"))
CO_MENTION_MAX_PAGES = int(os.environ.get("VCWI_CO_MENTION_PAGES", "4"))

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
# A person's NETWORK radius for comparison. Full 5-hop reachability is useless:
# inside one connected component everybody reaches everybody, so overlap is
# always 100%. Two hops is who you could plausibly be introduced to.
COMPARE_RADIUS = int(os.environ.get("VCWI_COMPARE_RADIUS", "2"))

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
# cold-query latency: at 6 per endpoint a cold connect took 4m30s.
ENRICH_FRONTIER_FANOUT = int(os.environ.get("VCWI_ENRICH_FRONTIER_FANOUT", "3"))
# Wall-clock ceiling for widening one neighborhood. On expiry we stop and SAY SO
# rather than silently returning a thinner graph than the caller believes.
ENRICH_TIME_BUDGET_S = float(os.environ.get("VCWI_ENRICH_TIME_BUDGET", "45"))
# Total wall-clock a single cold connect() may spend widening both sides.
CONNECT_WORK_BUDGET_S = float(os.environ.get("VCWI_CONNECT_WORK_BUDGET", "180"))
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
