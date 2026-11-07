#!/usr/bin/env python3
"""Reproducibly extract the public Recons Arad parking inventory (stdlib only).

The source lists streets/areas, not individually surveyed parking bays. No office
map coordinates are copied to inventory records. Optional OSM reference matching
is kept separate from official parking extent and is never a navigation target.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen

SOURCE_URL = "https://www.reconsarad.ro/parcari-cu-plata"
ROOT = Path(__file__).resolve().parents[1]


class Node:
    def __init__(self, tag="root", attrs=None, parent=None):
        self.tag, self.attrs, self.parent = tag, dict(attrs or []), parent
        self.children = []

    def walk(self, tag=None):
        if tag is None or self.tag == tag:
            yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.walk(tag)

    def text(self):
        return re.sub(r"\s+", " ", " ".join(
            c.text() if isinstance(c, Node) else c for c in self.children
        )).strip()


class TreeParser(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.current = self.root

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs, self.current)
        self.current.children.append(node)
        if tag not in self.VOID:
            self.current = node

    def handle_endtag(self, tag):
        cursor = self.current
        while cursor is not self.root:
            if cursor.tag == tag:
                self.current = cursor.parent
                return
            cursor = cursor.parent

    def handle_data(self, data):
        self.current.children.append(data)


def fold(value):
    value = value.translate(str.maketrans({"ş": "ș", "ţ": "ț", "Ş": "Ș", "Ţ": "Ț"}))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()).split())


def parse_inventory(tree, retrieved_at):
    records, totals, overall_total = [], {}, None
    for body in tree.walk("div"):
        if "card-body" not in body.attrs.get("class", "").split():
            continue
        paragraphs = [p.text() for p in body.walk("p") if p.text()]
        if not paragraphs:
            continue
        zone_match = re.fullmatch(r"Zona\s+([0-3])\s+de tarifare\s*:", paragraphs[0], re.I)
        if not zone_match:
            continue
        zone = int(zone_match[1])
        fragment = body.parent.attrs.get("id", "")
        groups, current = [], []
        for text in paragraphs[1:]:
            if "TOTAL" in text.upper():
                if current:
                    groups.append(" ".join(current)); current = []
                total = re.search(r"TOTAL\s+ZONA\s+([0-3])\s*-?\s*(\d+)\s+LOCURI", text, re.I)
                if total:
                    totals[int(total[1])] = int(total[2])
                all_total = re.search(r"TOTAL LOCURI DE PARCARE DIN SISTEMUL DE PLATA\s*-?\s*(\d+)", text, re.I)
                if all_total:
                    overall_total = int(all_total[1])
                continue
            if re.match(r"^\d+\.", text):
                if current:
                    groups.append(" ".join(current))
                current = [text]
            elif current:
                current.append(text)
        if current:
            groups.append(" ".join(current))
        occurrences = Counter()
        for raw in groups:
            number = re.match(r"^(\d+)\.\s*", raw)
            capacities = list(re.finditer(r"-\s*(\d+)\s+locuri\b", raw, re.I))
            if not number or not capacities:
                raise ValueError(f"Unparseable parking entry in zone {zone}: {raw}")
            last = capacities[-1]
            name = raw[number.end():last.start()].strip(" -–")
            normalized_name = fold(name)
            occurrences[normalized_name] += 1
            stable_key = f"{zone}|{normalized_name}|{occurrences[normalized_name]}"
            records.append({
                "id": "recons-" + hashlib.sha256(stable_key.encode()).hexdigest()[:16],
                "record_type": "paid_parking_street_or_area", "name": name,
                "address": name + ", Arad, România", "locality": "Arad",
                "zone": zone, "capacity": int(last[1]),
                "latitude": None, "longitude": None, "geometry": None,
                "location_precision": "unknown", "geocoding_method": None,
                "geocode_source_url": None, "geometry_role": None,
                "geocoding_note": None, "geocoding_reference_ids": [], "geocoding_matched_name": None,
                "source_url": SOURCE_URL, "source_fragment": fragment,
                "source_item_number": int(number[1]), "source_text": raw,
                "source_updated_at": None, "retrieved_at": retrieved_at,
                "max_stay_minutes": 120 if zone == 0 and "maxim 2 ore" in tree.text() else None,
            })
    if not records or set(r["zone"] for r in records) != {0, 1, 2, 3}:
        raise ValueError("Expected a nonempty inventory containing all four zones; source structure may have changed")
    records.sort(key=lambda r: (r["zone"], r["source_item_number"]))
    calculated = {z: sum(r["capacity"] for r in records if r["zone"] == z) for z in range(4)}
    return records, totals, calculated, overall_total


def amount(value):
    value = re.sub(r"\s+", "", value)
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+,\d{2}", value):
        return float(value.replace(".", "").replace(",", "."))
    if re.fullmatch(r"\d+(?:[,.]\d{1,2})?", value):
        return float(value.replace(",", "."))
    return None


def parse_tariffs(tree, retrieved_at):
    tariffs = []
    categories = [
        "parking_meter_mediana", "sms", "reservation_annual",
        "resident_up_to_two_cars_one_street", "resident_more_than_two_cars_one_street",
        "nonresident_one_street", "all_streets", "family_doctor_all_streets",
        "public_institution", "public_interest_utility",
    ]
    tables = list(tree.walk("table"))
    if len(tables) != len(categories):
        raise ValueError(f"Expected 10 tariff tables; got {len(tables)}. Inspect changed source before publishing")
    for index, table in enumerate(tables):
        rows = [[cell.text() for cell in row.walk() if cell.tag in {"td", "th"}] for row in table.walk("tr")]
        headers = rows[0]
        for row_number, row in enumerate(rows[1:], 1):
            if index == 0:
                duration, cells, zones = row[1], row[2:], [0]
            else:
                duration, cells = row[0], row[1:]
                zones = []
                for header in headers[1:]:
                    zone = re.search(r"ZONA\s*([0-3ABC])", header, re.I)
                    zones.append(({"A": 1, "B": 2, "C": 3}.get(zone[1].upper(), int(zone[1]) if zone[1].isdigit() else None)) if zone else None)
            for column, (value, zone) in enumerate(zip(cells, zones), 1):
                if zone is None or value.strip() in {"", "-"}:
                    continue
                price_text = re.split(r"\s*(?:EUR|RON|lei|\+)", value, flags=re.I)[0].strip()
                parsed_amount = amount(price_text)
                currency = "EUR" if "EUR" in value else "RON"
                code = re.search(r"cod\s*(\d+)", value)
                duration_unit = "minute" if duration.isdigit() else ("month" if "Lun" in duration else "quarter" if "Trimestru" in duration else "year" if "An" in duration else "day")
                duration_value = int(duration) if duration.isdigit() else 1
                tariffs.append({
                    "id": f"tariff-{index}-{row_number}-{zone}",
                    "category": categories[index], "zone": zone,
                    "duration_label": duration, "duration_value": duration_value,
                    "duration_unit": duration_unit,
                    "amount": parsed_amount, "currency": currency,
                    "vat_included": False if "+ TVA" in value else None,
                    "sms_code": code[1] if code else None,
                    "source_value": value, "source_header": headers[column if index != 0 else 2],
                    "parse_warning": "ambiguous_price_format" if parsed_amount is None else ("currency_from_cell_overrides_header" if currency == "EUR" and "lei" in headers[column] else None),
                    "source_url": SOURCE_URL, "source_table_index": index,
                    "source_row_index": row_number, "retrieved_at": retrieved_at,
                })
    return tariffs


def write_exports(data_dir, name, rows):
    (data_dir / f"{name}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n")
    columns = list(rows[0]) if rows else []
    with (data_dir / f"{name}.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for k, v in row.items()})


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--offline", action="store_true", help="Use the checked-in source snapshot")
    ap.add_argument("--root", type=Path, default=ROOT)
    args = ap.parse_args()
    root = args.root
    raw, data = root / "raw", root / "data"
    raw.mkdir(parents=True, exist_ok=True); data.mkdir(parents=True, exist_ok=True)
    source_path = raw / "recons-paid-parking.html"
    manifest_path = raw / "sources.json"
    if not args.offline:
        response = urlopen(Request(SOURCE_URL, headers={"User-Agent": "AradParkingData/1.0 (+reproducible public-interest data extraction)"}), timeout=45)
        source_bytes = response.read()
        # Fetch into memory and parse before replacing a last-known-good snapshot.
        tree = TreeParser(); tree.feed(source_bytes.decode("utf-8"))
        retrieved_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        parse_inventory(tree.root, retrieved_at); parse_tariffs(tree.root, retrieved_at)
        source_path.write_bytes(source_bytes)
        manifest_path.write_text(json.dumps({"url": SOURCE_URL, "retrieved_at": retrieved_at, "sha256": hashlib.sha256(source_bytes).hexdigest(), "content_type": response.headers.get("Content-Type")}, indent=2) + "\n")
    if not source_path.exists() or not manifest_path.exists():
        ap.error("Offline extraction requires raw/recons-paid-parking.html and raw/sources.json; run without --offline once")
    manifest = json.loads(manifest_path.read_text())
    source_bytes = source_path.read_bytes()
    if hashlib.sha256(source_bytes).hexdigest() != manifest["sha256"]:
        raise ValueError("Raw snapshot SHA-256 does not match its source manifest")
    tree = TreeParser(); tree.feed(source_bytes.decode("utf-8"))
    records, claimed, calculated, claimed_total = parse_inventory(tree.root, manifest["retrieved_at"])
    tariffs = parse_tariffs(tree.root, manifest["retrieved_at"])
    # Geocoding enrichment is an explicit, separate reviewed reference layer.
    refs = root / "data" / "geocoding.json"
    if refs.exists():
        mapping = json.loads(refs.read_text())
        for record in records:
            if record["id"] in mapping:
                record.update({k: v for k, v in mapping[record["id"]].items() if k in {"latitude", "longitude", "geometry", "location_precision", "geocoding_method", "geocode_source_url", "geometry_role", "geocoding_note", "geocoding_reference_ids", "geocoding_matched_name"}})
    write_exports(data, "records", records)
    write_exports(data, "tariffs", tariffs)
    features = [{"type": "Feature", "id": r["id"], "geometry": r.get("geometry"), "properties": {k: v for k, v in r.items() if k != "geometry"}} for r in records]
    (data / "records.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False, indent=2) + "\n")
    zones = [{"zone": z, "color": ["green", "red", "yellow", "blue"][z], "capacity_claimed_by_source": claimed.get(z), "capacity_sum_of_entries": calculated[z], "difference_entries_minus_claimed": calculated[z] - claimed[z] if z in claimed else None, "source_url": SOURCE_URL} for z in range(4)]
    write_exports(data, "zones", zones)
    rules = {
        "source_url": SOURCE_URL, "retrieved_at": manifest["retrieved_at"],
        "timezone": "Europe/Bucharest",
        "source_schedule_text": [p.text() for p in tree.root.walk("p") if re.match(r"^[abc]\)\s*(?:Luni|Sâmbătă|Duminică)", p.text())],
        "interpretation": "Read the quoted parking schedule, not the cashier opening hours. Default paid hours Monday–Friday 08:00–17:00; Sunday 08:00–14:00 has an explicit Piața Obor / Pădurii / Căpitan Ignat exception. Public-holiday exceptions are not assumed.",
        "source_quoted_zone_0_limit": "Zona verde - Zona 0 – parcarea fiind permisă maxim 2 ore" if "maxim 2 ore" in tree.root.text() else None,
    }
    (data / "rules.json").write_text(json.dumps(rules, ensure_ascii=False, indent=2) + "\n")
    known = sum(r["geometry"] is not None for r in records)
    metadata = {
        "dataset": "arad-parking-data", "title": "Parcări cu plată Arad — inventarul Recons",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source_retrieved_at": manifest["retrieved_at"], "record_count": len(records), "geocoded_count": known,
        "status": "real_source_extracted_with_quality_warnings", "source_urls": [SOURCE_URL],
        "license": "Upstream Recons data: no explicit open-data reuse licence identified. Code is MIT. Any OpenStreetMap reference geometries are ODbL-1.0; see LICENSE-DATA.md.",
        "record_granularity": "A source-listed street, street segment, square or parking area; never an individual parking bay.",
        "claimed_capacity_total": claimed_total, "calculated_capacity_total": sum(calculated.values()),
        "tariff_count": len(tariffs), "tariffs_with_ambiguous_amount": sum(t["amount"] is None for t in tariffs),
        "spatial_coverage": {"known_geometry_count": known, "unknown_geometry_count": len(records) - known, "reference_geometry_only": True},
        "quality_notes": [
            "Zone and grand totals in the source may disagree with the sum of individual entries; both are retained in zones.json.",
            "Two separate source entries named Corneliu Coposu are retained as separate records; they must not be collapsed merely by street name.",
            "SMS cells explicitly say EUR + TVA although the table headers say lei; currency is derived from each cell.",
            "Reservation prices such as 21,900,00 are malformed/ambiguous; normalized amount is null and the original string is retained.",
            "A source-listed area may include multiple segments; geometry, when available, is an explicitly labelled OSM reference and not the parking boundary.",
            "The embedded source-page map locates the operator office and is not a parking inventory; it is intentionally excluded.",
            "Source page does not establish a source-wide effective date; retrieved_at is an acquisition timestamp, not an effective date.",
        ],
        "files": ["records.csv", "records.json", "records.geojson", "tariffs.csv", "tariffs.json", "zones.csv", "zones.json", "rules.json"],
    }
    (data / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: metadata[k] for k in ("record_count", "geocoded_count", "claimed_capacity_total", "calculated_capacity_total", "tariff_count")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
