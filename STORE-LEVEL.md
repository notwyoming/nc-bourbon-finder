# Plan: store-level resolution for NC ABC bourbon alerts

Goal: know not just "shipped to the Asheville ABC Board" but "which specific store." Built on live reconnaissance (2026-07-20), not assumptions.

## What the recon proved

There is **no official, structured, store-level product-inventory source** for the WNC boards. Concretely:

- **State site (`abc2.nc.gov`)** exposes exactly three relevant surfaces, all verified:
  - `Search/StockShippedData` - the board-level shipment feed we already use. No store field.
  - `StoresBoards/Stocks` - "Warehouse Stock Status" (state warehouse on-hand, product-level). Not store-level.
  - `Search/ABCStoreLocator` + `Search/StoreSearch` - a **store locator** (find stores by board/city/zip/miles). Directory only, **no product/inventory** query. Probed `*Data` variants (StoreData, ProductSearchData, StoreProductData, etc.) - all 404.
- **`boards.abc.nc.gov`** is a login-gated internal board portal (not public).
- **Asheville board site** (`ashevilleabc.com`) is a static WordPress marketing site: store list, hours, "Sale Items," "Product Information." No live inventory, no commerce API (WP REST is stock Jetpack/Elementor only). Runs **9 stores**.
- **The other 8 watched boards** publish no website at all (blank `website` in the feed; Canton has a stub).
- **Big metro boards** (Wake, Meck) *do* run their own store/product inventory apps (React front-ends hitting private JSON APIs) - proof the tech exists, but only where a board built it. None of the WNC boards did.

So: the destination store split is an **internal board decision that is not published**, and retail per-store stock is not published for these boards. This is why the plan leans on reduction + reverse-engineering + human sensors.

## The biggest lever: single-store reduction

A "board" is a local ABC *system*; small ones run a **single store**, in which case board-level already **is** store-level and the existing alert needs only a static store label. Only multi-store boards need real disambiguation - and among the watchlist that is essentially just **Asheville (9 stores)** plus possibly Waynesville/Haywood.

Census complete (via the state store locator, `abc2.nc.gov/Search/StoreSearch`, board IDs below). Retail-store counts, captured in `stores.json`:

| Board | Locator ID | Retail stores | Resolution |
|-------|-----------|---------------|------------|
| Black Mountain | 51 | 1 | **board = store** (auto-resolved) |
| Weaverville | 234 | 1 | **board = store** |
| Woodfin | 226 | 1 | **board = store** |
| Fletcher | 86 | 1 | **board = store** |
| Canton | 64 | 1 | **board = store** |
| Waynesville | 187 | 1 | **board = store** |
| Brevard | 31 | 2 | needs disambiguation |
| Marion | 122 | 2 | needs disambiguation |
| Asheville | 35 | 8 (+1 MXB depot, excluded) | the hard case |

**6 of 9 boards are single-store and now resolve for free.** Only Asheville, Brevard, and Marion need real store disambiguation. `stores.json` holds the full address + phone for every retail store, and non-retail outlets (the Asheville "MXB Only" depot) are flagged and excluded.

## Tiered techniques (conventional -> unorthodox -> long-shot)

### Tier A - collapse the problem - SHIPPED
1. **Board->store census.** Done. Store list per board captured in `stores.json` (address + phone; non-retail depots flagged).
2. **Enrich single-store alerts.** Done. `check.py` appends the exact store + phone for single-store boards, and "1 of N stores - see locator" for multi-store boards. 6 of 9 watched boards resolve to a specific store.

### Tier B - conventional per-board scraping - SKIPPED
Sale Items monitoring, phone-number-in-alert, and Facebook-page scraping. Decided not worth it: low signal, high maintenance.

### Tier C - reverse-engineering (highest upside) - TRACKED IN ISSUE #1
Fingerprint the metro-board inventory APIs (Wake/Meck), and test whether a shared statewide backend generalizes to WNC store/board IDs. Full write-up and acceptance criteria in GitHub issue #1. This is the path that could crack Asheville, Brevard, and Marion.

### Tier D - community sensors - SKIPPED
Reddit/Facebook/Discord sighting monitors. Decided out of scope.

### Tier E - authoritative long-shot - PARKED (revisit)
**NC public-records request** to a board for per-store receiving/distribution records or the allocated-product distribution methodology. Legitimate under NC public records law; slow but authoritative. Deferred for now; revisit after the above.

## Status

- **Tier A: shipped** (`stores.json` + enriched alerts). 6 of 9 boards store-resolved.
- **Tier C: open** as issue #1 - the route to resolving Asheville/Brevard/Marion.
- **Tier E: parked** - the public-records angle, to revisit.

## Guardrails

Public data and public-records only; polite request rates; respect each site's terms. The public-records route is explicitly legitimate. Nothing here requires or condones access to the login-gated board portal.
