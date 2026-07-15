"""Calm one-off scraper: Drew Glover's Instagram mutuals -> tier-1 edges.

WHAT IT DOES
    Reads a public account's *followers* and *following* lists while signed in
    as YOU, takes the intersection (people who follow Drew AND whom Drew follows
    back = mutuals), and loads each as a tier-1 `instagram_mutual` edge to Drew.
    A mutual follow is a bilateral, structurally-asserted tie (both parties
    chose it), so it obeys Rule 0 exactly like a LinkedIn 1st-degree connection.

HOW IT AUTHENTICATES
    It opens a REAL, visible browser with a persistent profile (.ig_profile/,
    gitignored). On first run you log into YOUR OWN Instagram account in that
    window by hand — this script never sees, asks for, or stores your password.
    The session persists locally so later runs skip the login.

WHY "CALM"
    Instagram rate-limits list loading and may action accounts that scrape fast.
    This scrolls like a human: small steps, randomised 1.5-4 s pauses, stops the
    moment the list stops growing, and caps total work. Keep it one-time and
    unhurried. The only account at risk is yours — this is your session, your
    call. If Instagram shows a rate-limit wall, the script stops and says so.

USAGE
    # 1. scrape (opens a browser; log in on first run, then let it scroll)
    ./.venv/bin/python scripts/instagram_mutuals.py scrape \
        --target drew.glover --out data/drew_ig_mutuals.json

    # 2. load the result into the demo graph as tier-1 edges to Drew
    ./.venv/bin/python scripts/instagram_mutuals.py load \
        --in data/drew_ig_mutuals.json --owner "Drew Glover"

This is a demo helper, not a product feature: Instagram's dialog DOM changes
often, so treat scraping as best-effort and eyeball the saved JSON before load.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PROFILE_DIR = Path(__file__).resolve().parent.parent / ".ig_profile"
BASE = "https://www.instagram.com"

# href paths that look like /name/ but are not a person's profile
_NON_USER = {"explore", "reels", "reel", "p", "stories", "accounts", "direct",
             "about", "legal", "privacy", "terms", "developer", "web",
             "instagram", "invites", "your_activity", "settings"}

# JS run inside the page: pull {username, fullname} from every profile row in
# the open followers/following dialog. Best-effort — DOM shape drifts. Scopes to
# the modal dialog when present, else falls back to the whole document.
_COLLECT_JS = r"""
() => {
  const dialog = document.querySelector('div[role="dialog"]') || document.body;
  const out = {};
  for (const a of dialog.querySelectorAll('a[href^="/"]')) {
    const m = (a.getAttribute('href') || '').match(/^\/([A-Za-z0-9._]+)\/$/);
    if (!m) continue;
    const username = m[1];
    let row = a;
    for (let i = 0; i < 5 && row.parentElement; i++) row = row.parentElement;
    let fullname = out[username] || '';
    for (const s of row.querySelectorAll('span')) {
      const t = (s.textContent || '').trim();
      if (t && t.toLowerCase() !== username.toLowerCase()
          && t.length > 1 && t.length < 60
          && !t.includes('•') && !/^(Follow|Following|Remove|Verified)$/i.test(t)
          && /[A-Za-z]/.test(t)) { fullname = t; break; }
    }
    out[username] = fullname;
  }
  return Object.entries(out).map(([username, fullname]) => ({username, fullname}));
}
"""

# scroll the dialog's inner scrollable container to its bottom
_SCROLL_JS = r"""
() => {
  const dialog = document.querySelector('div[role="dialog"]') || document.body;
  let best = null, bestH = 0;
  for (const d of dialog.querySelectorAll('div')) {
    if (d.scrollHeight > d.clientHeight + 40 && d.clientHeight > 100
        && d.scrollHeight > bestH) { bestH = d.scrollHeight; best = d; }
  }
  if (!best) return false;
  best.scrollTop = best.scrollHeight;
  return true;
}
"""


_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_IG_APP_ID = "936619743392459"          # the public web client app id
SESSION_FILE = Path(__file__).resolve().parent.parent / ".ig_session"


def _sleep_calm(lo: float = 1.5, hi: float = 4.0) -> None:
    time.sleep(random.uniform(lo, hi))


# --- cookie / private-JSON-API path (no automated login to get blocked) ------

def _load_sessionid(args) -> str:
    """Your own IG session cookie, from --sessionid, $IG_SESSIONID, or the
    gitignored .ig_session file. It never has to log in — the cookie proves an
    already-authenticated session created in your normal browser."""
    import os
    val = (getattr(args, "sessionid", "") or os.environ.get("IG_SESSIONID", ""))
    if not val and SESSION_FILE.exists():
        val = SESSION_FILE.read_text().strip()
    return val.strip().strip('"').strip("'")


def _api_client(sessionid: str):
    import httpx
    return httpx.Client(
        headers={"User-Agent": _UA, "X-IG-App-ID": _IG_APP_ID,
                 "Accept": "*/*", "Referer": BASE + "/"},
        cookies={"sessionid": sessionid}, timeout=30.0, follow_redirects=True)


def _resolve_user(client, username: str) -> dict | None:
    """Resolve a handle to {id, username, full_name, is_private}. Uses the
    search endpoint — `web_profile_info` is aggressively 429-throttled."""
    r = client.get(f"{BASE}/api/v1/web/search/topsearch/",
                   params={"context": "blended", "query": username})
    if r.status_code != 200:
        return None
    for item in r.json().get("users", []):
        u = item.get("user", {})
        if u.get("username", "").lower() == username.lower():
            return {"id": u.get("pk"), "username": u.get("username"),
                    "full_name": u.get("full_name") or "",
                    "is_private": bool(u.get("is_private"))}
    return None


def _api_list(client, user_id: str, kind: str, *, cap: int,
              progress=None) -> dict:
    """Page IG's friendships JSON (kind = 'followers' | 'following'). Calm:
    ~50/page with randomised pauses. Returns {username: full_name}."""
    endpoint = f"{BASE}/api/v1/friendships/{user_id}/{kind}/"
    found: dict[str, str] = {}
    max_id = ""
    while len(found) < cap:
        params = {"count": 50}
        if max_id:
            params["max_id"] = max_id
        r = client.get(endpoint, params=params)
        if r.status_code == 429 or "please wait" in r.text.lower():
            if progress:
                progress(f"  ! rate-limited on {kind} at {len(found)} — stopping")
            break
        if r.status_code != 200:
            if progress:
                progress(f"  ! {kind} HTTP {r.status_code} at {len(found)}")
            break
        try:
            data = r.json()
        except ValueError:
            if progress:
                progress(f"  ! Instagram challenge on {kind} at "
                         f"{len(found)} — stopping")
            break
        for u in data.get("users", []):
            found[u["username"]] = u.get("full_name") or ""
        max_id = data.get("next_max_id") or ""
        if progress and len(found) % 200 < 50:
            progress(f"    …{kind}: {len(found)}")
        if not max_id:
            break
        _sleep_calm(1.0, 2.5)
    return found


def _api_list_full(client, user_id: str, kind: str, *, cap: int,
                   progress=None) -> list:
    """Like _api_list but keeps the pk of each user (needed to then query
    THEIR following list). Returns [{pk, username, full_name}]."""
    endpoint = f"{BASE}/api/v1/friendships/{user_id}/{kind}/"
    out, seen, max_id = [], set(), ""
    while len(out) < cap:
        params = {"count": 50}
        if max_id:
            params["max_id"] = max_id
        r = client.get(endpoint, params=params)
        if r.status_code != 200 or "please wait" in r.text.lower():
            if progress:
                progress(f"  ! {kind} HTTP {r.status_code} at {len(out)} — stop")
            break
        try:
            data = r.json()
        except ValueError:
            if progress:
                progress(f"  ! Instagram challenge on {kind} at "
                         f"{len(out)} — stopping")
            break
        for u in data.get("users", []):
            if u["username"] in seen:
                continue
            seen.add(u["username"])
            out.append({"pk": str(u.get("pk")), "username": u["username"],
                        "full_name": u.get("full_name") or ""})
        max_id = data.get("next_max_id") or ""
        if progress and len(out) % 250 < 50:
            progress(f"    …{kind}: {len(out)}")
        if not max_id:
            break
        _sleep_calm(1.0, 2.5)
    return out


def _follows_target(client, x_pk: str, target_handle: str):
    """Does user x_pk follow @target_handle? Queries x's following list for the
    handle (server-side search, exact-match verified). Returns True/False, or
    'ratelimited', or None if x's following isn't visible (private/blocked)."""
    r = client.get(f"{BASE}/api/v1/friendships/{x_pk}/following/",
                   params={"count": 20, "query": target_handle})
    if r.status_code == 429 or "please wait" in r.text.lower():
        return "ratelimited"
    if r.status_code != 200:
        return None
    try:
        data = r.json()
    except ValueError:
        # Instagram sometimes answers 200 with an HTML login/challenge page.
        # Treat it like throttling so the caller checkpoints and pauses instead
        # of crashing midway through a long, authenticated run.
        return "ratelimited"
    for u in data.get("users", []):
        if u.get("username", "").lower() == target_handle.lower():
            return True
    return False


def _open_context(p):
    """Persistent context in an ISOLATED profile (.ig_profile/, not your real
    Chrome data). Prefers real Chrome over Playwright's bundled Chromium, which
    Instagram flags and loops back to login. Anti-automation flag hides the
    webdriver signal. Falls back to bundled Chromium if Chrome isn't drivable."""
    PROFILE_DIR.mkdir(exist_ok=True)
    common = dict(user_data_dir=str(PROFILE_DIR), headless=False,
                  viewport={"width": 1180, "height": 900}, user_agent=_UA,
                  args=["--disable-blink-features=AutomationControlled"])
    try:
        return p.chromium.launch_persistent_context(channel="chrome", **common)
    except Exception:
        return p.chromium.launch_persistent_context(**common)


