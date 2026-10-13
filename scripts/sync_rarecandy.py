#!/usr/bin/env python3
"""Pull a public Rare Candy collection into data/.

Runs from the GitHub Action on a schedule (and by hand). Rare Candy blocks
cross-origin browser requests, so the page can't fetch this live; it reads the
committed JSON instead. Each file is only rewritten when its content changed,
so the Action doesn't commit on every run.

  data/rarecandy.json   owned cards
  data/history.json     total collection value, one point per day
  data/prices.json      per-card price change points: {id: [[date, price], ...]}
  data/sets/<id>.json   every single card in each owned set (refreshed daily)

Rare Candy's own price-history endpoint is broken server-side, so per-card
history is built from our own snapshots starting 2026-10-12.
"""
import datetime as dt
import json
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request

USERNAME = "Kburchtree"
ENDPOINT = "https://rarecandy.foo/graphql"
ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = DATA / "rarecandy.json"
HISTORY = DATA / "history.json"
PRICES = DATA / "prices.json"
SETS = DATA / "sets"

PORTFOLIO_QUERY = """
query UserPortfolioProducts($input: UserProfileV2Input!, $filter: PortfolioProductsFilter) {
  userProfile: userProfileV2(input: $input) {
    portfolio {
      products(filter: $filter) {
        results {
          id
          name
          displayPrice
          shareUrl
          quantityOwned
          ... on CardProductV2 {
            category
            number
            rarity { label }
            set {
              id name slug releaseDate totalCardCount symbolImageUrl logoImageUrl
              brand { name slug }
            }
          }
          images { small large type }
          language { abbreviatedName }
        }
        paginationInfo { totalCount lastKey }
      }
    }
  }
}
"""

SET_QUERY = """
query CardSetView($input: CardSetViewInputV2!, $filter: CardsFilterV2!) {
  cardSetView: cardSetViewV2(input: $input) {
    cards(filter: $filter) {
      results {
        id name displayPrice number shareUrl
        rarity { label }
        images { small large type }
      }
      paginationInfo { totalCount lastKey }
    }
  }
}
"""


def gql(query, variables, tries=4):
    body = json.dumps({"query": query, "variables": variables}).encode()
    for attempt in range(tries):
        req = urllib.request.Request(ENDPOINT, data=body, headers={
            "content-type": "application/json",
            "app": "pokemart",
            "apollographql-client-name": "pokemart",
            "user-agent": "card-collection-sync (github.com/krburchel/card-collection)",
        })
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                payload = json.loads(r.read())
            if not payload.get("errors"):
                return payload["data"]
            err = payload["errors"]
        except (urllib.error.URLError, TimeoutError) as e:
            err = e
        # Rare Candy's backend times out now and then on later pages
        if attempt < tries - 1:
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(err)


def paginate(fetch):
    results, last_key, total = [], None, None
    while True:
        page = fetch(last_key)
        results += page["results"]
        total = page["paginationInfo"]["totalCount"]
        last_key = page["paginationInfo"]["lastKey"]
        if not last_key or not page["results"]:
            break
    return results, total


def clean_name(name):
    # "Charizard ex #199/165" -> "Charizard ex"; the number is its own field
    return re.sub(r"\s+#\S+$", "", name or "")


def front_image(p):
    imgs = p.get("images") or []
    return next((i for i in imgs if i.get("type") == "front"), None) or next(iter(imgs), {}) or {}


def normalize(p):
    s = p.get("set") or {}
    brand = s.get("brand") or {}
    img = front_image(p)
    return {
        "id": "rc:" + p["id"],
        "source": "rarecandy",
        "brand": brand.get("name") or "Other",
        "brandSlug": brand.get("slug") or "other",
        "set": s.get("name") or "",
        "setId": s.get("id") or "",
        "setRelease": (s.get("releaseDate") or "")[:10],
        "setTotal": s.get("totalCardCount"),
        "setSymbol": s.get("symbolImageUrl"),
        "setLogo": s.get("logoImageUrl"),
        "name": clean_name(p.get("name")),
        "number": p.get("number") or "",
        "rarity": (p.get("rarity") or {}).get("label") or "",
        "category": p.get("category") or "",
        "lang": (p.get("language") or {}).get("abbreviatedName") or "",
        "price": p.get("displayPrice"),
        "qty": p.get("quantityOwned") or 0,
        "img": img.get("small"),
        "imgLarge": img.get("large"),
        "url": (p.get("shareUrl") or "").split("?")[0],
    }


