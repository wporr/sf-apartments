#!/usr/bin/env python3
"""
LIST A -- map buildings to the management group / owner that operates them.

Method: SF's business registry records every address a business is registered at,
with an official neighborhood per location. For a management company or a landlord
with a portfolio, those addresses ARE the buildings. Cross-referencing them against
the apartment-building list from permit records (buildings.csv) confirms which of
those addresses are actually rental buildings rather than back offices.

Filters out companies that don't handle residential rentals: commercial brokerages,
and the service trades (title, escrow, mortgage, construction, staging, ...).

Outputs:
  portfolio_map.csv     building -> company, target neighborhoods only, Inner Richmond first
  groups_ranked.csv     one row per company, for the follow-up website/contact work

Usage:  python3 portfolio_map.py
"""

import csv
import json
import os
import re
import urllib.parse
import urllib.request
from collections import defaultdict

API = "https://data.sfgov.org/resource/g8m3-pdis.json"
NAICS = ["531311", "531312", "531390", "531210", "531110", "53"]

HERE = os.path.dirname(os.path.abspath(__file__))

# Liam's areas. SF has no "Cole Valley"/"Central Richmond" boundary, so those two
# are carved out of their parent neighborhood by bounding box (same as target_buildings.py).
PARENT_HOODS = {"Mission", "Nob Hill", "Inner Richmond", "Outer Richmond",
                "Haight Ashbury", "Noe Valley"}
BBOX = {
    "Cole Valley":      (-122.4530, -122.4450, 37.7620, 37.7695),
    "Central Richmond": (-122.4850, -122.4730, 37.7720, 37.7870),
}
# Inner Richmond first, per Liam's priority.
HOOD_ORDER = ["Inner Richmond", "Central Richmond", "Outer Richmond", "Cole Valley",
              "Noe Valley", "Mission", "Nob Hill"]

# Commercial-only shops -- they don't do residential rentals.
COMMERCIAL = re.compile(
    r"\b(cbre|jones lang|lasalle|jll|cushman|wakefield|colliers|newmark|savills|"
    r"transwestern|avison|kidder|marcus & millichap|eastdil|hines|boston properties|"
    r"kilroy|tishman|shorenstein|prologis|digital realty)\b", re.I)

# Real-estate-adjacent trades that aren't landlords or managers.
NOT_RENTAL = re.compile(
    r"\b(title|escrow|mortgage|lending|loan|apprais|insurance|architect|"
    r"construction|contracting|builders|remodel|staging|photograph|inspection|"
    r"cleaning|janitorial|landscap|roofing|plumbing|electric|law offices|attorney|"
    r"consulting|advisors|capital partners|securities|development co)\b", re.I)

STREET_SUFFIX = re.compile(
    r"\b(st|street|ave|avenue|blvd|boulevard|rd|road|dr|drive|way|wy|pl|place|"
    r"ct|court|ter|terrace|ln|lane|cir|circle|plz|plaza|park|hwy)\b\.?", re.I)
UNIT_PART = re.compile(r"\b(apt|apartment|unit|ste|suite|fl|floor|#)\b.*$", re.I)


def norm_addr(addr):
    """'1140 Pine St #3' -> '1140 PINE' so registry and permit addresses line up."""
    a = (addr or "").split(",")[0]
    a = UNIT_PART.sub("", a)
    a = re.sub(r"#.*$", "", a)
    a = STREET_SUFFIX.sub("", a)
    a = re.sub(r"[^A-Za-z0-9 ]", " ", a)
    return re.sub(r"\s+", " ", a).strip().upper()


def norm_name(name):
    n = (name or "").strip().lower()
    n = re.sub(r"[.,]", "", n)
    n = re.sub(r"\b(llc|l l c|inc|incorporated|lp|l p|llp|corp|corporation|co)\b", "", n)
    return re.sub(r"\s+", " ", n).strip()


def refine_hood(hood, loc):
    """Carve Cole Valley / Central Richmond out of their parent boundaries."""
    coords = (loc or {}).get("coordinates")
    if coords:
        lon, lat = coords[0], coords[1]
        for name, (lo, hi, la, ha) in BBOX.items():
            if lo <= lon <= hi and la <= lat <= ha:
                if name == "Cole Valley" and hood == "Haight Ashbury":
                    return name
                if name == "Central Richmond" and hood in ("Inner Richmond", "Outer Richmond"):
                    return name
    return hood


def fetch():
    where = "location_end_date IS NULL AND self_reported_naics_code in ({})".format(
        ",".join("'{}'".format(c) for c in NAICS))
    page, offset, out = 50000, 0, []
    while True:
        params = {
            "$select": ("ownership_name,dba_name,full_business_address,city,business_zip,"
                        "mailing_address_1,mail_city,self_reported_naics_code,"
                        "neighborhoods_analysis_boundaries,location,location_start_date"),
            "$where": where, "$order": ":id",
            "$limit": str(page), "$offset": str(offset),
        }
        with urllib.request.urlopen(API + "?" + urllib.parse.urlencode(params),
                                    timeout=120) as r:
            batch = json.load(r)
        out.extend(batch)
        if len(batch) < page:
            return out
        offset += page