def _wait_for_login(page, seconds: int) -> bool:
    if _is_logged_in(page):
        return True
    print(f"\n>>> Log into YOUR OWN Instagram in the browser window.\n"
          f"    (I never see your password.) Waiting up to {seconds}s…",
          file=sys.stderr)
    deadline = time.monotonic() + seconds
    while not _is_logged_in(page) and time.monotonic() < deadline:
        _sleep_calm(2.0, 3.0)
    return _is_logged_in(page)


def _is_logged_in(page) -> bool:
    # POSITIVE signal only: the logged-in app chrome (nav bar / messages / home
    # glyph) is present. Testing for the *absence* of a password field is unsafe
    # — it also reads true before the login form has rendered, which silently
    # skips a not-yet-signed-in user straight into scraping a login wall.
    try:
        for sel in ('svg[aria-label="Home"]', 'a[href="/direct/inbox/"]',
                    'a[href="/explore/"]', 'svg[aria-label="New post"]',
                    '[aria-label="Search"]'):
            if page.query_selector(sel):
                return True
        return False
    except Exception:
        return False


def _rate_limited(page) -> bool:
    try:
        body = (page.inner_text("body") or "").lower()
    except Exception:
        return False
    return any(s in body for s in (
        "try again later", "please wait a few minutes",
        "we limit how often", "action blocked", "rate limit"))


