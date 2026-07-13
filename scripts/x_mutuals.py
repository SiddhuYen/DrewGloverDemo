"""Calm one-off scraper: Drew's X (Twitter) mutuals -> tier-1 edges.

X is a far denser network of VCs/founders/operators than Instagram, so a mutual
follow here is a high-signal bridge toward the tech world (Drew follows
@alexisohanian, etc.). A mutual is bilateral, so it obeys Rule 0 like a LinkedIn
1st connection: a new `x_mutual` tier-1 edge to Drew.

AUTH — no automated login (X blocks it). We carry a session the user already
created in their own browser, via the auth_token + ct0 cookies in the gitignored
.x_session file, injected into a headless browser. Sidesteps both the login
block and X's rotating GraphQL query ids.

METHOD — X's REST following endpoint is dead and its GraphQL query ids rotate, so
we scrape the two rendered list pages (which the cookies open logged-in):
    mutuals = (people Drew FOLLOWS)  ∩  (people who follow Drew back)
We use /verified_followers for the follow-back set: it is the notable, blue-check
subset — exactly the accounts that bridge to prominent people — and far smaller
to scroll than the full follower list.

    ./.venv/bin/python scripts/x_mutuals.py scrape --handle DrewBailer \
        --out data/x_mutuals.json
    ./.venv/bin/python scripts/x_mutuals.py load --in data/x_mutuals.json \
        --owner "Drew Glover"

X's DOM shifts and it rate-limits scrolling, so this is best-effort: it scrolls
calmly, checkpoints, and stops when a list stops growing. Eyeball the JSON first.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SESSION_FILE = Path(__file__).resolve().parent.parent / ".x_session"
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# handles that are X chrome, not people
_NON_USER = {"home", "explore", "notifications", "messages", "i", "settings",
             "search", "compose", "hashtag", "DrewBailer", "intent", "login",
             "signup", "tos", "privacy", "about", "download"}

def _sleep(lo=1.5, hi=3.0):
    time.sleep(random.uniform(lo, hi))


def _harvest_users(obj, out):
    """Recursively pull every X user object ({screen_name, name}) out of a
    GraphQL timeline response, regardless of exact nesting."""
    if isinstance(obj, dict):
        sn = obj.get("screen_name")
        if isinstance(sn, str) and "name" in obj:
            out.setdefault(sn, obj.get("name") or "")
        for v in obj.values():
            _harvest_users(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _harvest_users(v, out)


def _load_cookies():
    if not SESSION_FILE.exists():
        return None
    kv = dict(l.strip().split("=", 1) for l in SESSION_FILE.read_text().splitlines()
              if "=" in l)
    if "auth_token" not in kv or "ct0" not in kv:
        return None
    return kv


def _open_context(p, kv):
    ctx = p.chromium.launch(headless=True).new_context(
        user_agent=_UA, viewport={"width": 1280, "height": 1000})
    ctx.add_cookies([
        {"name": "auth_token", "value": kv["auth_token"], "domain": ".x.com", "path": "/"},
        {"name": "ct0", "value": kv["ct0"], "domain": ".x.com", "path": "/"},
    ])
    return ctx


def _bottom_cursor(obj):
    """The pagination cursor for the next page, wherever it sits in the tree."""
    stack, res = [obj], None
    while stack:
        o = stack.pop()
        if isinstance(o, dict):
            if o.get("cursorType") == "Bottom" and isinstance(o.get("value"), str):
                res = o["value"]
            stack.extend(o.values())
        elif isinstance(o, list):
            stack.extend(o)
    return res


def _scrape_list(page, kv, handle, path, op, *, max_pages, progress):
    """Load the list page just long enough to CAPTURE X's live GraphQL request
    for `op` (its rotating query id + auth headers), then replay it over HTTP
    with cursor pagination — complete and fast, no fragile scrolling."""
    cap = {}

    def on_request(req):
        if "/graphql/" in req.url and op in req.url and "url" not in cap:
            cap["url"], cap["headers"] = req.url, dict(req.headers)

    page.on("request", on_request)
    try:
        for attempt in range(2):          # reload once if the request is missed
            if attempt == 0:
                page.goto(f"https://x.com/{handle}/{path}",
                          wait_until="domcontentloaded", timeout=30000)
            else:
                progress(f"  retrying /{path} (reload)…")
                page.reload(wait_until="domcontentloaded", timeout=30000)
            deadline = time.monotonic() + 25
            while "url" not in cap and time.monotonic() < deadline:
                _sleep(1.2, 2.0)
                try:
                    page.evaluate("window.scrollTo(0, 800)")
                except Exception:
                    pass
            if "url" in cap:
                break
    finally:
        page.remove_listener("request", on_request)
    if "url" not in cap:
        progress(f"  ! never captured a {op} request on /{path}")
        return {}
    return _paginate(cap, kv, op, max_pages, progress)


def _paginate(cap, kv, op, max_pages, progress):
    import httpx
    from urllib.parse import urlparse, parse_qs
    pu = urlparse(cap["url"])
    qs = parse_qs(pu.query)
    variables = json.loads(qs["variables"][0])
    features = qs.get("features", [None])[0]
    base = f"{pu.scheme}://{pu.netloc}{pu.path}"
    hz = cap["headers"]
    headers = {k: hz[k] for k in hz if k.lower() in (
        "authorization", "x-csrf-token", "x-twitter-active-user",
        "x-twitter-auth-type", "x-twitter-client-language", "content-type",
        "user-agent", "referer")}
    client = httpx.Client(headers=headers,
                          cookies={"auth_token": kv["auth_token"], "ct0": kv["ct0"]},
                          timeout=30, follow_redirects=True)
    found, cursor = {}, None
    try:
        for i in range(max_pages):
            if cursor:
                variables["cursor"] = cursor
            params = {"variables": json.dumps(variables, separators=(",", ":"))}
            if features:
                params["features"] = features
            r = client.get(base, params=params)
            if r.status_code != 200:
                progress(f"  ! {op} HTTP {r.status_code} at {len(found)}: "
                         f"{r.text[:80]}")
                break
            data = r.json()
            before = len(found)
            _harvest_users(data, found)
            cursor = _bottom_cursor(data)
            if (i + 1) % 5 == 0:
                progress(f"    …{op}: {len(found)}")
            if not cursor or len(found) == before:
                break
            _sleep(1.5, 3.0)
    finally:
        client.close()
    return {h: n for h, n in found.items() if h not in _NON_USER}


def cmd_scrape(args):
    from playwright.sync_api import sync_playwright
    kv = _load_cookies()
    if not kv:
        print("No .x_session with auth_token + ct0.", file=sys.stderr)
        return 1
    log = lambda m: print(m, file=sys.stderr, flush=True)
    with sync_playwright() as p:
        ctx = _open_context(p, kv)
        page = ctx.new_page()
        # boot the SPA once so the first list's GraphQL request fires promptly
        try:
            page.goto("https://x.com/home", wait_until="domcontentloaded",
                      timeout=30000)
            _sleep(4, 6)
        except Exception:
            pass
        log(f"Scraping @{args.handle} following…")
        following = _scrape_list(page, kv, args.handle, "following", "/Following",
                                 max_pages=args.max_pages, progress=log)
        _sleep(3, 5)
        log(f"Scraping @{args.handle} verified_followers…")
        vfollowers = _scrape_list(page, kv, args.handle, "verified_followers",
                                  "/BlueVerifiedFollowers",
                                  max_pages=args.max_pages, progress=log)
        followers = dict(vfollowers)
        if not args.verified_only:
            _sleep(3, 5)
            log(f"Scraping @{args.handle} followers (full)…")
            followers.update(_scrape_list(page, kv, args.handle, "followers",
                                          "/Followers", max_pages=args.max_pages,
                                          progress=log))
        ctx.close()

    mut = sorted(set(following) & set(followers), key=str.lower)
    mutuals = [{"handle": h, "name": following.get(h) or followers.get(h) or "",
                "verified": h in vfollowers} for h in mut]
    out = {"handle": args.handle,
           "counts": {"following": len(following), "followers": len(followers),
                      "verified_followers": len(vfollowers),
                      "mutuals": len(mutuals)},
           "mutuals": mutuals}
    dest = Path(args.out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"\nfollowing: {len(following)}  followers: {len(followers)}  "
          f"mutuals: {len(mutuals)}\nwrote {dest}")
    return 0


def cmd_load(args):
    from app import config
    from app.db import SessionLocal, init_db
    from app.graph import builder
    data = json.loads(Path(args.infile).read_text())
    owner_name = args.owner or config.DEMO_SEED_NAME
    init_db()
    db = SessionLocal()
    try:
        owner = builder.get_or_create_person(db, owner_name, is_warm=True)
        source = builder.get_or_create_source(
            db, f"x-mutuals://{data.get('handle','')}",
            title=f"{owner_name} — X mutual follows", provider="x")
        edges = skipped = 0
        for m in data.get("mutuals", []):
            handle = (m.get("handle") or "").strip()
            name = (m.get("name") or "").strip()
            if not name or " " not in name:
                name = name or handle          # bare handle rarely merges cleanly
            person = builder.get_or_create_person(db, name, is_warm=True)
            if person is None or person.id == owner.id:
                skipped += 1
                continue
            edge = builder.add_edge(db, owner, person, "x_mutual", source=source,
                                    evidence=(f"{owner_name} and {name} follow "
                                              f"each other on X (@{handle})."))
            if edge is not None:
                edges += 1
        db.commit()
        print(f"loaded {edges} x_mutual edges ({skipped} skipped) to {owner_name}")
        return 0
    finally:
        db.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sc = sub.add_parser("scrape", help="scrape X mutuals via injected session")
    sc.add_argument("--handle", required=True, help="X handle, e.g. DrewBailer")
    sc.add_argument("--out", default="data/x_mutuals.json")
    sc.add_argument("--max-pages", type=int, default=40,
                    help="max cursor pages per list (safety ceiling)")
    sc.add_argument("--verified-only", action="store_true",
                    help="skip the full follower list; use verified only")
    sc.set_defaults(func=cmd_scrape)
    ld = sub.add_parser("load", help="load scraped mutuals as tier-1 edges")
    ld.add_argument("--in", dest="infile", required=True)
    ld.add_argument("--owner", default="")
    ld.set_defaults(func=cmd_load)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
