"""Name normalisation + deterministic entity-shape filters.

Two jobs: dedup keys (person_norm_key / org_norm_key) and pruning junk out of
scraped rosters (is_noise_name / looks_like_person_name).

Pruning here is DELIBERATELY deterministic — never an LLM. An LLM name-filter
was tried and rejected: it deleted real co-founders while keeping page-title
artifacts like "Drew Glover - LinkedIn". Name shape is a syntactic property, so
a syntactic filter is both cheaper and strictly more reliable.
"""
import re

_PUNCT = re.compile(r"[^\w\s]", flags=re.UNICODE)
_WS = re.compile(r"\s+")

# Honorifics / role words / place-and-media nouns that pollute capitalised-token
# extraction from scraped pages.
_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "for", "in", "on", "at", "to",
    "mr", "mrs", "ms", "dr", "prof", "sir", "ceo", "cfo", "cto", "president",
    "chairman", "director", "founder", "officer", "company", "inc", "llc",
    "university", "foundation", "news", "report", "said", "according",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
    "sunday", "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
    "united", "states", "kingdom", "new", "york", "san", "los", "angeles",
    "francisco", "city", "north", "south", "east", "west", "street", "avenue",
    "windows", "phone", "office", "server", "cloud", "online", "today",
    "world", "times", "post", "journal", "magazine", "press", "media",
    "national", "international", "state", "higher", "education", "council",
    "committee", "conference", "symposium", "award", "civilian", "college",
    "academy", "business", "global", "federal", "central", "royal", "public",
    "big", "tech", "vision", "audio", "music", "shopping", "store",
    # role/title fragments that scraped rosters glue onto names ("Partner Jason
    # Calacanis", "Abhay Mavalankar SVP") — never part of a real personal name.
    "partner", "gp", "vp", "svp", "evp", "coo", "managing", "principal", "head",
    # VC-roster chrome specifically
    "team", "portfolio", "investor", "investors", "ventures", "capital",
    "episode", "guest", "host", "season", "podcast",
    # episode-structure words. "Acquired: Nvidia Part II" parses as the
    # all-proper-noun phrase "Nvidia Part II", which POS tagging alone accepts.
    "part", "vol", "volume", "chapter", "edition", "series", "special",
    "finale", "preview", "recap", "trailer", "bonus", "rewind", "encore",
    "mailbag", "ama", "roundup", "highlights",
    # brand words that masquerade as surnames in a podcast's author field
    # ("Ignite Insights", "Fitness Entrepreneur", "Turpentine Media")
    "insights", "entrepreneur", "fitness", "network", "studio", "studios",
    "radio", "show", "collective", "agency", "productions", "labs",
    # site navigation and section headings on firm pages. "Investment Criteria"
    # and "Key Performance Indicator" are PROPN PROPN — grammatically identical
    # to "Mary Nwokocha" — so only a lexicon separates them.
    "investment", "criteria", "approach", "login", "pitch", "frequently",
    "asked", "questions", "performance", "indicator", "reputation",
}

ORG_SUFFIXES = {
    "inc": "company", "inc.": "company", "llc": "company", "ltd": "company",
    "corp": "company", "corporation": "company", "co": "company",
    "company": "company", "group": "company", "holdings": "company",
    "partners": "firm", "ventures": "firm", "capital": "firm", "fund": "firm",
    "labs": "company", "technologies": "company", "systems": "company",
    "university": "school", "college": "school", "institute": "school",
    "school": "school", "academy": "school",
    "foundation": "nonprofit", "trust": "nonprofit",
    "association": "nonprofit", "society": "nonprofit", "nonprofit": "nonprofit",
    "department": "government", "agency": "government", "commission": "government",
    "committee": "government", "bureau": "government", "ministry": "government",
    "conference": "event", "summit": "event", "forum": "event", "expo": "event",
}

# Trailing tokens removed when building an org dedup key. Conservative: legal /
# structural suffixes only; interior words are never removed.
_ORG_DEDUP_SUFFIXES = {
    "inc", "llc", "ltd", "limited", "corp", "corporation", "co", "company",
    "group", "holdings", "plc", "gmbh", "sa", "ag", "foundation", "university",
}

