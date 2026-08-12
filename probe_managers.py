#!/usr/bin/env python3
"""
Probe SF residential management group websites for the three-list workflow.

For each company in roster.json:
  1. fetch the homepage; detect listing platform (AppFolio/Buildium/RentCafe/self-hosted)
  2. find and fetch vacancy/listing pages; parse listings
  3. find contact/team pages; extract phone numbers and emails
  4. record everything to probe_results.json for downstream assembly

Read-only web fetches, throttled, standard UA. No form submissions.

Usage:  python3 probe_managers.py
"""

import json
import os
import re
import ssl
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

PLATFORMS = {
    "appfolio": r"([a-z0-9\-]+)\.appfolio\.com",
    "buildium": r"([a-z0-9\-]+)\.managebuilding\.com",
    "rentcafe": r"rentcafe\.com",
    "rentvine": r"([a-z0-9\-]+)\.rentvine\.com",
    "tenantturner": r"tenantturner\.com",
}

PHONE = re.compile(r"\(?\b(415|628|510|650)\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")
EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.(?:com|net|org|us|io)\b")
NAV_WORDS = re.compile(r"vacanc|listing|available|rentals|for-rent|apartments|properties|portfolio", re.I)
CONTACT_WORDS = re.compile(r"contact|about|team|staff|our-people|agents|management", re.I)


def get(url, timeout=25):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
        return r.geturl(), r.read().decode("utf-8", "ignore")


def links(html, base):
    out = []
    for href, txt in re.findall(r'<a[^>]+href="([^"#]+)"[^>]*>(.*?)</a>', html, re.S | re.I):
        t = re.sub(r"<[^>]+>", " ", txt)
        t = re.sub(r"\s+", " ", t).strip()
        out.append((urllib.parse.urljoin(base, href), t))
    return out


def detect_platform(html):
    for name, pat in PLATFORMS.items():
        m = re.search(pat, html, re.I)
        if m:
            sub = m.group(1) if m.groups() else ""
            return name, sub
    return "", ""


def parse_appfolio(sub):
    """Server-side filtered: 1BR, <= $4000."""
    url = ("https://{}.appfolio.com/listings?".format(sub) +
           urllib.parse.urlencode({"filters[market_rent_to]": 4000, "filters[bedrooms]": 1}))
    _, html = get(url)
    out = []
    for chunk in re.split(r'class="listing-item result js-listing-item"', html)[1:]:
        block = chunk[:8000]
        addr = re.search(r'class="[^"]*js-listing-address[^"]*"[^>]*>(.*?)</span>', block, re.S)
        rent = re.search(r"\$\s*([\d,]+)", block)
        link = re.search(r'href="(/listings/detail/[^"]+)"', block)
        title = re.search(r'js-listing-title">\s*<a[^>]*>(.*?)</a>', block, re.S)
        avail = re.search(r'js-listing-available">([^<]+)<', block)
        if not (addr and rent):
            continue
        out.append({
            "address": re.sub(r"\s+", " ", addr.group(1)).strip(),
            "rent": int(rent.group(1).replace(",", "")),
            "beds": 1,
            "title": re.sub(r"\s+", " ", title.group(1)).strip() if title else "",
            "available": avail.group(1).strip() if avail else "",
            "url": "https://{}.appfolio.com{}".format(sub, link.group(1)) if link else url,
        })
    return out


def parse_wpl(vac_url):
    _, html = get(vac_url)
    out, seen = [], set()
    for url in re.findall(r'href="(https?://[^"]*/properties/[^"]+)"', html):
        if url in seen:
            continue
        seen.add(url)
        slug = url.rstrip("/").rsplit("/", 1)[-1]
        beds = re.search(r"-(\d+)-Bedrooms?-", slug)
        rent = re.search(r"USD([\d\-]+)", slug)
        if not (beds and rent and beds.group(1) == "1"):
            continue
        rent_val = int(rent.group(1).replace("-", ""))
        if rent_val > 4000:
            continue
        addr = re.sub(r"^\d+-(Apartment|Home|Condo|Flat)-", "", slug)
        addr = re.sub(r"-(San-Francisco|California|United-States).*$", "", addr).replace("-", " ")
        out.append({"address": addr.strip(), "rent": rent_val, "beds": 1,
                    "title": "", "available": "", "url": url})
    return out


