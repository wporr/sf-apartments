#!/usr/bin/env python3
"""
Assemble the three deliverables from probe_results.json + portfolio_map.csv:

  LIST A  portfolio_map.csv       building -> management group, by neighborhood (built earlier;
                                  this script re-filters it to Liam's exact criteria)
  LIST B  groups_incomplete.csv   groups whose portfolio picture is missing/incomplete:
                                  website, basic info, contact info from their site
  LIST C  listings_found.csv      live listings from the groups' own websites,
                                  by neighborhood, Inner Richmond first

Usage:  python3 assemble_lists.py
"""

import csv
import json
import os
import re
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PERMITS_API = "https://data.sfgov.org/resource/i98e-djp9.json"

HOOD_ORDER = ["Inner Richmond", "Central Richmond", "Cole Valley",
              "Noe Valley", "Mission", "Nob Hill"]
BBOX = {
    "Cole Valley":      (-122.4530, -122.4450, 37.7620, 37.7695),
    "Central Richmond": (-122.4850, -122.4730, 37.7720, 37.7870),
}
NON_SF = re.compile(r"\b(oakland|berkeley|santa rosa|san jose|daly city|alameda|"
                    r"emeryville|richmond,|el cerrito|san mateo|burlingame|sausalito|"
                    r"mill valley|vallejo|hayward|concord|walnut creek|petaluma|novato|"
                    r"san rafael|redwood city|pacifica|south san francisco)\b", re.I)
LIGHT = re.compile(r"\b(bay window|bay-window|sun[- ]?drenched|sunny|bright|natural light|"
                   r"south[- ]facing|west[- ]facing|top floor|corner unit|skylight|"
                   r"light[- ]filled|airy)\b", re.I)
WOOD = re.compile(r"\b(hardwood|wood floors?|refinished floors)\b", re.I)

_cache = {}


def geocode_hood(address):
    """address -> (official hood or '', refined hood honoring Cole Valley/Central Richmond)"""
    m = re.search(r"\b(\d{1,5})[\s-]+([A-Za-z0-9][A-Za-z0-9'\. ]*?)"
                  r"\s*(?:St|Street|Ave|Avenue|Av|Blvd|Boulevard|Rd|Dr|Way|Pl|Ter|Ct|Ln)\.?\b",
                  address, re.I)
    if not m:
        return ""
    num, street = m.group(1), m.group(2).strip()
    key = (num, street.lower())
    if key in _cache:
        return _cache[key]
    where = "street_number='{}' AND upper(street_name)=upper('{}')".format(
        num, street.replace("'", "''"))
    url = PERMITS_API + "?" + urllib.parse.urlencode({
        "$select": "neighborhoods_analysis_boundaries,location",
        "$where": where, "$limit": "1"})
    hood = ""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
        if data:
            hood = data[0].get("neighborhoods_analysis_boundaries") or ""
            coords = (data[0].get("location") or {}).get("coordinates")
            if coords:
                lon, lat = coords
                for name, (lo, hi, la, ha) in BBOX.items():
                    if lo <= lon <= hi and la <= lat <= ha:
                        if name == "Cole Valley" and hood == "Haight Ashbury":
                            hood = name
                        elif name == "Central Richmond" and hood in ("Inner Richmond", "Outer Richmond"):
                            hood = name
    except Exception:
        pass
    _cache[key] = hood
    return hood