def load_apartment_buildings():
    """address key -> (units, neighborhood) for known apartment buildings."""
    path = os.path.join(HERE, "buildings.csv")
    idx = {}
    if not os.path.exists(path):
        print("  ! buildings.csv missing -- run target_buildings.py first")
        return idx
    for r in csv.DictReader(open(path)):
        idx[norm_addr(r["address"])] = (int(r["units"]), r["neighborhood"])
    return idx


def main():
    rows = fetch()
    print("fetched {} active real-estate registrations".format(len(rows)))
    apts = load_apartment_buildings()
    print("cross-referencing against {} known apartment buildings".format(len(apts)))

    firms = defaultdict(lambda: {"names": [], "naics": set(), "locs": {}, "mail": ""})
    for r in rows:
        owner = r.get("ownership_name") or r.get("dba_name")
        if not owner:
            continue
        key = norm_name(owner)
        if not key:
            continue
        f = firms[key]
        f["names"].append(owner.strip())
        if r.get("self_reported_naics_code"):
            f["naics"].add(r["self_reported_naics_code"])
        if not f["mail"] and r.get("mailing_address_1"):
            f["mail"] = "{}, {}".format(r["mailing_address_1"], r.get("mail_city", ""))
        addr = r.get("full_business_address")
        if not addr or (r.get("city") or "").lower() != "san francisco":
            continue
        hood = refine_hood(r.get("neighborhoods_analysis_boundaries", ""), r.get("location"))
        f["locs"][norm_addr(addr)] = {"addr": addr.strip(), "hood": hood}

    building_rows, firm_rows = [], []
    for key, f in firms.items():
        display = max(set(f["names"]), key=f["names"].count)
        if COMMERCIAL.search(display) or NOT_RENTAL.search(display):
            continue
        # Nonresidential-only filers aren't relevant.
        if f["naics"] and f["naics"].issubset({"531120", "531312"}):
            continue

        # A registered address counts as a portfolio building when it is a known
        # apartment building, or sits in a target neighborhood for a multi-site firm.
        portfolio = []
        for akey, loc in f["locs"].items():
            in_apts = akey in apts
            units, apt_hood = apts.get(akey, (None, None))
            hood = apt_hood or loc["hood"]
            if hood not in HOOD_ORDER:
                continue
            if not in_apts and len(f["locs"]) < 2:
                continue  # lone non-apartment address is probably just an office
            portfolio.append({"address": loc["addr"], "hood": hood,
                              "units": units or "", "confirmed_apartment": "yes" if in_apts else ""})

        if not portfolio:
            continue

        confirmed = sum(1 for p in portfolio if p["confirmed_apartment"])
        for p in portfolio:
            building_rows.append({
                "neighborhood": p["hood"], "address": p["address"], "units": p["units"],
                "confirmed_apartment": p["confirmed_apartment"], "company": display,
                "naics": ", ".join(sorted(f["naics"])),
                "company_buildings_in_target": len(portfolio),
            })
        firm_rows.append({
            "company": display,
            "buildings_in_target_hoods": len(portfolio),
            "confirmed_apartment_buildings": confirmed,
            "total_sf_addresses": len(f["locs"]),
            "hoods": ", ".join(sorted({p["hood"] for p in portfolio})),
            "inner_richmond": sum(1 for p in portfolio if p["hood"] == "Inner Richmond"),
            "naics": ", ".join(sorted(f["naics"])),
            "mailing_address": f["mail"],
            "sample_buildings": " | ".join(p["address"] for p in portfolio[:4]),
        })

    order = {h: i for i, h in enumerate(HOOD_ORDER)}
    building_rows.sort(key=lambda r: (order[r["neighborhood"]], -(r["units"] or 0)
                                      if isinstance(r["units"], int) else 0, r["address"]))
    firm_rows.sort(key=lambda r: (-r["inner_richmond"], -r["buildings_in_target_hoods"],
                                  r["company"]))

    with open(os.path.join(HERE, "portfolio_map.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["neighborhood", "address", "units",
                                           "confirmed_apartment", "company", "naics",
                                           "company_buildings_in_target"])
        w.writeheader(); w.writerows(building_rows)

    with open(os.path.join(HERE, "groups_ranked.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["company", "buildings_in_target_hoods",
                                           "confirmed_apartment_buildings", "inner_richmond",
                                           "total_sf_addresses", "hoods", "naics",
                                           "mailing_address", "sample_buildings"])
        w.writeheader(); w.writerows(firm_rows)

    print("\nportfolio_map.csv -- {} buildings mapped to {} companies".format(
        len(building_rows), len(firm_rows)))
    by_hood = defaultdict(int)
    for r in building_rows:
        by_hood[r["neighborhood"]] += 1
    for h in HOOD_ORDER:
        if by_hood[h]:
            print("  {:18} {}".format(h, by_hood[h]))
    print("\ntop companies in Inner Richmond:")
    for r in firm_rows[:12]:
        if not r["inner_richmond"]:
            break
        print("  {:>2} IR / {:>3} total  {}".format(
            r["inner_richmond"], r["buildings_in_target_hoods"], r["company"]))


if __name__ == "__main__":
    main()