# Diminutive -> formal first name, so "Tim Cook" and "Timothy Cook" collapse to
# ONE node. Applied to the FIRST token only. One-directional and conservative:
# genuinely gender-ambiguous stems (Chris, Pat, Sam, Jamie, Alex) are omitted to
# avoid wrong merges.
_DIMINUTIVES = {
    "tim": "timothy", "timmy": "timothy",
    "bill": "william", "billy": "william", "will": "william", "willy": "william",
    "bob": "robert", "bobby": "robert", "rob": "robert", "robbie": "robert",
    "dick": "richard", "rick": "richard", "ricky": "richard", "rich": "richard",
    "tom": "thomas", "tommy": "thomas",
    "mike": "michael", "mikey": "michael",
    "jim": "james", "jimmy": "james",
    "joe": "joseph", "joey": "joseph",
    "dave": "david", "davey": "david",
    "dan": "daniel", "danny": "daniel",
    "matt": "matthew",
    "nick": "nicholas",
    "tony": "anthony",
    "ben": "benjamin", "benji": "benjamin",
    "ed": "edward", "eddie": "edward",
    "ted": "theodore", "teddy": "theodore",
    "andy": "andrew",
    "greg": "gregory",
    "jeff": "jeffrey",
    "ken": "kenneth", "kenny": "kenneth",
    "larry": "lawrence",
    "pete": "peter",
    "ron": "ronald", "ronnie": "ronald",
    "fred": "frederick", "freddie": "frederick",
    "charlie": "charles", "chuck": "charles",
    "nate": "nathaniel",
    "vince": "vincent",
    "walt": "walter",
    "hank": "henry",
    "liz": "elizabeth", "beth": "elizabeth", "betty": "elizabeth",
    "kate": "katherine", "katie": "katherine", "kathy": "katherine",
    "meg": "margaret", "peggy": "margaret", "maggie": "margaret",
    "sue": "susan", "susie": "susan",
    "jen": "jennifer", "jenny": "jennifer",
    "becky": "rebecca",
    "debbie": "deborah", "deb": "deborah",
    "cindy": "cynthia",
    "vicky": "victoria",
    "abby": "abigail",
}

# Scraped-web boilerplate that name extractors otherwise mistake for people.
# Tokens here must NOT collide with real name words.
_NOISE_TOKENS = {
    "cookie", "cookies", "policy", "policies", "privacy", "agreement",
    "consent", "gdpr", "copyright", "disclaimer", "trademark",
    "profile", "profiles", "login", "signin", "signup", "logout",
    "newsletter", "subscribe", "unsubscribe", "settings", "preferences",
    "notifications", "sitemap", "homepage", "password", "username",
    "advertisement", "sponsored", "checkout", "wishlist", "captcha",
    # social/nav labels that sit next to a name in a roster cell and get glued
    # onto it ("Email Hoefler", "Linkedin X Email")
    "email", "linkedin", "twitter", "instagram", "facebook", "github",
    "bio", "biography", "website", "portfolio", "contact",
}

_NOISE_PHRASES = {
    "cookie policy", "cookie settings", "cookie preferences", "manage cookies",
    "accept cookies", "accept all", "reject all", "privacy policy",
    "privacy notice", "privacy statement", "your privacy", "data protection",
    "user agreement", "terms of service", "terms of use", "terms and conditions",
    "all rights reserved", "learn more", "read more", "show more", "see more",
    "sign in", "sign up", "log in", "create account", "join now", "get started",
    "contact us", "about us", "follow us", "skip to content", "personal information",
}

# A real personal name is written in a cased script. Reject strings with no
# cased characters at all (CJK, Devanagari, Arabic page chrome such as "जुलाई").
_HAS_CASED = re.compile(r"[a-zA-ZÀ-ɏ]")

# Role words that scraped rosters GLUE onto a name: "Partner Alex Harris",
# "Abhay Mavalankar SVP". These are stripped from the ends of a candidate rather
# than used to reject it — rejecting cost us real co-founders when a team page
# rendered the title and the name in one text node.
_ROLE_AFFIXES = {
    "partner", "partners", "gp", "vp", "svp", "evp", "coo", "ceo", "cto", "cfo",
    "cmo", "managing", "principal", "head", "director", "founder", "cofounder",
    "co-founder", "founding", "general", "associate", "analyst", "chairman",
    "chair", "chairperson", "president", "advisor", "adviser", "operating",
    "venture", "investor", "emeritus",
    "mr", "mrs", "ms", "dr", "prof", "sir",
}


