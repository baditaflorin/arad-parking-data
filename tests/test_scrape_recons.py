import hashlib
import json
import sys
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from scrape_recons import TreeParser, amount, parse_inventory, parse_tariffs
from geocode_osm import match_records, source_name


class SourceParsingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "raw/recons-paid-parking.html").read_text()
        cls.parser = TreeParser(); cls.parser.feed(cls.source)
        cls.records, cls.claimed, cls.sums, cls.total = parse_inventory(cls.parser.root, "snapshot")
        cls.tariffs = parse_tariffs(cls.parser.root, "snapshot")

    def test_zone_inventory_and_capacities(self):
        self.assertEqual(Counter(r["zone"] for r in self.records), {0: 3, 1: 36, 2: 39, 3: 1})
        self.assertEqual(self.sums, {0: 690, 1: 2371, 2: 3508, 3: 187})
        self.assertEqual(self.total, 6877)
        self.assertEqual(sum(self.sums.values()), 6756)

    def test_nested_capacity_description_does_not_double_count(self):
        record = next(r for r in self.records if r["zone"] == 1 and r["source_item_number"] == 29)
        self.assertEqual(record["capacity"], 72)
        self.assertIn("33 locuri", record["name"])
        self.assertIn("21 locuri", record["name"])

    def test_wrapped_paragraph_entry_is_reassembled(self):
        record = next(r for r in self.records if r["zone"] == 1 and r["source_item_number"] == 25)
        self.assertEqual(record["capacity"], 198)
        self.assertIn("26-54", record["name"])
        self.assertIn("35-59", record["name"])

    def test_duplicate_street_entries_keep_distinct_ids(self):
        rows = [r for r in self.records if r["name"].lower() == "str. corneliu coposu"]
        self.assertEqual(len(rows), 2)
        self.assertEqual({r["capacity"] for r in rows}, {60, 77})
        self.assertEqual(len({r["id"] for r in self.records}), len(self.records))

    def test_currency_from_cell_not_incorrect_table_header(self):
        row = next(t for t in self.tariffs if t["category"] == "sms" and t["zone"] == 0 and t["duration_label"] == "60")
        self.assertEqual(row["currency"], "EUR")
        self.assertEqual(row["amount"], 0.60)
        self.assertEqual(row["sms_code"], "105")
        self.assertFalse(row["vat_included"])
        self.assertEqual(row["parse_warning"], "currency_from_cell_overrides_header")

    def test_ambiguous_prices_are_not_invented(self):
        self.assertIsNone(amount("21,900,00"))
        self.assertEqual(amount("2.160,00"), 2160.0)
        malformed = [t for t in self.tariffs if t["amount"] is None]
        self.assertEqual(len(malformed), 3)
        self.assertTrue(all(t["category"] == "reservation_annual" for t in malformed))
        self.assertEqual(len(self.tariffs), 82)

    def test_source_change_fails_instead_of_silently_emitting_empty_data(self):
        parser = TreeParser(); parser.feed("<html><body>New site</body></html>")
        with self.assertRaisesRegex(ValueError, "source structure"):
            parse_inventory(parser.root, "snapshot")

    def test_raw_snapshot_checksum(self):
        manifest = json.loads((ROOT / "raw/sources.json").read_text())
        self.assertEqual(hashlib.sha256((ROOT / "raw/recons-paid-parking.html").read_bytes()).hexdigest(), manifest["sha256"])

    def test_geocoding_does_not_map_complex_inventory_to_office(self):
        osm = json.loads((ROOT / "raw/osm-reference.json").read_text())
        mapping, unmatched = match_records(self.records, osm)
        self.assertEqual(len(mapping), 67)
        self.assertEqual(len(unmatched), 12)
        for record in self.records:
            if record["zone"] == 0:
                self.assertNotIn(record["id"], mapping)
        for geo in mapping.values():
            self.assertEqual(geo["geometry_role"], "reference_street")
            self.assertEqual(geo["location_precision"], "street_reference_not_parking_extent")
            self.assertIn(geo["geometry"]["type"], {"LineString", "MultiLineString"})
            self.assertTrue(46.10 < geo["latitude"] < 46.30)
            self.assertTrue(21.20 < geo["longitude"] < 21.50)


if __name__ == "__main__":
    unittest.main()
