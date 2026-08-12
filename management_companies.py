#!/usr/bin/env python3
"""
Compile property management companies operating in San Francisco from the city's
Registered Business Locations dataset (DataSF g8m3-pdis).

Every business operating in SF must register with the Treasurer & Tax Collector,
and the registry is public and refreshed daily. Filtering to the real-estate NAICS
family gives us landlords AND managers; the two are separated by signal:

  - a management company registers at MANY addresses (the buildings it runs)
  - a single-building LLC registers once, often named after its own address

Output: management_companies.csv, ranked so the biggest operators come first.

Usage:  python3 management_companies.py
"""

import csv
import json
import re
import urllib.parse
import urllib.request
from collections import defaultdict

API = "https://data.sfgov.org/resource/g8m3-pdis.json"

# Real-estate NAICS family.
#   531311 residential property managers   531312 nonresidential property managers
#   531390 other real estate activities    531210 real estate agents & brokers
#   531110 lessors of residential buildings (mostly landlords, but big managers land here too)
#   53     businesses that only self-reported the sector
NAICS = ["531311", "531312", "531390", "531210", "531110", "53"]

# Names that read like a management operation rather than a holding entity.
MGMT_WORDS = re.compile(
    r"\b(management|managment|mgmt|properties|property|realty|real estate|"
    r"leasing|rentals|residential|apartments|apts|housing|homes|"
    r"associates|partners|group|company|realtors)\b",
    re.I,
)

# Holding-entity tells: "123 Main Street LLC", "... Owner LLC", "... Investors LP".
ADDRESS_NAME = re.compile(r"^\s*\d+[\s-]")
HOLDING_WORDS = re.compile(r"\b(owner|investors|holdings|trust|estate of|family)\b", re.I)


def fetch():
    where = "location_end_date IS NULL AND self_reported_naics_code in ({})".format(
        ",".join("'{}'".format(c) for c in NAICS)
    )
    page, offset, out = 50000, 0, []
    while True:
        params = {
            "$select": (
                "ownership_name,dba_name,full_business_address,city,state,business_zip,"
                "mailing_address_1,mail_city,mail_zipcode,self_reported_naics_code,"
                "neighborhoods_analysis_boundaries,supervisor_district,location_start_date"
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
        if len(batch) < page:
            return out
        offset += page


def norm(name):
    """Collapse punctuation/suffix noise so 'ABC Mgmt, Inc.' == 'ABC Mgmt Inc'."""
    n = (name or "").strip().lower()
    n = re.sub(r"[.,]", "", n)
    n = re.sub(r"\b(llc|l l c|inc|incorporated|lp|l p|llp|corp|corporation|co)\b", "", n)
    return re.sub(r"\s+", " ", n).strip()


def classify(display_name, n_locations):
    """Score how likely this is a management company vs a single-building holder."""
    score = 0
    if MGMT_WORDS.search(display_name):
        score += 2
    if ADDRESS_NAME.match(display_name):
        score -= 3
    if HOLDING_WORDS.search(display_name):
        score -= 2
    if n_locations >= 10:
        score += 4
    elif n_locations >= 4:
        score += 3
    elif n_locations >= 2:
        score += 1
    return score


def main():
    rows = fetch()
    print("fetched {} active real-estate business registrations".format(len(rows)))

    firms = defaultdict(lambda: {"addresses": set(), "dbas": set(), "naics": set(),
                                 "hoods": set(), "names": []})
    for r in rows:
        owner = r.get("ownership_name") or r.get("dba_name")
        if not owner:
            continue
        key = norm(owner)
        if not key:
            continue
        f = firms[key]
        f["names"].append(owner.strip())
        if r.get("dba_name"):
            f["dbas"].add(r["dba_name"].strip())
        addr = ", ".join(x for x in (r.get("full_business_address"), r.get("city")) if x)
        if addr:
            f["addresses"].add(addr)
        if r.get("self_reported_naics_code"):
            f["naics"].add(r["self_reported_naics_code"])
        if r.get("neighborhoods_analysis_boundaries"):
            f["hoods"].add(r["neighborhoods_analysis_boundaries"])
        f.setdefault("mail", "")
        if not f["mail"] and r.get("mailing_address_1"):
            f["mail"] = ", ".join(
                x for x in (r.get("mailing_address_1"), r.get("mail_city"),
                            r.get("mail_zipcode")) if x
            )

    out = []
    for key, f in firms.items():
        # Most common spelling of the name wins.
        display = max(set(f["names"]), key=f["names"].count)
        n_loc = len(f["addresses"])
        score = classify(display, n_loc)
        if score < 2:
            continue
        target_hoods = f["hoods"] & {
            "Mission", "Nob Hill", "Inner Richmond", "Outer Richmond",
            "Haight Ashbury", "Noe Valley",
        }
        out.append({
            "company": display,
            "locations_in_sf": n_loc,
            "score": score,
            "in_target_hoods": ", ".join(sorted(target_hoods)),
            "naics": ", ".join(sorted(f["naics"])),
            "dbas": " | ".join(sorted(f["dbas"])[:3]),
            "mailing_address": f.get("mail", ""),
            "sample_addresses": " | ".join(sorted(f["addresses"])[:3]),
        })

    out.sort(key=lambda x: (-x["locations_in_sf"], -x["score"], x["company"]))

    cols = ["company", "locations_in_sf", "score", "in_target_hoods", "naics",
            "dbas", "mailing_address", "sample_addresses"]
    with open("management_companies.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(out)

    multi = [x for x in out if x["locations_in_sf"] >= 4]
    print("wrote management_companies.csv -- {} candidate firms".format(len(out)))
    print("  {} operate at 4+ SF addresses (strongest management signal)".format(len(multi)))
    print("\ntop 25 by SF footprint:")
    for x in out[:25]:
        print("  {:>4} addrs  {}".format(x["locations_in_sf"], x["company"]))


if __name__ == "__main__":
    main()
