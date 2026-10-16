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
  data/cardinfo.json    gameplay info per owned Pokémon card (type, stage, HP,
                        attacks, regulation mark) for the deck builder
  data/tcgsets.json     Pokémon set codes (e.g. MEW) -> set id, for deck lists
  images/cards/         WebP backups of owned cards' pictures (small + large)

Rare Candy's own price-history endpoint is broken server-side, so per-card
history is built from our own snapshots starting 2026-10-12.
"""
import datetime as dt
import io
import json
import os
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
IMAGES = ROOT / "images" / "cards"
CARDINFO = DATA / "cardinfo.json"
TCGSETS = DATA / "tcgsets.json"
# Open Pokémon TCG card data (same ids as the images.scrydex.com picture URLs)
TCG_DATA = "https://raw.githubusercontent.com/PokemonTCG/pokemon-tcg-data/master"

# Refuse to save if the collection suddenly shrinks this much (e.g. profile made
# private, or Rare Candy returning an empty list). Run the workflow with
# "force" checked to accept a real drop.
SHRINK_GUARD = 0.5
FORCE = os.environ.get("FORCE_SYNC") == "1"

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
    old_count = len(json.loads(OUT.read_text()).get("cards", [])) if OUT.exists() else 0
    if old_count >= 10 and len(cards) < old_count * SHRINK_GUARD and not FORCE:
        raise RuntimeError(
            f"Collection dropped from {old_count} to {len(cards)} cards; not saving. "
            "If that's real, run the workflow with 'force' checked.")
    backup_images(cards)
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


def backup_images(cards):
    """Save a WebP copy of each owned card's picture so the site doesn't depend
    on Rare Candy's image host. Only downloads what's missing; failures are
    skipped (the page falls back to the remote URL)."""
    try:
        from PIL import Image
    except ImportError:
        print("Pillow not installed; skipping image backup", file=sys.stderr)
        return
    from concurrent.futures import ThreadPoolExecutor
    IMAGES.mkdir(parents=True, exist_ok=True)

    def save(job):
        url, path, quality, name = job
        try:
            req = urllib.request.Request(url, headers={"user-agent": "card-collection-sync"})
            with urllib.request.urlopen(req, timeout=60) as r:
                im = Image.open(io.BytesIO(r.read()))
            im = im.convert("RGBA" if im.mode in ("P", "LA", "RGBA") else "RGB")
            tmp = path.with_suffix(".tmp")
            im.save(tmp, "WEBP", quality=quality, method=4)
            tmp.replace(path)
            return True
        except Exception as e:  # one bad image shouldn't stop the sync
            print(f"Image {name} failed: {e}", file=sys.stderr)
            return False

    jobs = []
    for c in cards:
        stem = c["id"].split(":", 1)[1]
        for field, size, quality in (("img", "s", 82), ("imgLarge", "l", 80)):
            path = IMAGES / f"{stem}-{size}.webp"
            if not path.exists() and c.get(field):
                jobs.append((c[field], path, quality, f"{c['name']} ({size})"))
    if jobs:
        with ThreadPoolExecutor(max_workers=8) as pool:
            saved = sum(pool.map(save, jobs))
        print(f"Backed up {saved} of {len(jobs)} images")

    for c in cards:
        stem = c["id"].split(":", 1)[1]
        for field, size in (("img", "s"), ("imgLarge", "l")):
            rel = f"images/cards/{stem}-{size}.webp"
            if (ROOT / rel).exists():
                c[field + "File"] = rel


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


def get_json(url):
    req = urllib.request.Request(url, headers={"user-agent": "card-collection-sync"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def tcg_id(card):
    # https://images.scrydex.com/pokemon/sv3pt5-96/small -> sv3pt5-96
    m = re.search(r"/pokemon/([^/]+)/", card.get("img") or "")
    return m.group(1) if m else None


def card_info(t, codes):
    set_id = t["id"].rsplit("-", 1)[0]
    info = {
        "tcgId": t["id"],
        "super": t.get("supertype"),
        "sub": t.get("subtypes") or [],
        "types": t.get("types") or [],
        "mark": t.get("regulationMark"),
        "expanded": (t.get("legalities") or {}).get("expanded") == "Legal",
        "code": codes.get(set_id),
        "no": t.get("number"),
    }
    if t.get("hp"):
        info["hp"] = int(t["hp"]) if t["hp"].isdigit() else t["hp"]
    if t.get("evolvesFrom"):
        info["from"] = t["evolvesFrom"]
    if t.get("abilities"):
        info["abilities"] = [a["name"] for a in t["abilities"]]
    if t.get("attacks"):
        info["attacks"] = [{"n": a["name"], "c": a.get("cost") or [], "d": a.get("damage") or ""} for a in t["attacks"]]
    return info


def sync_cardinfo(cards, today):
    """Look up each owned Pokémon card in the open Pokémon TCG dataset so the
    deck builder knows Pokémon/Trainer/Energy, stage, type and legality. Only
    sets with cards we haven't looked up yet are downloaded."""
    pokemon = [c for c in cards if c["brandSlug"] == "pokemon" and c["category"] == "SINGLE_CARD"]
    info = json.loads(CARDINFO.read_text()) if CARDINFO.exists() else {}
    # Cards the dataset doesn't have yet get another try once a day
    todo = [c for c in pokemon if tcg_id(c) and (
        c["id"] not in info or (not info[c["id"]].get("super") and info[c["id"]].get("checked") != today))]
    if not todo and TCGSETS.exists():
        return
    try:
        sets = get_json(f"{TCG_DATA}/sets/en.json")
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"Card info skipped: {e}", file=sys.stderr)
        return
    # PTCG Live writes Scarlet & Violet promos as SVP
    codes = {s["id"]: ("SVP" if s.get("ptcgoCode") == "PR-SV" else s.get("ptcgoCode")) for s in sets}
    write_if_changed(TCGSETS, {code: {"id": sid, "name": next(s["name"] for s in sets if s["id"] == sid)}
                               for sid, code in sorted(codes.items()) if code})
    by_set = {}
    for c in todo:
        by_set.setdefault(tcg_id(c).rsplit("-", 1)[0], []).append(c)
    for set_id, set_cards in sorted(by_set.items()):
        try:
            tcg = {t["id"]: t for t in get_json(f"{TCG_DATA}/cards/en/{set_id}.json")}
        except urllib.error.HTTPError as e:
            tcg = {}
            if e.code != 404:
                print(f"Card info for {set_id} failed: {e}", file=sys.stderr)
                continue
        except (urllib.error.URLError, TimeoutError) as e:
            print(f"Card info for {set_id} failed: {e}", file=sys.stderr)
            continue
        for c in set_cards:
            t = tcg.get(tcg_id(c))
            # New promos lag behind in the dataset; another printing with the
            # same name in that set is close enough for type/stage
            twin = next((x for x in tcg.values() if x["name"] == c["name"]), None)
            if t:
                info[c["id"]] = card_info(t, codes)
            elif twin:
                info[c["id"]] = {**card_info(twin, codes), "tcgId": tcg_id(c),
                                 "no": tcg_id(c).rsplit("-", 1)[1], "mark": None, "approx": True}
            elif m := re.fullmatch(r"(?:Basic )?(\w+) Energy", c["name"]):
                # e.g. Mega Evolution Energies, which the dataset doesn't have
                info[c["id"]] = {"super": "Energy", "sub": ["Basic"], "types": [m.group(1)], "expanded": True}
            else:
                info[c["id"]] = {"super": None, "checked": today}
    if write_if_changed(CARDINFO, dict(sorted(info.items()))):
        print(f"Card info: {len(info)} cards")


def main():
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    today = now.date().isoformat()
    cards = sync_collection(now)
    sync_prices(cards, today)
    sync_sets(cards, today)
    sync_cardinfo(cards, today)


if __name__ == "__main__":
    try:
        main()
    except (urllib.error.URLError, RuntimeError, KeyError, TypeError) as e:
        print(f"Sync failed: {e}", file=sys.stderr)
        sys.exit(1)
