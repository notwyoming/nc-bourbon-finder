# nc-bourbon-finder

Email alerts when allocated bourbons (Eagle Rare, Blanton's, ...) ship from the
NC ABC warehouse to watched local boards.

Data source: `https://abc2.nc.gov/Search/StockShippedData` — an unauthenticated
JSON feed of all shipments to all ~166 NC boards over a rolling 16-day window,
refreshed roughly daily (~11am ET).

See [PLAN.md](PLAN.md) for the design: a stdlib-only Python script run on a
GitHub Actions cron, diffing the feed against committed state and emailing via
Gmail SMTP.