# Punctuation-free forms, so a single token like "Co-Founder" (which normalize()
# turns into the two words "co founder") still matches the affix "co-founder".
_ROLE_AFFIXES_JOINED = {a.replace("-", "").replace(" ", "") for a in _ROLE_AFFIXES}

# Job-function words. Individually harmless (a person may be surnamed "Fellow"),
# but a candidate made ENTIRELY of them is a role, never a human. Used only by
# the all-tokens test in `is_noise_name`, never to reject a single token.
_ROLE_ONLY_TOKENS = {
    "executive", "assistant", "administrative", "administrator", "coordinator",
    "recruiter", "receptionist", "bookkeeper", "controller", "counsel",
    "paralegal", "intern", "internship", "fellow", "staff", "member", "office",
    "chief", "officer", "manager", "management", "lead", "leadership",
    "analyst", "associate", "engineer", "designer", "scientist", "researcher",
    "consultant", "advisory", "board", "committee", "operations", "finance",
    "marketing", "communications", "legal", "people", "talent", "platform",
    "relations", "development", "strategy", "product", "program", "project",
    "chairwoman", "vice", "senior", "junior", "deputy", "acting", "interim",
    "current", "former", "emeritus", "eir", "entrepreneur", "residence",
}


def _is_role_token(token: str) -> bool:
    return normalize(token).replace(" ", "") in _ROLE_AFFIXES_JOINED


def strip_role_affixes(name: str) -> str:
    """Drop leading/trailing role-title tokens: "Partner Alex Harris" -> "Alex
    Harris"; "Elizabeth Yin Co-Founder" -> "Elizabeth Yin". Interior tokens are
    untouched, so "Van Der Berg" survives."""
    parts = (name or "").strip().split()
    while parts and _is_role_token(parts[0]):
        parts.pop(0)
    while parts and _is_role_token(parts[-1]):
        parts.pop()
    return " ".join(parts)


def is_noise_name(name: str) -> bool:
    """True if `name` is scraped boilerplate, navigation chrome, or a page-title
    artifact rather than a real named entity — e.g. "Cookie Policy",
    "Drew Glover - LinkedIn", "Drew Glover | CEO.com"."""
    raw = (name or "").strip()
    if not raw:
        return True
    low = raw.lower()
    # embedded URL / domain / social handle => scraped chrome, not a name
    if ("http" in low or "www." in low or "@" in raw
            or re.search(r"\.(com|org|net|io|ai|co|gov|edu)\b", low)):
        return True
    # "Name - Site" / "Title | Source" / bullet artifacts. A real personal name
    # never contains a SPACED separator (hyphenated surnames have no spaces).
    if any(sep in raw for sep in (" - ", " | ", " – ", " — ", " · ", " • ", "•", "::")):
        return True
    # "Alex, Drew" / "Harris, Alex" — a comma joins two names or inverts one; it
    # is never part of a single forward-order personal name.
    if "," in raw or ";" in raw:
        return True
    # Sentence/CTA punctuation: "PITCH US!", "Brainstorming Session...".
    # A personal name carries none of these.
    if any(ch in raw for ch in "!?…") or ".." in raw:
        return True
    # Shouted UI copy: "RESERVE YOUR SPOT". Real names are not all-caps, and
    # every provider that emits upper-case names title-cases them first.
    letters = [c for c in raw if c.isalpha()]
    if len(raw.split()) > 1 and letters and all(c.isupper() for c in letters):
        return True
    if not _HAS_CASED.search(raw):
        return True
    norm = normalize(name)
    if not norm:
        return True
    if norm in _NOISE_PHRASES:
        return True
    tokens = norm.split()
    # A candidate whose EVERY token is a role word carries no name-bearing token,
    # so it is a job description, not a person: "Executive Assistant",
    # "Managing Director", "Team Member", "Chief Of Staff".
    #
    # This is not cosmetic. "Executive Assistant" was scraped as a name from four
    # firms' team pages and merged into ONE person node with 57 edges, silently
    # bridging Foundry Group, Wing, Uncork and Framework. The test is structural
    # ("contains zero name-bearing tokens"), so it generalises past a blocklist,
    # and it can only remove nodes — it can never fabricate an edge.
    if tokens and all(tok in _ROLE_ONLY_TOKENS or tok in _STOPWORDS
                      or tok in _ROLE_AFFIXES_JOINED for tok in tokens):
        return True
    return any(tok in _NOISE_TOKENS for tok in tokens)