def scan_generic_listings(pages):
    """Best-effort: pull $-amounts + nearby address-looking text from arbitrary pages."""
    out = []
    for _, html in pages:
        text = re.sub(r"(?s)<(script|style).*?</\1>", " ", html)
        for m in re.finditer(
            r"(\d{2,5}\s+[A-Z][A-Za-z' ]+(?:St|Street|Ave|Avenue|Blvd|Dr|Way|Pl|Ter)\.?)"
            r"[^$]{0,400}\$\s*([\d,]{4,6})", text):
            rent = int(m.group(2).replace(",", ""))
            if 1500 <= rent <= 4000:
                out.append({"address": m.group(1).strip(), "rent": rent, "beds": "",
                            "title": "", "available": "", "url": ""})
    return out


def probe(company):
    rec = {"name": company["name"], "site": company["site"], "platform": "",
           "listings": [], "phones": [], "emails": [], "contact_pages": [],
           "portfolio_pages": [], "error": ""}
    try:
        base, home = get(company["site"])
    except Exception as e:
        rec["error"] = "homepage: {}".format(e)
        return rec

    platform, sub = detect_platform(home)
    rec["platform"] = platform + (":" + sub if sub else "")
    all_links = links(home, base)

    nav = [(u, t) for u, t in all_links if NAV_WORDS.search(u + " " + t)]
    contact = [(u, t) for u, t in all_links if CONTACT_WORDS.search(u + " " + t)]

    # -- listings --------------------------------------------------------- #
    try:
        if platform == "appfolio" and sub:
            rec["listings"] = parse_appfolio(sub)
        elif re.search(r"/properties/", home):
            rec["listings"] = parse_wpl(base)
        else:
            wpl_page = next((u for u, t in nav if re.search(r"san.?francisco|vacanc|rental", u, re.I)), None)
            if wpl_page:
                try:
                    rec["listings"] = parse_wpl(wpl_page)
                except Exception:
                    pass
            if not rec["listings"]:
                pages = []
                for u, _ in nav[:3]:
                    if u.startswith(base.rstrip("/")):
                        try:
                            pages.append(get(u))
                            time.sleep(1)
                        except Exception:
                            pass
                rec["listings"] = scan_generic_listings(pages)
                rec["portfolio_pages"] = [u for u, _ in nav[:6]]
    except Exception as e:
        rec["error"] += " listings: {}".format(e)

    # -- contacts --------------------------------------------------------- #
    blobs = [home]
    for u, _ in contact[:3]:
        if u.startswith(base.rstrip("/")):
            try:
                _, h = get(u)
                blobs.append(h)
                rec["contact_pages"].append(u)
                time.sleep(1)
            except Exception:
                pass
    joined = " ".join(blobs)
    rec["phones"] = sorted(set(re.sub(r"[^\d]", "", p)
                               for p in PHONE.findall(joined)))[:6]
    # PHONE.findall returns only the area-code group; refind full numbers
    rec["phones"] = sorted(set(m.group(0).strip() for m in PHONE.finditer(joined)))[:6]
    rec["emails"] = sorted(set(e.lower() for e in EMAIL.findall(joined)
                               if not re.search(r"example|sentry|wixpress|schema", e)))[:6]
    return rec


def main():
    with open(os.path.join(HERE, "roster.json")) as f:
        roster = json.load(f)
    results = []
    for c in roster:
        print("probing {:44}".format(c["name"][:44]), end="", flush=True)
        rec = probe(c)
        n = len(rec["listings"])
        print(" platform={:18} listings={:<3} phones={} {}".format(
            rec["platform"] or "-", n, len(rec["phones"]),
            "ERR " + rec["error"][:40] if rec["error"] else ""))
        results.append(rec)
        time.sleep(2)
    with open(os.path.join(HERE, "probe_results.json"), "w") as f:
        json.dump(results, f, indent=1)
    print("\nwrote probe_results.json ({} companies)".format(len(results)))


if __name__ == "__main__":
    main()
