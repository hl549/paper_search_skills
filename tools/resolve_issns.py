#!/usr/bin/env python3
"""Resolve authoritative ISSNs from OpenAlex source records (never guess)."""
import urllib.request, json, time

UA = {"User-Agent": "Mozilla/5.0 paper-digest/1.0"}
NAMES = [
    "Journal of Geophysical Research: Atmospheres",
    "Atmospheric Environment",
    "Journal of Volcanology and Geothermal Research",
]
for name in NAMES:
    u = ("https://api.openalex.org/sources?search=" + urllib.parse.quote(name)
         + "&per-page=5")
    req = urllib.request.Request(u, headers=UA)
    d = json.load(urllib.request.urlopen(req, timeout=30))
    print(f"### {name}")
    for s in (d.get("results") or [])[:3]:
        issns = [i for i in (s.get("issn_l"), s.get("issn_e")) if i]
        print(f"  - {s['display_name']} | ISSN: {issns} | works~{s.get('works_count')}")
    time.sleep(1)