def normalize(name: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace — the base dedup key."""
    if not name:
        return ""
    s = name.strip().lower()
    s = _PUNCT.sub(" ", s)
    s = _WS.sub(" ", s).strip()
    return s


def strip_middle_initials(name: str) -> str:
    """"John F. Kennedy" -> "John Kennedy". First and last tokens always kept;
    only interior single-letter tokens are dropped."""
    parts = name.split()
    if len(parts) <= 2:
        return name.strip()
    kept = [parts[0]]
    for mid in parts[1:-1]:
        if len(mid.rstrip(".")) <= 1:  # an initial like "F" or "F."
            continue
        kept.append(mid)
    kept.append(parts[-1])
    return " ".join(kept)


def person_norm_key(name: str) -> str:
    """Canonical dedup key for a person: normalised, middle initials stripped,
    first name canonicalised through the diminutive map."""
    base = normalize(strip_middle_initials(name))
    if not base:
        return ""
    parts = base.split()
    parts[0] = _DIMINUTIVES.get(parts[0], parts[0])
    return " ".join(parts)


def strip_org_suffixes(name: str) -> str:
    """"Acme Inc." / "Acme Corporation" -> "acme". Trailing suffix tokens only."""
    parts = normalize(name).split()
    while len(parts) > 1 and parts[-1] in _ORG_DEDUP_SUFFIXES:
        parts.pop()
    return " ".join(parts)


def org_norm_key(name: str) -> str:
    """Canonical dedup key for an organization (suffix-stripped)."""
    return strip_org_suffixes(name) or normalize(name)


def name_variants(name: str):
    """Surface forms worth storing as aliases."""
    variants = set()
    raw = name.strip()
    if raw:
        variants.add(raw)
    smi = strip_middle_initials(raw)
    if smi:
        variants.add(smi)
    return variants


# Reverse of `_DIMINUTIVES`: formal first name -> its nicknames.
_DIMINUTIVES_REVERSE: dict = {}
for _nick, _formal in _DIMINUTIVES.items():
    _DIMINUTIVES_REVERSE.setdefault(_formal, []).append(_nick)


def search_name_variants(name: str, limit: int = 4):
    """First-name spellings to try when SEARCHING an external source for a person.

    A structured source often carries the formal name ("Joseph Gebbia" from an
    SEC filing) while the media world uses the nickname ("Joe Gebbia" on every
    podcast). Searching only the stored form silently misses the appearance that
    would bridge two islands. Expands the FIRST token in both directions —
    formal→nick and nick→formal — leaving the surname untouched.

    Returns the original first, deduped, capped at `limit`.
    """
    raw = strip_middle_initials((name or "").strip())
    parts = raw.split()
    if len(parts) < 2:
        return [raw] if raw else []

    first = parts[0]
    key = normalize(first)
    alts = []
    formal = _DIMINUTIVES.get(key)
    if formal and formal != key:
        alts.append(formal)                       # joe -> joseph
    alts.extend(_DIMINUTIVES_REVERSE.get(key, []))  # joseph -> joe, joey

    surname = " ".join(parts[1:])
    out = [raw]
    for alt in alts:
        candidate = f"{alt.capitalize()} {surname}"
        if candidate not in out:
            out.append(candidate)
    return out[:limit]


def looks_like_person_name(token: str) -> bool:
    """Heuristic: 2-4 capitalised words, no org suffix, no stopwords/boilerplate."""
    token = (token or "").strip()
    if is_noise_name(token):
        return False
    parts = token.split()
    if not (2 <= len(parts) <= 4):
        return False
    for p in parts:
        if not p[:1].isupper():
            return False
        np = normalize(p)
        if len(np) < 2:          # drop bare initials ("John W")
            return False
        if np in _STOPWORDS:
            return False
        if np in ORG_SUFFIXES:
            return False
    return True


def detect_org_type(name: str) -> str:
    """Return an ORG_TYPES value from the trailing suffix, else 'unknown'."""
    parts = normalize(name).split()
    for p in reversed(parts):
        if p in ORG_SUFFIXES:
            return ORG_SUFFIXES[p]
    return "unknown"


def looks_like_org_name(name: str) -> bool:
    """True if any token matches a known org suffix."""
    parts = normalize(name).split()
    return any(p in ORG_SUFFIXES for p in parts)
