#!/usr/bin/env python3
"""Pull a public Rare Candy collection into data/rarecandy.json.

Runs from the GitHub Action on a schedule (and by hand). Rare Candy blocks
cross-origin browser requests, so the page can't fetch this live; it reads the
committed JSON instead. Only rewrites files when the collection actually
changed, so the Action doesn't commit on every run.
"""
import datetime as dt
import json
import pathlib
import re
import sys
import urllib.error
import urllib.request

USERNAME = "Kburchtree"
ENDPOINT = "https://rarecandy.foo/graphql"
ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "rarecandy.json"
HISTORY = ROOT / "data" / "history.json"

QUERY = """
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


def fetch_page(last_key):
    body = json.dumps({
        "query": QUERY,
        "variables": {
            "input": {"username": USERNAME},
            "filter": {"pageSize": 100, "lastKey": last_key},
        },
    }).encode()
    req = urllib.request.Request(ENDPOINT, data=body, headers={
        "content-type": "application/json",
        "app": "pokemart",
        "apollographql-client-name": "pokemart",
        "user-agent": "card-collection-sync (github.com/krburchel/card-collection)",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        payload = json.loads(r.read())
    if payload.get("errors"):
        raise RuntimeError(payload["errors"])
    return payload["data"]["userProfile"]["portfolio"]["products"]


def normalize(p):
    s = p.get("set") or {}
    brand = s.get("brand") or {}
    front = next((i for i in p.get("images") or [] if i.get("type") == "front"), None) \
        or next(iter(p.get("images") or []), {}) or {}
    url = (p.get("shareUrl") or "").split("?")[0]
    # "Charizard ex #199/165" -> "Charizard ex"; the number is its own field
    name = re.sub(r"\s+#\S+$", "", p.get("name") or "")
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
        "name": name,
        "number": p.get("number") or "",
        "rarity": (p.get("rarity") or {}).get("label") or "",
        "category": p.get("category") or "",
        "lang": (p.get("language") or {}).get("abbreviatedName") or "",
        "price": p.get("displayPrice"),
        "qty": p.get("quantityOwned") or 0,
        "img": front.get("small"),
        "imgLarge": front.get("large"),
        "url": url,
    }


def main():
    results, last_key, total = [], None, None
    while True:
        page = fetch_page(last_key)
        results += page["results"]
        total = page["paginationInfo"]["totalCount"]
        last_key = page["paginationInfo"]["lastKey"]
        if not last_key or not page["results"]:
            break
    if total and len(results) < total:
        raise RuntimeError(f"Only got {len(results)} of {total} items; not overwriting")

    cards = sorted((normalize(p) for p in results if (p.get("quantityOwned") or 0) > 0),
                   key=lambda c: c["id"])

    old = json.loads(OUT.read_text()) if OUT.exists() else {}
    if old.get("cards") == cards:
        print(f"No changes ({len(cards)} cards)")
        return

    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    OUT.write_text(json.dumps({
        "username": USERNAME,
        "updatedAt": now.isoformat().replace("+00:00", "Z"),
        "cards": cards,
    }, indent=1, ensure_ascii=False) + "\n")

    # One history point per day (last write of the day wins)
    hist = json.loads(HISTORY.read_text()) if HISTORY.exists() else []
    today = now.date().isoformat()
    point = {
        "date": today,
        "value": round(sum((c["price"] or 0) * c["qty"] for c in cards), 2),
        "unique": len(cards),
        "copies": sum(c["qty"] for c in cards),
    }
    hist = [h for h in hist if h["date"] != today] + [point]
    HISTORY.write_text(json.dumps(hist, indent=1) + "\n")
    print(f"Updated: {point}")


if __name__ == "__main__":
    try:
        main()
    except (urllib.error.URLError, RuntimeError, KeyError, TypeError) as e:
        print(f"Sync failed: {e}", file=sys.stderr)
        sys.exit(1)
