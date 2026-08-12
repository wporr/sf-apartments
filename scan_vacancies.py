#!/usr/bin/env python3
"""
Scan San Francisco property management companies' own websites for 1BR vacancies
matching Liam's criteria, before those units reach the big listing sites.

Most managers publish inventory through one of a few platforms, so a small number
of adapters covers a lot of companies:

  appfolio -- <sub>.appfolio.com/listings, uniform markup AND server-side filters
  wpl      -- the WP Property Listing plugin; detail URLs encode address/beds/price
  (add more adapters as new platforms turn up)

Companies live in companies.json so the list can grow without touching this file.

Usage:  python3 scan_vacancies.py
"""

import csv
import json
import os
import re
import ssl
import urllib.parse
import urllib.request
from html import unescape

MAX_RENT = 4000
WANT_BEDS = 1
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}

# Street names are useless for this -- Geary, California and Bush each run across
# half the city. Resolve the real neighborhood by looking the address up in SF's
# permit records, which carry an official neighborhood boundary per address.
TARGET_HOODS = {"Mission", "Nob Hill", "Inner Richmond", "Outer Richmond",
                "Haight Ashbury", "Noe Valley"}
PERMITS_API = "https://data.sfgov.org/resource/i98e-djp9.json"
_hood_cache = {}

# Words that signal the light Liam actually cares about.
LIGHT = re.compile(r"\b(bay window|bay-window|sun[- ]?drenched|sunny|bright|"
                   r"natural light|south[- ]facing|west[- ]facing|top floor|"
                   r"corner unit|skylight|light[- ]filled|airy)\b", re.I)
WOOD = re.compile(r"\b(hardwood|wood floor|wood floors|refinished floors)\b", re.I)

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def get(url, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
        return r.read().decode("utf-8", "ignore")


def strip_tags(s):
    return unescape(re.sub(r"<[^>]+>", " ", s or "")).replace("\xa0", " ").strip()


def money(s):
    m = re.search(r"\$\s*([\d,]+)", s or "")
    return int(m.group(1).replace(",", "")) if m else None


def match_hood(address):
    """Resolve street number + street name to SF's official neighborhood boundary.

    Uses the building-permit dataset as a free geocoder: it carries an official
    neighborhood per address, which beats guessing from the street name.
    """
    m = re.search(r"\b(\d{1,5})\s+([A-Za-z][A-Za-z0-9'\. ]+?)\b"
                  r"(?:\s+(st|street|ave|avenue|blvd|rd|way|pl|place|dr|drive|ct|ter|terrace))?\b",
                  address, re.I)
    if not m:
        return ""
    num, street = m.group(1), m.group(2).strip()
    key = (num, street.lower())
    if key in _hood_cache:
        return _hood_cache[key]

    where = "street_number='{}' AND upper(street_name)=upper('{}')".format(
        num, street.replace("'", "''")
    )
    url = PERMITS_API + "?" + urllib.parse.urlencode({
        "$select": "neighborhoods_analysis_boundaries",
        "$where": where,
        "$limit": "1",
    })
    hood = ""
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30,
                                    context=CTX) as r:
            data = json.load(r)
        if data:
            hood = data[0].get("neighborhoods_analysis_boundaries", "") or ""
    except Exception:
        hood = ""
    _hood_cache[key] = hood
    return hood


# --------------------------------------------------------------------------- #
# Adapters
# --------------------------------------------------------------------------- #

def scan_appfolio(company):
    """AppFolio portals accept server-side filters, so ask for 1BR under budget."""
    base = "https://{}.appfolio.com/listings".format(company["appfolio_sub"])
    qs = urllib.parse.urlencode({
        "filters[market_rent_to]": MAX_RENT,
        "filters[bedrooms]": WANT_BEDS,
    })
    html = get(base + "?" + qs)
    out = []
    # Each result is a <div class="listing-item result js-listing-item"> block.
    for chunk in re.split(r'<div class="listing-item result js-listing-item"', html)[1:]:
        block = chunk[:6000]
        text = strip_tags(block)
        link = re.search(r'href="(/listings/detail/[^"]+)"', block)
        addr = re.search(r'class="js-listing-address[^"]*"[^>]*>(.*?)<', block, re.S)
        if not addr:
            addr = re.search(r"<h[23][^>]*>(.*?)</h[23]>", block, re.S)
        rent = money(text)
        beds = re.search(r"([\d.]+)\s*(?:bd|bed)", text, re.I)
        out.append({
            "company": company["name"],
            "address": strip_tags(addr.group(1)) if addr else "",
            "rent": rent,
            "beds": beds.group(1) if beds else "",
            "url": ("https://{}.appfolio.com".format(company["appfolio_sub"]) + link.group(1))
                   if link else base,
            "blurb": text[:400],
        })
    return out


