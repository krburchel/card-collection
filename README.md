# Card Collection

Dashboard for my trading card collection: https://krburchel.github.io/card-collection/

- **Rare Candy sync**: `.github/workflows/sync.yml` runs `scripts/sync_rarecandy.py` every 30 minutes
  (GitHub may delay scheduled runs) and commits `data/rarecandy.json` + `data/history.json` when the
  collection changes. Run it on demand from the Actions tab ("Run workflow").
  It also saves per-card price change points (`data/prices.json`, starting 2026-10-13, since Rare Candy's
  own price-history endpoint is broken) and, once a day, the full card list of every owned set
  (`data/sets/<setId>.json`) for the missing-cards view.
  Anything Rare Candy tracks (Pokémon, Lorcana, MTG, …) shows up automatically under its brand.
- **Backups**: owned cards' pictures are saved as WebP in `images/cards/` (the page falls back to Rare
  Candy's image host if one is missing). If the collection suddenly drops by more than half, the sync
  refuses to save and fails (GitHub emails you); if the drop is real, run the workflow with **force** checked.
- **Manual cards**: `data/manual.json` holds cards Rare Candy can't track (e.g. baseball). Use the
  same fields as `rarecandy.json`: `id` (unique, `m:` prefix), `brand`, `set`, `name`, `number`,
  `rarity`, `price`, `qty`, optional `img`/`imgLarge`/`url`/`setRelease`/`setTotal`.
- **Decks**: the Decks tab builds 60-card Pokémon decks from cards you own (capped at your copies,
  4-copy rule, Basic Pokémon, ACE SPEC, evolution lines, Standard/Expanded legality; Standard = regulation
  mark H and later). Basic Energy is always available. Paste a Pokémon TCG Live / Limitless list to see what
  you already own; "Copy list" exports in PTCG Live format. Decks are saved in the browser (localStorage).
  Card types come from the open Pokémon TCG dataset (`data/cardinfo.json`, `data/tcgsets.json`), looked up
  by the sync only for cards it hasn't seen.
