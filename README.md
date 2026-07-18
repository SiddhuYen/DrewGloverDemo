# VC Warm-Intro Pathfinder

[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/SiddhuYen/DrewGloverDemo?quickstart=1&ref=artemis-ui-latest)

**Click the badge** to launch the app in the cloud — no install. Codespaces builds it,
starts the server, and opens the UI in a browser tab (first build takes a few minutes).
Prefer to run it locally? See [Run the app](#-run-the-app) below.

Finds the warmest **real** introduction path from **Drew Glover** (Co-Founder & GP,
[Fiat Ventures](https://www.fiat.vc)) to anyone in the VC/startup world — and says so
honestly when no such path exists.

Every hop is backed by a source that *structurally asserts* the relationship. No hop is
ever inferred from two names appearing on the same page.

```
Drew Glover
  │  sat down together on the podcast   [tier 1 · podcast_guest]
  │  "Bree Hanson interviewed Drew Glover on DrinksWithAVC (Ep. 37)."
  │  https://www.buzzsprout.com/1525162/episodes/17361266-dwavc-drew-glover-ep-37
Bree Hanson ★
  │  sat down together on the podcast   [tier 1 · podcast_guest]
  │  "Bree Hanson interviewed Charles Hudson on DrinksWithAVC (Ep. 10)."
  │  https://www.buzzsprout.com/1525162/episodes/8637084-dwavc-charles-hudson-ep-10
Charles Hudson
```

## ▶ Run the app

**No install (cloud):** click the
[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/SiddhuYen/DrewGloverDemo?quickstart=1&ref=artemis-ui-latest)
badge. It builds the environment, seeds the bundled graph, and serves the app on port
8000 — which opens in a browser tab automatically once it's ready.

**Locally:** the fastest way is a native desktop window with the graph already loaded.
No API key needed.

**First time — set up the environment (once):**

```bash
# Windows (PowerShell)
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m spacy download en_core_web_sm

# macOS / Linux
python3.12 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
./.venv/bin/python -m spacy download en_core_web_sm
```

**Then launch it (this is the command to remember):**

```bash
# Windows
.venv\Scripts\python.exe -m desktop.main

# macOS / Linux
./.venv/bin/python -m desktop.main
```

The window opens on **Connect** — type a name (e.g. *Sheel Mohnot*) and hit **Find path**.
Use `-m desktop.main --server-only` instead to run it as a local web server and open the
`http://127.0.0.1:<port>/static/index.html` URL it prints.

> Must be `-m desktop.main` (module form). Running `python desktop/main.py` directly fails
> with `ModuleNotFoundError: No module named 'app'`.

## Quickstart (developer CLI)

```bash
python3.12 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
./.venv/bin/python -m spacy download en_core_web_sm
cp .env.example .env          # optional: SERPER_API_KEY, OPENCORPORATES_API_TOKEN

./.venv/bin/python scripts/demo.py                      # seed + 6 sample connects
./.venv/bin/python -m app.cli connect "Drew Glover" "Sheel Mohnot"
./.venv/bin/python -m uvicorn app.main:app --reload      # then open /ui
```

No API key is required. Serper and OpenCorporates are optional boosters; without
them the system falls back to DuckDuckGo and skips OpenCorporates entirely.

## The two rules that make it trustworthy

A prior prototype hallucinated `Drew → David Roos → Jason Calacanis → Sam Altman`,
where the middle hop was nothing more than two names printed on the same VC directory
page. Two invariants make that class of bug impossible here.

**Rule 0 — structural assertion only.** An edge exists only when a source *asserts* the
tie: a team roster listing both people, a podcast episode, an SEC Form 4, a Wikidata
claim, a CSV row. Co-occurrence never creates an edge. This is enforced in exactly one
place — `builder.add_edge` — which *raises* on a non-structural type rather than
silently skipping, and `connect._adjacency` refuses to traverse such a row even if one
were forced into the database.

**Rule 1 — cap org fan-out.** Membership in a *small* org becomes pairwise person-person
edges; membership in a mega-hub becomes nothing. Ten partners at a VC firm genuinely know
each other. 80,000 Google employees do not, and materializing that clique would put every
pair of strangers two hops apart. The cap is `MAX_ORG_MEMBERS_FOR_EDGES = 40`, and the
*largest roster ever observed* for an org is authoritative, so a source listing only 5 of
Google's employees cannot sneak past it.

Corollaries worth stating, because each one cost a real bug:

- **A podcast connects the host to the guest, never guest to guest.** Two guests of the
  same show have typically never met. A feed whose host is not a named human (Amplitude's
  *Next Gen Builders*) therefore produces **zero** edges rather than junk ones. Nor is a
  show's own brand a host: "Riding Unicorns" and "Ignite Insights" are two proper nouns
  each and pass every name and part-of-speech test. Only structure separates them from a
  person — the show title begins with its own brand.
- **A name in an episode title is not a guest.** `Acquired: Novo Nordisk` names a drug
  company, which spaCy tags `PERSON`; `20VC: How We Got Fred Wilson to Invest $94M` is
  *about* Fred Wilson, who was never on the show. No classifier separates these — NER
  calls "Lockheed Martin" a person too. Only two things assert a guest: an explicit
  `with`/`feat.` clause, or a feed whose own titles consistently place the guest in a fixed
  slot (`DWAVC: <Name> | Ep. 37`). Acquired went from 21 fabricated guests to 7 real ones.
- **A homepage is not a roster.** Fiat's Wix homepage interleaves its three partners with
  quoted portfolio founders; NER cannot tell them apart. `firms.py` scrapes only real
  `/team`-style URLs, and Fiat is seeded from a verified manifest instead.
- **An EDGAR full-text hit is not an insider record.** Searching `"Charles Hudson"`
  returns a Form 4 filed by *Hudson Charles E. III*, an unrelated Joby Aviation insider.
  Accepting it wired a seed-stage VC into Joby's board — 66 fabricated edges. `edgar.py`
  now requires the queried person to be one of the filing's *reporting people*.
- **Role titles are stripped from names, not used to reject them.** A roster rendering
  `"Partner Alex Harris"` in one text node must still yield `Alex Harris`. Rejecting such
  candidates silently deleted Drew's two real co-founders.
- **A roster is read per DOM element, never as one flat string.** Flattening glued Storm
  Ventures' UI labels onto names and invented "Email Hoefler". And a name is admitted by
  **part of speech** (every token a proper noun), not by a spaCy `PERSON` tag: the model
  tags neither "Sheel Mohnot" nor "Brainstorming Session", so requiring `PERSON` deletes a
  real co-founder while admitting page furniture. NER is used only to *veto* what it can
  affirmatively type as a place or org ("Silicon Valley").
- **A Wikipedia search hit is not an identity.** The top result for "Drew Glover" resolves
  to Nikolas Cruz's entity — a human, so an `is_human` check passes. The page title must
  equal the queried name before its QID is trusted.
- **A failed fetch is never cached.** Wikimedia 403s a spoofed browser User-Agent; caching
  that empty result for 30 days silently disabled the whole Wikidata backbone.
- **A portfolio page says the *firm* invested, never which partner did.** So it cannot
  assert `investor_of` (tier 3, "invested in their company") — no free source names the
  deal lead, and the founders are not on the page. What two *independent* portfolio pages
  do assert is that both firms back the same company, which is `shared_portfolio`
  (tier 4). A company's identity is the **domain** its logo links to, never its printed
  name: "Bolt" the scooter company and "Bolt" the checkout company must never merge.
  (Measured: this currently yields **zero** edges — see *Portfolios* below.)
- **A page belongs to a firm by identity, not by keyword.** Searching "Storm Ventures
  portfolio" returns `calmstorm.vc`, whose page reads "Calm Storm Ventures" — containing
  the token `storm` *and* the whole phrase `storm ventures`. Only the domain, or the name
  the page declares for itself, settles it (with an initialism allowance, since `btv.vc`
  declares "BTV" and means Better Tomorrow Ventures). And a bare domain match settles only
  a *single-token* firm: "Invesco Private Capital" matched
  `invesco.com/.../invesco-private-**credit**/team.html`, attaching a different business
  unit's 28 staff to the VC arm that made the investment.
- **A name in a funding announcement is an investor only if a cue governs it.** An edge
  comes from the span after "led by" / "with participation from", never from a capitalised
  name elsewhere on the page. `"Fiat Ventures, and its team, led by managing partner,
  Marcos Fernandez"` puts a person after the cue; `"Fiat Ventures, with $25M for first
  fund"` is a fund launch, not a round.
- **Investors are scoped to ONE round, and a roundup is discarded.** A newsletter digest
  pooled Fiat Ventures with Northzone, Accel and EQT — named in a *different* round on the
  same page — producing 81 fabricated tier-3 edges. Cues are now clustered by proximity,
  only the cluster naming the queried firm is read, and a page holding more than two rounds
  yields nothing.

## Warmth tiers

Path cost is the sum of edge costs; `warmth_score = 1 / (1 + total_cost)`. Lower cost is
warmer, and because total cost encodes both tier *and* length, one introduction correctly
beats three.

| Tier | Cost | Relationships | Structural source |
|---|---|---|---|
| 1 | 1.0 | `podcast_guest`, `cohost`, `cofounder`, `fiat_colleague`, `linkedin_1st` | episode, team page, CSV |
| 2 | 2.0 | `same_firm_partner`, `board_member` | firm roster, EDGAR, OpenCorporates |
| 3 | 3.0 | `co_investor` (same round), `investor_of` | funding announcement |
| 4 | 4.5 | `shared_portfolio`, `coauthor`, `colleague` | two portfolio pages, Wikidata employer |
| 5 | 7.0 | `co_speaker`, `notable_affiliation` | event page, Wikidata |
| — | ∞ | `cooccurrence` | **never persisted** |

`investor_of` stays empty on purpose: it needs a source naming the partner who led the round
*and* the founder who took it. `co_investor` is emitted from funding announcements — see
below for what it currently yields.

## Architecture

```
app/
  main.py        FastAPI: /connect /discover /tree /compare /health /seed /network/csv /ui
  cli.py         connect | discover | tree | compare | seed | stats
  config.py      every knob; Drew's sources
  db.py          engine, WAL, additive migrations
  models.py      Person, Organization, RelationshipEdge, Source, LocalProfile
  extract.py     spaCy NER (the only extractor — no LLM, ever)
  edges/
    names.py     deterministic name filters + dedup keys
    taxonomy.py  relationship types, tiers, cost fn   <- Rule 0 lives here
  graph/
    builder.py   upserts; add_edge (Rule 0), materialize_org_edges (Rule 1)
    enrich.py    structured-only enrichment of one person (both endpoints)
    connect.py   meet-in-the-middle weighted top-3 diverse pathfinder
    tree.py      warmest-path tree + network comparison (mutual contacts)
  providers/     wikidata wikipedia edgar opencorporates serper duckduckgo
                 firms (roster scrape) podcasts (RSS host<->guest)
  ingest/
    seed.py      Drew's warm first degree
    linkedin_csv.py   optional booster
scripts/  demo.py  precrawl.py
tests/    211 tests, no network
```

**How it reaches people.** Free data has no clean co-investment edges, so the connective
tissue is *person → org → person*: reach a firm and you reach its partners. Drew's warm
layer is his Fiat co-founders plus the two DrinksWithAVC hosts who interviewed him; through
those hosts, the 36 other VCs they have interviewed sit two honest hops away. Any target
outside the graph is enriched on demand, then the search meets in the middle.

**Enrichment layers**, applied per person, cheapest and most authoritative first.
Each is a *silo* of targeted lookups — the breadth of ArtemisV2's expansion, but every
lookup terminates in a structural assertion rather than in prose co-occurrence.

| Layer | Source | Yields |
|---|---|---|
| Wikidata | employer / member-of / chaired-board claims | `colleague`, `board_member` |
| SEC EDGAR | fellow Form 4 filers at the same issuer | `board_member` |
| OpenCorporates | fellow registered officers (needs a token) | `board_member`, `colleague` |
| **person → firm** | the roster page that *names them* | `same_firm_partner` |
| firm → roster | team page of a firm they already belong to | `same_firm_partner` |
| **person → podcasts** | shows they were a *guest* on (Apple episode search) | `podcast_guest` |

The person→podcast silo matters more than its size suggests. Seeding walks *known feeds*;
without a person-first lookup a prominent figure is nearly invisible. Sam Altman's Wikidata
record yields exactly three colleagues (Reddit; OpenAI's 79 employees are correctly killed
by Rule 1) — while Harry Stebbings' 20VC interview of him sat unread. Two guards make it
safe: the episode must **assert** him as guest, and when we already know his organisations
the episode must **corroborate** one of them, so an episode titled "Drew Glover" on a local
news show cannot merge a different Drew Glover into his node.

**Rule 1 removes closeness, not facts.** A mega-hub's membership is now recorded even
though its members are not contacts: "Sam Altman works at OpenAI" is true, useful, and is
exactly what corroborates his identity on a podcast — while his 79 colleagues remain
strangers to him in the graph.

The person→firm layer is what lets an arbitrary VC gain colleagues. Without it a roster is
only reachable firm-first (via precrawl), so a podcast guest stays a lone node with no firm.
A person→org `org_membership` row records the firm; it carries no `person_b` and is never
traversed, so membership becomes closeness only through Rule 1.

**Latency.** `connect()` escalates only as far as it must: search the existing graph, then
enrich the two endpoints, then widen their frontiers — returning at the first stage that
finds a path. Enriching unconditionally cost **3m48s** of network to rediscover a path
already in the graph; the staged version answers the same query in **0.26s**. Widening is
bounded by a fan-out and a wall-clock budget, and reports how many neighbours it left
unexplored rather than letting "we stopped looking" masquerade as "no path exists".

## Why no LLM

An LLM entity filter was tried and rejected: it deleted real co-founders while keeping
page-title artifacts like `"Drew Glover - LinkedIn"`. Name shape is a syntactic property,
so a syntactic filter is cheaper, faster, and strictly more reliable. Extraction is spaCy
NER; every candidate then passes the deterministic filters in `edges/names.py`.

## Trees, and comparing a network against Drew's

```bash
./.venv/bin/python -m app.cli tree    "Sheel Mohnot" --max-hops 2
./.venv/bin/python -m app.cli compare "Sheel Mohnot"          # --against defaults to Drew
./.venv/bin/python -m app.cli compare "Immad Akhund" --radius 2
```

`tree` is the Dijkstra shortest-path tree over edge **cost**, so a node's parent is the
person who would actually introduce you — not merely someone standing nearby. It reports
hops, warmth tiers, and *who introduces the most people*, which is how the hubs surface.

```
Sheel Mohnot — 40 people within 2 hops
  by hop:  {1: 6, 2: 34}      by tier: {1: 36, 2: 4}
  who introduces the most people:   34  Bree Hanson (1h)

Sheel Mohnot
└── ● Bree Hanson ★  [sat down together on the podcast]
    ├── ● Drew Glover ★
    ├── ● Tae Hea Nahm
    └── ● Amy Cheetham
```

Both commands cite their sources. Every chain hop carries the relationship, its warmth
tier, the quoted evidence, and a link to the page that asserts it — `tree --links`, and
`compare` by default (`--no-links` to suppress):

```
     via Drew Glover:
       Drew Glover
         │  ● sat down together on the podcast  [tier 1 · podcast_guest]
         │    “Bree Hanson interviewed Drew Glover on DrinksWithAVC (Ep. 37).”
         │    https://www.buzzsprout.com/1525162/episodes/17361266-dwavc-drew-glover-ep-37
         Bree Hanson
```

Two details, because a citation that cannot be checked is not a citation. **A URL is never
truncated** — a clipped link looks valid and is not; only the prose evidence is elided,
with an ellipsis. And when a feed omits `<link>` (DrinksWithAVC does), the episode page is
recovered from the enclosure by dropping the audio extension, rather than citing the show's
homepage — which would send the reader to a page that does not contain the episode quoted.

`compare` reports the **mutual contacts**, ranked by how cheap the introduction is *from
both sides*. Two decisions make the number mean anything:

- **A radius, not full reachability.** Inside one connected component everybody reaches
  everybody — with no hop limit, Drew's "reach" is all 495 of them — so unbounded overlap is
  always 100%. `COMPARE_RADIUS = 2` is the set of people you could plausibly be introduced
  to.
- **Neither person may route through the other.** Marcos Fernandez was showing up as a
  "mutual contact" of Drew and Sheel via `Sheel → Bree → Drew Glover → Marcos`. A contact
  reachable only *through* the other person is not shared; he is simply Drew's.

```
Drew Glover  vs  Sheel Mohnot          Drew Glover  vs  Immad Akhund
  Drew reaches      37                   Drew reaches      38
  Sheel reaches     39                   Immad reaches    107
  shared            35  (85.4%)          shared             0  (0.0%)
  connected: 2 hops apart                No structural chain connects them.
```

Also on the API (`GET /tree?person=…`, `GET /compare?person=…&against=…`) and in the UI,
which now has four tabs: Connect, Discover, Tree, Compare. Every rendered hop links to the
page that asserts it.

## No hop limit

`MAX_HOPS = 0` — a path of **any length** is returned, provided every hop is structurally
asserted. Warmth already penalises distance (cost is summed, so a 6-hop chain scores 0.143
against a 2-hop chain's 0.333); refusing on hop count only hid real chains. Drew reaches
Alexis Ohanian in six honest tier-1 hops:

```
Drew Glover → Bree Hanson → Charles Hudson → Ben Gilbert → Brian Tolkin
            → Harry Stebbings → Alexis Ohanian     6 hops · warmth 0.143
```

Lifting the cap took Drew's reachable set from 218 to **495**. Set `VCWI_MAX_HOPS=5` to
bound it again; the refusal then names the distance ("they are 6 hops apart") rather than
implying no chain exists.

The only remaining way to fail is genuine disconnection, and that is reported as such:

```
Drew Glover -> Sam Altman
  No chain of structurally-asserted relationships connects them at all.
  We do not guess at a path from names that merely co-occur.
```

## Building the backbone

With a `SERPER_API_KEY` set, `scripts/precrawl.py` scrapes the team pages of ~25 active VC
firms. This is what connects Drew's warm island to the wider VC graph: Eric Bahn, Sheel
Mohnot and Tae Hea Nahm are all DrinksWithAVC guests *and* firm partners, so their rosters
splice the two together.

```bash
./.venv/bin/python -m app.cli seed --discover   # + human-hosted VC podcasts (Layer C)
./.venv/bin/python scripts/precrawl.py          # ~30 Serper searches; caches 30 days
```

Measured effect on Drew's reachable set (at the 5-hop bound these were taken under; with
the limit now lifted the final figure is 495):

| build | reachable |
|---|---|
| warm seed only (DWAVC + Fiat) | 40 |
| \+ firm rosters (`precrawl.py`) | 89 |
| \+ person→firm enrichment | 101 |
| \+ podcast fleet (`seed --discover`) | **214** |

Paths start using tier-2 `same_firm_partner` hops:

```
Drew -> Bree Hanson (podcast, t1) -> Eric Bahn (podcast, t1) -> Elizabeth Yin (Hustle Fund roster, t2)
```

Bessemer is skipped: its roster exceeds the Rule 1 cap, so it records membership and zero
pairwise edges. Precursor and Upfront yield nothing — their team pages are JS shells that
serve no readable text, and an unreadable page asserts nothing.

### Portfolios: built, correct, and currently worth nothing

`precrawl.py` also reads each firm's portfolio page and records 141 investments across 262
companies. It has produced **zero `shared_portfolio` edges**, for two measured reasons:

1. **No overlap.** Of 262 companies, every single one is backed by exactly one firm in the
   corpus. These 25 firms differ by stage and sector; co-investment lives among peers.
   Drew is a fintech seed investor, so his co-investors are QED, Nyca, Commerce, Restive.
2. **Most portfolio pages are JS shells.** The structural signal is the outbound link to a
   company's own site. Foundry's page has 103 external anchors; QED's has 129 anchors and
   only **4** external — its companies are internal `/companies/<slug>` links. Extraction
   yields 101 companies for Foundry, 0 for QED.

The machinery is tested (domain identity, Rule 1 on the investor set, idempotent
recording) and will pay the moment either constraint lifts. But today it contributes
nothing, and the README says so rather than implying a tier-4 backbone that does not exist.

### Funding rounds: the parser works, the rosters don't exist

```bash
./.venv/bin/python -c "
from app.db import SessionLocal, init_db; from app.ingest.rounds import ingest_rounds
init_db(); ingest_rounds(SessionLocal(), ['Fiat Ventures'], progress=print)"
```

`providers/funding.py` reads announcements and extracts the investors a cue governs. On
Fiat Ventures it correctly finds six co-investors across five rounds — Link Ventures,
Northwestern Mutual Future Ventures, Panoramic Ventures, Insight Partners, Invesco Private
Capital, Bonfire Ventures — verified against TechCrunch's Copper story ("a *preemptive*
round led by Fiat Ventures … participation from Panoramic Ventures, Insight Partners,
Invesco Private Capital").

It emits **zero tier-3 person edges**, because `co_investor` connects *partners*, and not
one of those six firms publishes a team page we can verify belongs to it. The org-level
fact is persisted on each firm (`meta.co_investments`) so the edges appear the moment a
roster becomes readable or a LinkedIn import supplies the people. `ingest_rounds` prints
which firms it could not read, so "we could not see their team" never reads as "they have
no partners".

Two fabrications were caught and fixed here, both worth knowing about:

* A **newsletter roundup** produced 81 edges tying Fiat to Northzone, Accel and EQT, who
  appeared in a different round on the same page.
* A **wrong business unit**: Invesco Private *Credit*'s 28 staff were attached to Invesco
  Private *Capital*, producing 84 edges. Both are now regression tests.

## Known limits

- Coverage is the VC/startup world, not the general public. The UI says so.
- Paths are **unverified** — real relationships, but confirm before requesting an intro.
- **Recall is bounded by who is structurally reachable.** Sam Altman correctly returns
  "no path". That is the design, not a defect.
- JS-rendered team pages (Precursor, Upfront) scrape empty. Fixing that needs a headless
  browser, which the demo deliberately avoids.
- Tiers 3–5 (`investor_of`, `co_investor`, `shared_portfolio`, `co_speaker`,
  `notable_affiliation`) are defined but **no provider emits them** — funding-announcement
  scraping was never built. Only tiers 1, 2 and `colleague` populate the graph today.
- `en_core_web_sm` skips names whose first token is a month word (`April Underwood`), a
  deliberate precision-over-recall tradeoff inherited from the stopword list.
- LinkedIn import is supported (`POST /network/csv`) but untested against a real export.

## Tests

```bash
./.venv/bin/python -m pytest tests/ -q      # 211 passed, no network
```

They cover the junk-vs-real name sets, Rule 0 (co-occurrence creates zero edges and
raises), Rule 1 (a 60-member org creates zero pairwise edges; a 10-member firm creates
them), cost monotonicity, diverse-path selection, block-boundary roster extraction, the
POS-over-`PERSON` filter, and regressions for every bug listed above.