def _collect_list(page, target: str, kind: str, *, max_scrolls: int,
                  patience: int) -> dict:
    """kind is 'followers' or 'following'. Returns {username: fullname}."""
    page.goto(f"{BASE}/{target}/{kind}/", wait_until="domcontentloaded",
              timeout=30000)
    _has_rows = r"""() => {
      const root = document.querySelector('div[role="dialog"]') || document.body;
      return [...root.querySelectorAll('a[href^="/"]')].some(
        a => /^\/[A-Za-z0-9._]+\/$/.test(a.getAttribute('href') || ''));
    }"""
    try:
        page.wait_for_function(_has_rows, timeout=20000)
    except Exception:
        # dump what's actually on the page so the selectors can be fixed in one
        # more pass instead of guessing blind
        diag = page.evaluate(r"""() => ({
          url: location.href,
          dialogs: document.querySelectorAll('div[role="dialog"]').length,
          bodyHead: (document.body.innerText || '').slice(0, 120),
        })""")
        print(f"  ! no {kind} rows for @{target} — {diag} "
              "(private account, wrong handle, not logged in, or DOM changed)",
              file=sys.stderr)
        return {}

    found: dict[str, str] = {}
    stale = 0
    for i in range(max_scrolls):
        for row in page.evaluate(_COLLECT_JS):
            u = row["username"]
            if u in _NON_USER or u.lower() == target.lower():
                continue
            # keep the first non-empty full name we see for a user
            if u not in found or (not found[u] and row["fullname"]):
                found[u] = row["fullname"]
        before = len(found)
        if not page.evaluate(_SCROLL_JS):
            break
        _sleep_calm()
        if _rate_limited(page):
            print(f"  ! Instagram rate-limited the {kind} list after "
                  f"{len(found)} names — stopping calmly. Re-run later.",
                  file=sys.stderr)
            break
        # re-collect after the scroll settled
        for row in page.evaluate(_COLLECT_JS):
            u = row["username"]
            if u in _NON_USER or u.lower() == target.lower():
                continue
            if u not in found or (not found[u] and row["fullname"]):
                found[u] = row["fullname"]
        stale = stale + 1 if len(found) == before else 0
        if stale >= patience:
            break
        if (i + 1) % 10 == 0:
            print(f"    …{kind}: {len(found)} so far", file=sys.stderr)
    return found