def scan_wpl(company):
    """WP Property Listing: the detail URL encodes address, bedrooms and price."""
    html = get(company["vacancies_url"])
    out, seen = [], set()
    pat = re.compile(r'href="(https?://[^"]*/properties/[^"]+)"')
    for url in pat.findall(html):
        if url in seen:
            continue
        seen.add(url)
        slug = url.rstrip("/").rsplit("/", 1)[-1]
        beds = re.search(r"-(\d+)-Bedrooms?-", slug)
        rent = re.search(r"USD([\d\-]+)", slug)
        rent_val = int(rent.group(1).replace("-", "")) if rent else None
        addr = re.sub(r"^\d+-(Apartment|Home|Condo|Flat)-", "", slug)
        addr = re.sub(r"-(San-Francisco|California|United-States).*$", "", addr).replace("-", " ")
        out.append({
            "company": company["name"],
            "address": addr.strip(),
            "rent": rent_val,
            "beds": beds.group(1) if beds else "",
            "url": url,
            "blurb": "",
        })
    return out


ADAPTERS = {"appfolio": scan_appfolio, "wpl": scan_wpl}


# --------------------------------------------------------------------------- #

def main():
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "companies.json")) as f:
        companies = json.load(f)

    rows, errors = [], []
    for c in companies:
        fn = ADAPTERS.get(c.get("platform"))
        if not fn:
            errors.append("{}: no adapter for platform '{}'".format(c["name"], c.get("platform")))
            continue
        try:
            found = fn(c)
            rows.extend(found)
            print("  {:38} {} listings".format(c["name"], len(found)))
        except Exception as e:
            errors.append("{}: {}".format(c["name"], e))
            print("  {:38} FAILED ({})".format(c["name"], type(e).__name__))

    # Filter to what Liam is actually looking for.
    hits = []
    for r in rows:
        if r["rent"] is None or r["rent"] > MAX_RENT:
            continue
        # Require an explicit 1-bedroom. Listings with no bed count are usually
        # studios or SROs, and letting them through poisoned earlier runs.
        if not str(r["beds"]).startswith(str(WANT_BEDS)):
            continue
        hood = match_hood(r["address"])
        blob = r["address"] + " " + r.get("blurb", "")
        r.update({
            "neighborhood": hood,
            "light_words": ", ".join(sorted(set(w.lower() for w in LIGHT.findall(blob)))),
            "wood_floors": "yes" if WOOD.search(blob) else "",
        })
        hits.append(r)

    # Sunlight first, exactly as ranked everywhere else in this project.
    hits.sort(key=lambda r: (0 if r["light_words"] else 1,
                             0 if r["neighborhood"] else 1,
                             r["rent"]))

    cols = ["company", "address", "neighborhood", "rent", "beds",
            "light_words", "wood_floors", "url"]
    with open(os.path.join(here, "vacancies.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(hits)

    print("\n{} total listings scraped, {} match 1BR under ${:,}".format(
        len(rows), len(hits), MAX_RENT))
    in_hood = [h for h in hits if h["neighborhood"] in TARGET_HOODS]
    print("{} of those are in your target areas".format(len(in_hood)))
    for h in in_hood[:20]:
        print("  ${:<6} {:34} {:14} {}".format(
            h["rent"], h["address"][:34], h["neighborhood"][:14], h["light_words"][:30]))
    if errors:
        print("\nerrors:")
        for e in errors:
            print("  " + e)


if __name__ == "__main__":
    main()
