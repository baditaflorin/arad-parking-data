#!/usr/bin/env python3
"""Match source-listed streets to actual OSM street REFERENCE geometry.

Never claims the road geometry is the parking footprint. A reviewed name mapping
expands abbreviations; constrained segments are explicitly kept as whole-street
references. No ambiguous fuzzy result or municipal centre fallback is accepted.
One cached Overpass request supplies all coordinates, avoiding bulk geocoding.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from scrape_recons import ROOT, fold

ENDPOINT = "https://overpass-api.de/api/interpreter"
QUERY = '[out:json][timeout:60];way["highway"]["name"](46.145,21.28,46.205,21.36);out geom;'

# Canonical OSM names reviewed against the source abbreviations and local OSM
# names. This is a transparent matching decision, not an authoritative renaming.
NAME_EXPANSIONS = {
    "e teodoroiu": "ecaterina teodoroiu", "v alecsandri": "vasile alecsandri",
    "ghe popa de teius": "gheorghe popa de teius", "i c bratianu": "ion c bratianu",
    "n grigorescu": "nicolae grigorescu", "xenopol": "a d xenopol",
    "n balcescu": "nicolae balcescu", "i sava": "aviator ioan sava",
    "gh lazar": "gheorghe lazar", "ghe magheru": "general gheorghe magheru",
    "st a doinas": "stefan augustin doinas", "m stanescu": "mircea stanescu",
    "t vladimirescu": "tudor vladimirescu", "gh cosbuc": "george cosbuc",
    "general dragalina": "general ion dragalina", "a muresanu": "andrei muresanu",
    "o goga": "octavian goga", "i georgescu": "ion georgescu",
    "m eminescu": "mihai eminescu", "i andreescu": "ion andreescu",
    "gen traian mosoiu": "traian mosoiu", "prof dr aurel ardelean": "profesor doctor aurel ardelean",
    "vicentiu babes": "vincentiu babes", "cornel radu": "doctor cornel radu",
}


def basename(name):
    return re.sub(r"^(?:strada|bulevardul|calea|splaiul|splai|piata)\s+", "", fold(name))


def source_name(record):
    text = record["name"]
    # Specific multi-street circuits, median parking, parks and complex areas
    # need authoritative extent; deliberately do not map to a plausible point.
    if record["zone"] == 0 or text.startswith(("Aradul Nou", "Parcarea", "Parcările", "Parcul", "Stadion", "acces ")):
        return None
    text = re.split(r"\s*[–(]|\s+-\s*|(?i:)-\s*tronson", text, maxsplit=1)[0].strip()
    text = re.sub(r"^(?:[Ss]tr\.|B-dul|P-ţa|P-ta|Piata|Piaţa|Piața|Splaiul|Splai)\s*", "", text)
    key = basename(text)
    return NAME_EXPANSIONS.get(key, key)


def match_records(records, osm):
    index = defaultdict(list)
    for element in osm["elements"]:
        if element.get("type") != "way" or "geometry" not in element:
            continue
        tags = element.get("tags", {})
        # Include old_name only when the OSM object explicitly provides it.
        names = set()
        for tag in ("name", "old_name", "alt_name", "official_name"):
            names.update(n.strip() for n in tags.get(tag, "").split(";") if n.strip())
        for name in names:
            index[basename(name)].append(element)
    mapping, unmatched = {}, []
    for record in records:
        key = source_name(record)
        matches = {e["id"]: e for e in index.get(key, [])}.values() if key else []
        matches = sorted(matches, key=lambda e: e["id"])
        if not matches:
            unmatched.append({"id": record["id"], "name": record["name"], "reason": "specific_extent_not_established" if key is None else "no_reviewed_name_match", "lookup_name": key})
            continue
        lines = [[[p["lon"], p["lat"]] for p in e["geometry"]] for e in matches if len(e["geometry"]) >= 2]
        if not lines:
            continue
        longest = max(lines, key=len)
        # Use an actual coordinate vertex rather than an invented coordinate.
        lon, lat = longest[len(longest) // 2]
        geometry = {"type": "LineString", "coordinates": lines[0]} if len(lines) == 1 else {"type": "MultiLineString", "coordinates": lines}
        mapping[record["id"]] = {
            "latitude": lat, "longitude": lon, "geometry": geometry,
            "location_precision": "street_reference_not_parking_extent",
            "geocoding_method": "osm_named_street_reference",
            "geocode_source_url": f"https://www.openstreetmap.org/way/{matches[0]['id']}",
            "geocoding_reference_ids": [f"way/{e['id']}" for e in matches],
            "geocoding_matched_name": "; ".join(sorted(set(e.get("tags", {}).get("name", "") for e in matches))),
            "geometry_role": "reference_street",
            "geocoding_note": "OSM geometry of the named street, potentially including parts outside the source-listed segment. Latitude/longitude is one OSM vertex used only as a representative reference. This is not a parking bay, entrance, parking boundary, or verified navigation target.",
        }
    return mapping, unmatched


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--root", type=Path, default=ROOT)
    args = ap.parse_args()
    raw, data = args.root / "raw", args.root / "data"
    path = raw / "osm-reference.json"
    if not args.offline:
        response = urlopen(Request(ENDPOINT + "?" + urlencode({"data": QUERY}), headers={"User-Agent": "AradParkingData/1.0 (civic open data extraction)"}), timeout=90)
        content = response.read()
        payload = json.loads(content)
        if payload.get("remark") or not isinstance(payload.get("elements"), list) or not payload["elements"]:
            raise ValueError("Overpass returned an error or empty response; retaining last-known-good cache")
        path.write_bytes(content)
        (raw / "osm-query.txt").write_text(QUERY + "\n")
    osm = json.loads(path.read_text())
    records = json.loads((data / "records.json").read_text())
    mapping, unmatched = match_records(records, osm)
    (data / "geocoding.json").write_text(json.dumps(mapping, ensure_ascii=False, indent=2) + "\n")
    (data / "geocoding-unmatched.json").write_text(json.dumps(unmatched, ensure_ascii=False, indent=2) + "\n")
    manifest = {"endpoint": ENDPOINT, "query": QUERY, "osm_timestamp": osm.get("osm3s", {}).get("timestamp_osm_base"), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "license": "ODbL-1.0", "attribution": "© OpenStreetMap contributors", "copyright_url": "https://www.openstreetmap.org/copyright", "matched_record_count": len(mapping), "unmatched_record_count": len(unmatched)}
    (raw / "osm-sources.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"matched": len(mapping), "unmatched": len(unmatched), "geometry_role": "reference_street"}))


if __name__ == "__main__":
    main()
