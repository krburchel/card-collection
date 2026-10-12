# Card Collection

Dashboard for my trading card collection: https://krburchel.github.io/card-collection/

- **Rare Candy sync**: `.github/workflows/sync.yml` runs `scripts/sync_rarecandy.py` every 30 minutes
  (GitHub may delay scheduled runs) and commits `data/rarecandy.json` + `data/history.json` when the
  collection changes. Run it on demand from the Actions tab ("Run workflow").
  Anything Rare Candy tracks (Pokémon, Lorcana, MTG, …) shows up automatically under its brand.
- **Manual cards**: `data/manual.json` holds cards Rare Candy can't track (e.g. baseball). Use the
  same fields as `rarecandy.json`: `id` (unique, `m:` prefix), `brand`, `set`, `name`, `number`,
  `rarity`, `price`, `qty`, optional `img`/`imgLarge`/`url`/`setRelease`/`setTotal`.