def main():
    with open(os.path.join(HERE, "probe_results.json")) as f:
        probes = json.load(f)
    extra = []
    xpath = os.path.join(HERE, "probe_extra.json")
    if os.path.exists(xpath):
        extra = json.load(open(xpath))

    # ------------------------------------------------------------------ #
    # LIST C -- listings from the groups' own sites
    # ------------------------------------------------------------------ #
    listings = []
    for p in probes:
        for l in p["listings"]:
            listings.append(dict(l, company=p["name"]))
    listings.extend(extra)

    rows_c = []
    for l in listings:
        addr = l.get("address", "")
        blob = "{} {}".format(l.get("title", ""), addr)
        if NON_SF.search(blob):
            continue
        # If the address names its city, require San Francisco -- otherwise the
        # geocoder happily matches an out-of-town street against SF records.
        city = re.search(r",\s*([A-Za-z .]+?),?\s*(?:CA|California)\b", addr, re.I)
        if city and "san franc" not in city.group(1).lower():
            continue
        if not city and not re.search(r"san franc", addr, re.I):
            # No city stated: only trust companies that list SF-only inventory.
            if l["company"] not in ("Meridian Property Management Group", "Anchor Realty"):
                continue
        hood = geocode_hood(addr)
        rows_c.append({
            "neighborhood": hood if hood in HOOD_ORDER else (hood or "unresolved"),
            "in_criteria": "yes" if hood in HOOD_ORDER else "",
            "address": addr,
            "rent": l.get("rent", ""),
            "company": l["company"],
            "light_words": ", ".join(sorted({w.lower() for w in LIGHT.findall(blob)})),
            "wood_floors": "yes" if WOOD.search(blob) else "",
            "available": l.get("available", ""),
            "title": (l.get("title") or "")[:100],
            "url": l.get("url", ""),
        })
    order = {h: i for i, h in enumerate(HOOD_ORDER)}
    rows_c.sort(key=lambda r: (0 if r["in_criteria"] else 1,
                               order.get(r["neighborhood"], 99), r["rent"] or 0))
    with open(os.path.join(HERE, "listings_found.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["neighborhood", "in_criteria", "address", "rent",
                                          "company", "light_words", "wood_floors",
                                          "available", "title", "url"])
        w.writeheader(); w.writerows(rows_c)

    # ------------------------------------------------------------------ #
    # LIST B -- groups with an incomplete portfolio picture
    # ------------------------------------------------------------------ #
    registry = {}
    gpath = os.path.join(HERE, "groups_ranked.csv")
    if os.path.exists(gpath):
        for r in csv.DictReader(open(gpath)):
            registry[re.sub(r"\W+", "", r["company"].lower())] = int(r["buildings_in_target_hoods"])

    def registry_buildings(name):
        key = re.sub(r"\W+", "", name.lower())
        best = 0
        for k, v in registry.items():
            if key[:10] and (key[:10] in k or k[:10] in key):
                best = max(best, v)
        return best

    rows_b = []
    for p in probes:
        known = registry_buildings(p["name"])
        # "complete enough" = we already mapped 3+ of their buildings via the registry
        if known >= 3:
            continue
        rows_b.append({
            "company": p["name"],
            "website": p["site"],
            "platform": p["platform"] or "",
            "live_listings_found": len(p["listings"]),
            "buildings_mapped_from_registry": known,
            "phones": " | ".join(p["phones"]),
            "emails": " | ".join(p["emails"]),
            "contact_pages": " | ".join(p["contact_pages"][:2]),
            "notes": p["error"][:120] if p["error"] else "",
        })
    rows_b.sort(key=lambda r: (-r["live_listings_found"], r["company"]))
    with open(os.path.join(HERE, "groups_incomplete.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["company", "website", "platform",
                                          "live_listings_found", "buildings_mapped_from_registry",
                                          "phones", "emails", "contact_pages", "notes"])
        w.writeheader(); w.writerows(rows_b)

    # ------------------------------------------------------------------ #
    # LIST A -- re-filter the portfolio map to Liam's exact criteria
    # (drop Outer Richmond, which was over-included on the first pass)
    # ------------------------------------------------------------------ #
    ppath = os.path.join(HERE, "portfolio_map.csv")
    rows_a = [r for r in csv.DictReader(open(ppath)) if r["neighborhood"] in HOOD_ORDER]
    rows_a.sort(key=lambda r: (order[r["neighborhood"]],
                               -int(r["company_buildings_in_target"] or 0), r["company"]))
    with open(ppath, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows_a[0].keys()))
        w.writeheader(); w.writerows(rows_a)

    # ------------------------------------------------------------------ #
    print("LIST A  portfolio_map.csv      {} buildings".format(len(rows_a)))
    print("LIST B  groups_incomplete.csv  {} groups".format(len(rows_b)))
    print("LIST C  listings_found.csv     {} listings ({} in criteria)".format(
        len(rows_c), sum(1 for r in rows_c if r["in_criteria"])))
    print("\nin-criteria listings:")
    for r in rows_c:
        if r["in_criteria"]:
            print("  {:16} ${:<6} {:34} {:24} {}".format(
                r["neighborhood"], r["rent"], r["address"][:34], r["company"][:24],
                r["light_words"][:26]))


if __name__ == "__main__":
    main()