def _write_result(target: str, followers: dict, following: dict,
                  dest: Path) -> list:
    mutual_keys = sorted(set(followers) & set(following))
    mutuals = [{"username": u, "fullname": followers.get(u) or following.get(u)
                or ""} for u in mutual_keys]
    out = {
        "target": target,
        "counts": {"followers": len(followers), "following": len(following),
                   "mutuals": len(mutuals)},
        "mutuals": mutuals,
        "followers": [{"username": u, "fullname": n} for u, n in
                      sorted(followers.items())],
        "following": [{"username": u, "fullname": n} for u, n in
                      sorted(following.items())],
    }
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"\nfollowers: {len(followers)}  following: {len(following)}  "
          f"mutuals: {len(mutuals)}\nwrote {dest}")
    return mutuals


def _scrape_via_api(args, sessionid: str) -> int:
    log = lambda m: print(m, file=sys.stderr, flush=True)
    client = _api_client(sessionid)
    user = _resolve_user(client, args.target)
    if not user:
        log("Could not resolve the account with that session cookie — it may be "
            "expired/invalid, or the handle is wrong. Re-copy `sessionid` from a "
            "browser where you're logged in.")
        return 1
    uid = str(user["id"])
    log(f"Resolved @{args.target} -> {user.get('full_name') or '?'} (id {uid}"
        f"{', private' if user.get('is_private') else ''}). Fetching lists…")
    followers = _api_list(client, uid, "followers", cap=args.cap, progress=log)
    _sleep_calm(2.0, 4.0)
    following = _api_list(client, uid, "following", cap=args.cap, progress=log)
    client.close()
    _write_result(args.target, followers, following, Path(args.out))
    return 0


def _scrape_via_browser(args) -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright not installed.", file=sys.stderr)
        return 2
    with sync_playwright() as p:
        ctx = _open_context(p)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(BASE, wait_until="domcontentloaded", timeout=30000)
        if not _wait_for_login(page, args.login_wait):
            print("Not logged in within the wait window — aborting.",
                  file=sys.stderr)
            ctx.close()
            return 1
        print(f"Logged in — scraping @{args.target} (calm mode)…",
              file=sys.stderr)
        followers = _collect_list(page, args.target, "followers",
                                  max_scrolls=args.max_scrolls,
                                  patience=args.patience)
        _sleep_calm(3.0, 6.0)
        following = _collect_list(page, args.target, "following",
                                  max_scrolls=args.max_scrolls,
                                  patience=args.patience)
        ctx.close()
    _write_result(args.target, followers, following, Path(args.out))
    print("Eyeball the JSON before `load` — scraping is best-effort.")
    return 0


