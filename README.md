# SF apartment sweep — state repo

This repo exists so the daily apartment-sweep cloud routine can remember what it
already showed Liam. Cloud routines run isolated with no memory between runs, so
without persisted state every day's brief re-reports the same listings.

- [`seen.md`](seen.md) — append-only log of already-reported listings, keyed by URL.

The routine clones this repo, reads `seen.md`, filters today's findings against it,
reports only what is new, then appends the new entries and pushes.

Search criteria: 1bd, under $4,000/mo, in Mission / Nob Hill / Inner + Central
Richmond / Cole Valley / Noe Valley. Ranked by sunlight first (bay windows,
exposure, top floor, corner units), then wood floors. Not ranked by price.

Routine: https://claude.ai/code/routines/trig_01LqJ1XCFjm9XTm8UUsLJh3A