def write_if_changed(path, obj, compare=None):
    """Write JSON when it differs from what's on disk. `compare` picks the part
    that counts as a change (so a timestamp alone doesn't trigger a commit)."""
    compare = compare or (lambda o: o)
    if path.exists():
        try:
            if compare(json.loads(path.read_text())) == compare(obj):
                return False
        except json.JSONDecodeError:
            pass
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, ensure_ascii=False) + "\n")
    return True


def sync_collection(now):
    results, total = paginate(lambda k: gql(PORTFOLIO_QUERY, {
        "input": {"username": USERNAME},
        "filter": {"pageSize": 100, "lastKey": k},
    })["userProfile"]["portfolio"]["products"])
    if total and len(results) < total:
        raise RuntimeError(f"Only got {len(results)} of {total} items; not overwriting")

    cards = sorted((normalize(p) for p in results if (p.get("quantityOwned") or 0) > 0),
                   key=lambda c: c["id"])
    changed = write_if_changed(OUT, {
        "username": USERNAME,
        "updatedAt": now.isoformat().replace("+00:00", "Z"),
        "cards": cards,
    }, compare=lambda o: o.get("cards"))
    if changed:
        today = now.date().isoformat()
        hist = json.loads(HISTORY.read_text()) if HISTORY.exists() else []
        point = {
            "date": today,
            "value": round(sum((c["price"] or 0) * c["qty"] for c in cards), 2),
            "unique": len(cards),
            "copies": sum(c["qty"] for c in cards),
        }
        hist = [h for h in hist if h["date"] != today] + [point]
        HISTORY.write_text(json.dumps(hist, indent=1) + "\n")
        print(f"Collection updated: {point}")
    else:
        print(f"Collection unchanged ({len(cards)} cards)")
    return cards


def sync_prices(cards, today):
    """Store a point only when a card's price changes; the price on any day is
    the latest point on or before it. Cards that leave the collection keep
    their history in case they come back."""
    prices = json.loads(PRICES.read_text()) if PRICES.exists() else {}
    for c in cards:
        if c["price"] is None:
            continue
        pts = prices.setdefault(c["id"], [])
        if pts and pts[-1][1] == c["price"]:
            continue
        if pts and pts[-1][0] == today:
            pts[-1][1] = c["price"]
        else:
            pts.append([today, c["price"]])
    if write_if_changed(PRICES, dict(sorted(prices.items()))):
        print("Prices updated")


def fetch_set(set_id):
    results, total = paginate(lambda k: gql(SET_QUERY, {
        "input": {"cardSetId": set_id},
        "filter": {
            "pageSize": 50,
            "lastKey": k,
            "facets": [
                {"field": "category", "values": ["SINGLE_CARD"]},
                {"field": "sortBy", "values": ["setSortOrder:asc"]},
            ],
        },
    })["cardSetView"]["cards"])
    if total and len(results) < total:
        raise RuntimeError(f"set {set_id}: only got {len(results)} of {total}")
    out = []
    for p in results:
        img = front_image(p)
        out.append({
            "id": "rc:" + p["id"],
            "name": clean_name(p.get("name")),
            "number": p.get("number") or "",
            "rarity": (p.get("rarity") or {}).get("label") or "",
            "price": p.get("displayPrice"),
            "img": img.get("small"),
            "imgLarge": img.get("large"),
            "url": (p.get("shareUrl") or "").split("?")[0],
        })
    return out


def sync_sets(cards, today):
    """Full card list for every set in the collection, refreshed once a day.
    A failed set keeps yesterday's file rather than failing the whole sync."""
    sets = {c["setId"]: c["set"] for c in cards if c["setId"] and c["category"] == "SINGLE_CARD"}
    for set_id, name in sorted(sets.items(), key=lambda kv: kv[1]):
        path = SETS / f"{set_id}.json"
        if path.exists():
            try:
                if json.loads(path.read_text()).get("fetchedOn") == today:
                    continue
            except json.JSONDecodeError:
                pass
        try:
            set_cards = fetch_set(set_id)
        except RuntimeError as e:
            print(f"Set {name} failed, keeping old list: {e}", file=sys.stderr)
            continue
        write_if_changed(path, {"setId": set_id, "name": name, "fetchedOn": today, "cards": set_cards})
        print(f"Set {name}: {len(set_cards)} cards")


def main():
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    today = now.date().isoformat()
    cards = sync_collection(now)
    sync_prices(cards, today)
    sync_sets(cards, today)


if __name__ == "__main__":
    try:
        main()
    except (urllib.error.URLError, RuntimeError, KeyError, TypeError) as e:
        print(f"Sync failed: {e}", file=sys.stderr)
        sys.exit(1)