def cmd_scrape(args) -> int:
    """Prefer the cookie path (no automated login for Instagram to block);
    fall back to the in-browser scroll only if no session cookie is given."""
    sessionid = _load_sessionid(args)
    if sessionid:
        print("Using your saved session cookie (no login needed).",
              file=sys.stderr)
        return _scrape_via_api(args, sessionid)
    print("No session cookie found — falling back to in-browser scraping.\n"
          "  (Tip: the cookie path is far more reliable — see --help.)",
          file=sys.stderr)
    return _scrape_via_browser(args)


def cmd_mutuals(args) -> int:
    """Reciprocity mutuals: for each account the target FOLLOWS, check whether
    it follows the target BACK (query its following list for the target handle).
    Works around Instagram capping the target's own followers list at ~48.

    Checkpoints to <out>.progress.json after every batch so a throttle or crash
    is resumable — just re-run the same command."""
    sessionid = _load_sessionid(args)
    if not sessionid:
        print("No session cookie (see --help).", file=sys.stderr)
        return 1
    log = lambda m: print(m, file=sys.stderr, flush=True)
    client = _api_client(sessionid)

    user = _resolve_user(client, args.target)
    if not user:
        log("Could not resolve the target — cookie expired or wrong handle.")
        return 1
    drew_pk, handle = str(user["id"]), user["username"]

    ckpt = Path(args.out).with_suffix(".progress.json")
    if ckpt.exists() and not args.restart:
        state = json.loads(ckpt.read_text())
        log(f"Resuming from checkpoint: {len(state['checked'])} already checked.")
    else:
        log(f"Fetching everyone @{handle} follows…")
        following = _api_list_full(client, drew_pk, "following", cap=args.cap,
                                   progress=log)
        if not following:
            log("No following rows returned — session is challenged or "
                "rate-limited; nothing checkpointed. Retry after cooldown.")
            client.close()
            return 0
        state = {"target": handle, "drew_pk": drew_pk,
                 "following": following, "checked": {}, "mutuals": []}
        ckpt.write_text(json.dumps(state))
        log(f"{len(following)} to check for a follow-back.")

    todo = [x for x in state["following"]
            if x["username"] not in state["checked"]]
    log(f"Checking {len(todo)} accounts (calm pace; resumable)…")
    stop = False
    for i, x in enumerate(todo, 1):
        res = _follows_target(client, x["pk"], handle)
        if res == "ratelimited":
            log(f"  ! rate-limited after {len(state['checked'])} checks — "
                "saving and pausing. Re-run the same command later to resume.")
            stop = True
            break
        state["checked"][x["username"]] = bool(res)
        if res is True:
            state["mutuals"].append({"username": x["username"],
                                     "fullname": x["full_name"]})
            log(f"  ✓ mutual: {x['username']}  ({x['full_name']})")
        if i % args.batch == 0:
            ckpt.write_text(json.dumps(state))
            log(f"    …{i}/{len(todo)} checked, "
                f"{len(state['mutuals'])} mutuals so far")
        _sleep_calm(args.delay_lo, args.delay_hi)
    client.close()

    ckpt.write_text(json.dumps(state))
    done = len(state["checked"])
    total = len(state["following"])
    if not stop and done >= total:
        out = {"target": handle,
               "counts": {"following": total, "mutuals": len(state["mutuals"])},
               "mutuals": sorted(state["mutuals"], key=lambda m: m["username"])}
        Path(args.out).write_text(json.dumps(out, indent=2, ensure_ascii=False))
        log(f"\nDONE. {len(state['mutuals'])} mutuals of {total} following.")
        log(f"wrote {args.out}  (checkpoint {ckpt.name} can be deleted)")
        return 0
    log(f"\nPaused at {done}/{total} checked, {len(state['mutuals'])} mutuals. "
        "Re-run to continue.")
    return 0


