#!/usr/bin/env python3
"""
Build a target list of small/mid apartment buildings in Liam's neighborhoods,
from SF's public building-permit records (DataSF dataset i98e-djp9).

This is the TARGETING step of direct-to-landlord outreach: it produces the list of
buildings worth looking at. It does NOT produce owner names or phone numbers --
neither is available in any SF open dataset (see README).

Output: buildings.csv, one row per distinct building (block+lot), with address,
unit count, stories, and the APN you'll need for the Recorder deed lookup.

Usage:  python3 target_buildings.py
"""

import csv
import json
import urllib.parse
import urllib.request
from collections import defaultdict

API = "https://data.sfgov.org/resource/i98e-djp9.json"

# SF's "analysis neighborhood" boundaries don't include Cole Valley or Central
# Richmond, so we pull the containing neighborhood and narrow by bounding box below.
NEIGHBORHOODS = [
    "Mission",
    "Nob Hill",
    "Inner Richmond",
    "Outer Richmond",   # narrowed to Central Richmond by bbox
    "Haight Ashbury",   # narrowed to Cole Valley by bbox
    "Noe Valley",
]

# (lon_min, lon_max, lat_min, lat_max) -- approximate, tuned to the colloquial areas.
BBOX = {
    "Cole Valley":       (-122.4530, -122.4450, 37.7620, 37.7695),
    "Central Richmond":  (-122.4850, -122.4730, 37.7720, 37.7870),
}

MIN_UNITS = 2      # bay-window Victorian flats are often 2-6 units
MAX_UNITS = 40     # above this you're into managed towers, which is what we're avoiding


def fetch():
    # existing_units is a TEXT column in this dataset, so it can't be compared
    # numerically in SoQL -- the unit-count filter happens client-side below.
    where = (
        "existing_use='apartments' "
        "AND neighborhoods_analysis_boundaries in ({})".format(
            ",".join("'{}'".format(n) for n in NEIGHBORHOODS)
        )
    )
    # Socrata caps a single response at 50k rows, so page through with a stable
    # $order (:id) -- without it, paging can skip or repeat records.
    page, offset, out = 50000, 0, []
    while True:
        params = {
            "$select": (
                "block,lot,street_number,street_name,street_suffix,existing_units,"
                "number_of_existing_stories,neighborhoods_analysis_boundaries,"
                "location,permit_creation_date,existing_construction_type_description"
            ),
            "$where": where,
            "$order": ":id",
            "$limit": str(page),
            "$offset": str(offset),
        }
        url = API + "?" + urllib.parse.urlencode(params)
        with urllib.request.urlopen(url, timeout=120) as r:
            batch = json.load(r)
        out.extend(batch)
        print("  ...{} records".format(len(out)))
        if len(batch) < page:
            return out
        offset += page


def refine_neighborhood(name, lon, lat):
    """Narrow the city's coarse boundary to the area Liam actually named."""
    if name == "Haight Ashbury":
        lo, hi, la, ha = BBOX["Cole Valley"]
        return "Cole Valley" if (lo <= lon <= hi and la <= lat <= ha) else None
    if name in ("Outer Richmond", "Inner Richmond"):
        lo, hi, la, ha = BBOX["Central Richmond"]
        if lo <= lon <= hi and la <= lat <= ha:
            return "Central Richmond"
        return "Inner Richmond" if name == "Inner Richmond" else None
    return name


def main():
    rows = fetch()
    print("fetched {} permit records".format(len(rows)))

    # One building may have many permits over the years; collapse to block+lot.
    buildings = defaultdict(dict)
    for r in rows:
        loc = r.get("location") or {}
        coords = loc.get("coordinates")
        if not coords:
            continue
        lon, lat = coords[0], coords[1]

        hood = refine_neighborhood(r.get("neighborhoods_analysis_boundaries", ""), lon, lat)
        if hood is None:
            continue

        try:
            units = float(r.get("existing_units") or 0)
        except ValueError:
            continue
        if not (MIN_UNITS <= units <= MAX_UNITS):
            continue

        key = (r.get("block"), r.get("lot"))
        b = buildings[key]

        # Keep the record with the highest unit count; ties go to the newest permit.
        if not b or units > b["units"] or (
            units == b["units"] and (r.get("permit_creation_date") or "") > b["last_permit"]
        ):
            addr = " ".join(
                x for x in (
                    r.get("street_number"), r.get("street_name"), r.get("street_suffix")
                ) if x
            )
            b.update({
                "apn": "{}{}".format(r.get("block", ""), r.get("lot", "")),
                "block": r.get("block", ""),
                "lot": r.get("lot", ""),
                "address": addr,
                "neighborhood": hood,
                "units": units,
                "stories": r.get("number_of_existing_stories") or "",
                "construction": r.get("existing_construction_type_description") or "",
                "last_permit": r.get("permit_creation_date") or "",
                "lat": lat,
                "lon": lon,
            })

    out = sorted(
        buildings.values(),
        key=lambda b: (b["neighborhood"], -b["units"], b["address"]),
    )

    cols = [
        "neighborhood", "address", "units", "stories", "construction",
        "apn", "block", "lot", "last_permit", "streetview", "recorder_lookup",
    ]
    with open("buildings.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for b in out:
            b["units"] = int(b["units"])
            b["streetview"] = (
                "https://www.google.com/maps/@?api=1&map_action=pano"
                "&viewpoint={},{}".format(b["lat"], b["lon"])
            )
            b["recorder_lookup"] = "https://sfassessor.org/ (search APN {})".format(b["apn"])
            w.writerow(b)

    print("wrote buildings.csv -- {} distinct buildings".format(len(out)))
    counts = defaultdict(int)
    for b in out:
        counts[b["neighborhood"]] += 1
    for k in sorted(counts):
        print("  {:18} {}".format(k, counts[k]))


if __name__ == "__main__":
    main()