def cmd_login(args) -> int:
    """Open the browser once, let you sign in, confirm it stuck, save + close.
    Run this before `scrape` so the session is durable and scraping is silent."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright not installed.", file=sys.stderr)
        return 2
    with sync_playwright() as p:
        ctx = _open_context(p)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(BASE, wait_until="domcontentloaded", timeout=30000)
        ok = _wait_for_login(page, args.login_wait)
        if ok:
            _sleep_calm(2.0, 3.0)          # let cookies settle before closing
            print("Logged in — session saved. You can run `scrape` now.")
        else:
            print("Did not detect a login in time — try again.", file=sys.stderr)
        ctx.close()
        return 0 if ok else 1


def cmd_load(args) -> int:
    from app import config
    from app.db import SessionLocal, init_db
    from app.graph import builder

    data = json.loads(Path(args.infile).read_text())
    target = data.get("target", "")
    mutuals = data.get("mutuals", [])
    owner_name = args.owner or config.DEMO_SEED_NAME

    init_db()
    db = SessionLocal()
    try:
        owner = builder.get_or_create_person(db, owner_name, is_warm=True)
        if owner is None:
            print(f"could not resolve owner {owner_name!r}", file=sys.stderr)
            return 1
        source = builder.get_or_create_source(
            db, f"instagram-mutuals://{target}",
            title=f"{owner_name} — Instagram mutual follows",
            provider="instagram")

        edges = skipped = 0
        for m in mutuals:
            handle = (m.get("username") or "").strip()
            # prefer a real display name (mergeable with graph nodes); the
            # handle is only a fallback label and always kept as evidence.
            name = (m.get("fullname") or "").strip()
            if not name or " " not in name:
                # a bare handle rarely matches a real person node cleanly
                name = name or handle
            person = builder.get_or_create_person(db, name, is_warm=True)
            if person is None or person.id == owner.id:
                skipped += 1
                continue
            edge = builder.add_edge(
                db, owner, person, "instagram_mutual", source=source,
                evidence=(f"{owner_name} and {name} follow each other on "
                          f"Instagram (@{handle})."))
            if edge is not None:
                edges += 1
        db.commit()
        print(f"loaded {edges} instagram_mutual edges "
              f"({skipped} skipped) to {owner_name}")
        return 0
    finally:
        db.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sc = sub.add_parser("scrape", help="scrape a public account's mutuals")
    sc.add_argument("--target", required=True,
                    help="Instagram handle to scrape, e.g. drew.glover")
    sc.add_argument("--out", default="data/ig_mutuals.json")
    sc.add_argument("--max-scrolls", type=int, default=400,
                    help="hard cap on scroll steps per list")
    sc.add_argument("--patience", type=int, default=6,
                    help="stop after this many scrolls add nothing new")
    sc.add_argument("--login-wait", type=int, default=300,
                    help="browser fallback: seconds to wait for you to log in")
    sc.add_argument("--sessionid", default="",
                    help="your IG session cookie (else $IG_SESSIONID or "
                         ".ig_session file); enables the reliable cookie path")
    sc.add_argument("--cap", type=int, default=10000,
                    help="max names to pull per list (safety ceiling)")
    sc.set_defaults(func=cmd_scrape)

    mu = sub.add_parser("mutuals",
                        help="reciprocity mutuals via follow-back checks")
    mu.add_argument("--target", required=True, help="handle, e.g. glovejones")
    mu.add_argument("--out", default="data/ig_mutuals.json")
    mu.add_argument("--sessionid", default="")
    mu.add_argument("--cap", type=int, default=10000,
                    help="max following to pull before checking")
    mu.add_argument("--batch", type=int, default=25,
                    help="checkpoint every N checks")
    mu.add_argument("--delay-lo", type=float, default=1.2)
    mu.add_argument("--delay-hi", type=float, default=2.6)
    mu.add_argument("--restart", action="store_true",
                    help="ignore any existing checkpoint and start over")
    mu.set_defaults(func=cmd_mutuals)

    lg = sub.add_parser("login", help="sign in once and save the session")
    lg.add_argument("--login-wait", type=int, default=600)
    lg.set_defaults(func=cmd_login)

    ld = sub.add_parser("load", help="load scraped mutuals as tier-1 edges")
    ld.add_argument("--in", dest="infile", required=True)
    ld.add_argument("--owner", default="")
    ld.set_defaults(func=cmd_load)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
