"""Offline tests for the data pipelines (no network). Run: python -m unittest discover tests"""
import io
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import local_pipeline as lp  # noqa: E402
import state_pipeline as sp  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "spending_sample.csv"


class StatePipelineTests(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(sp.fy_label(2025), "2025-26")
        self.assertEqual(sp.fy_label(2099), "2099-00")
        self.assertEqual(sp.period_month(2025, 1), "July 2025")
        self.assertEqual(sp.period_month(2025, 6), "December 2025")
        self.assertEqual(sp.period_month(2025, 7), "January 2026")
        self.assertEqual(sp.period_month(2025, 12), "June 2026")

    def test_to_milli_is_exact(self):
        self.assertEqual(sp.to_milli("-606.770"), -606770)
        self.assertEqual(sp.to_milli("20809.400"), 20809400)
        self.assertEqual(sp.to_milli(""), 0)
        self.assertEqual(sp.to_milli("12345678901.235"), 12345678901235)

    def test_pointer_parsing(self):
        text = ('FileName,UploadDate,FileSize,Download\n'
                '"Spending_FY25P11.csv","2026-08-01","1 GB","https://x/Spending_FY25P11.csv"\n'
                '"README.txt","2026-08-01","1 KB","https://x/readme"\n')
        self.assertEqual([(f["fileYear"], f["period"]) for f in sp.read_pointer(text)], [(2025, 11)])

    def test_discovery_finds_unlisted_files(self):
        exists = {sp.FILE_URL.format(yy=y % 100, pp=p) for y in (2016, 2025) for p in range(1, 13)}
        exists |= {sp.FILE_URL.format(yy=26, pp=1), sp.FILE_URL.format(yy=26, pp=2)}
        probe = lambda url: {"lastModified": "Sat, 07 Nov 2026 18:05:04 GMT", "size": 10} if url in exists else None  # noqa: E731
        pointer = [{"file": "Spending_FY25P11.csv", "fileYear": 2025, "period": 11}]
        found = sp.discover_files(pointer, probe=probe)
        names = [f["file"] for f in found]
        self.assertIn("Spending_FY25P12.csv", names)  # not listed, but exists
        self.assertIn("Spending_FY26P02.csv", names)
        self.assertNotIn("Spending_FY26P03.csv", names)
        self.assertEqual(len(found), 26)

    def test_plan_rereads_everything_when_anything_changes(self):
        files = [{"file": f"Spending_FY{y % 100}P{p:02d}.csv", "fileYear": y, "period": p,
                  "lastModified": "a", "size": 1} for y in (2023, 2024) for p in range(1, 13)]
        index = {"schema": sp.SCHEMA, "sourceFiles": {f["file"]: ["a", 1] for f in files}}
        self.assertEqual(sp.plan_years(files, index, full=False), [])
        self.assertEqual(sp.plan_years(files, index, full=True), [2023, 2024])
        files[3]["lastModified"] = "b"
        self.assertEqual(sp.plan_years(files, index, full=False), [2023, 2024])
        self.assertEqual(sp.plan_years(files[:3], {"schema": 1}, full=False), [2023])

    def test_rows_are_assigned_to_their_own_fiscal_year(self):
        with open(FIXTURE, newline="") as fh:
            agg = sp.aggregate_rows(fh, file_year=2025)
        self.assertEqual(agg["rows"], 10)
        self.assertEqual(agg["bad"], 0)
        self.assertEqual(agg["otherYear"], 1)
        self.assertEqual(sorted(agg["years"]), [2024, 2025])
        late = sp.build_summary(2024, agg["years"][2024], [])
        self.assertEqual(late["totals"]["netCents"], 5635000)
        self.assertEqual(late["periods"], [12])

    def test_summary_is_exact_in_cents(self):
        with open(FIXTURE, newline="") as fh:
            agg = sp.aggregate_rows(fh, file_year=2025)
        src = [{"file": "Spending_FY25P11.csv", "lastModified": "Sat, 07 Nov 2026 18:05:04 GMT"}]
        doc = sp.build_summary(2025, agg["years"][2025], src)
        self.assertEqual(doc["fiscalYear"], "2025-26")
        self.assertEqual(doc["unit"], "cents")
        self.assertFalse(doc["complete"])
        self.assertEqual(doc["through"], "May 2026")
        self.assertIn("Legislative, Judicial, & Exec", doc["agencies"])
        expected_milli = 24350 - 606770 + 319080 + 9435000 + 20809400 + 33243430 - 36050 + 1000004 + 10005
        self.assertEqual(doc["totals"]["netCents"], round(expected_milli / 10))
        # per-flow cents can differ from the exact total by a fraction of a cent each
        self.assertLessEqual(abs(sum(f[3] for f in doc["flows"]) - doc["totals"]["netCents"]), len(doc["flows"]))
        self.assertIn(sp.FUND_MISSING, doc["funds"])
        self.assertIn(sp.TYPE_MISSING, doc["spendTypes"])
        # departments are keyed by business unit (one entry each)
        bus = [d[0] for d in doc["departments"]]
        self.assertEqual(len(bus), len(set(bus)))
        sch = bus.index("0954")
        self.assertEqual(sum(v for _f, d, _t, v in doc["flows"] if d == sch), round((319080 + 1000004) / 10))

    def test_part_round_trip_and_merge(self):
        with open(FIXTURE, newline="") as fh:
            agg = sp.aggregate_rows(fh, file_year=2025)
        doc = sp.part_to_json(2025, [], agg["years"], {"rows": 10})
        back = sp.part_from_json(json.loads(json.dumps(doc)))
        merged = {}
        sp.merge_into(merged, back)
        sp.merge_into(merged, back)
        self.assertEqual(merged[2024]["rows"], 2)
        self.assertEqual(sum(merged[2025]["flows"].values()), 2 * sum(agg["years"][2025]["flows"].values()))

    def test_missing_column_is_an_error(self):
        with self.assertRaises(ValueError):
            sp.aggregate_rows(io.StringIO("business_unit,monetary_amount\n1,2\n"))


class LocalPipelineTests(unittest.TestCase):
    def test_city_buckets(self):
        b = lambda cat, s1, line: lp.bucket_for("cities", cat, s1, line)  # noqa: E731
        gg = "General Government and Public Safety"
        self.assertEqual(b(gg, "Public Safety", "Police"), "police")
        self.assertEqual(b(gg, "Public Safety", "Emergency Medical Services"), "fire")
        self.assertEqual(b(gg, "Public Safety", "Street Lighting"), "streets")
        self.assertEqual(b(gg, "Public Safety", "Animal Regulation"), "safety_other")
        self.assertEqual(b(gg, "General Government", "Legislative"), "admin")
        self.assertEqual(b("Health and Culture and Leisure", "Health", "Sewers"), "sanitation")
        self.assertEqual(b("Health and Culture and Leisure", "Health", "Social Services"), "health")
        self.assertEqual(b("Health and Culture and Leisure", "Culture and Leisure", "Libraries"), "libraries")
        self.assertEqual(b("Debt Service and Capital Outlay", "Capital Outlay", "Transportation"), "capital")
        self.assertEqual(b("Debt Service and Capital Outlay", "Debt Service", "Interest"), "debt")
        self.assertIsNone(b("Water Enterprise Fund", "Operating Expenses", "Pumping"))

    def test_county_buckets(self):
        b = lambda cat, s1: lp.bucket_for("counties", cat, s1, "")  # noqa: E731
        self.assertEqual(b("Public Protection", "Police Protection"), "sheriff")
        self.assertEqual(b("Public Protection", "Protection – Other"), "safety_other")
        self.assertEqual(b("Public Assistance", "Welfare"), "assistance")
        self.assertEqual(b("Public Ways and Facilities, Health, and Sanitation", "Public Ways and Facilities"), "roads")
        self.assertEqual(b("Education and Recreation and Cultural Services", "Education"), "recreation")
        self.assertIsNone(b("Hospital Enterprise Fund Fund", "Operating Expenses"))

    def test_shape_year(self):
        rows = [
            {"entity_name": "Pasadena", "county": "Los Angeles", "estimated_population": "140631",
             "category": "General Government and Public Safety", "subcategory_1": "Public Safety",
             "subcategory_2": "Police_Current Expenditures", "v": "112027030"},
            {"entity_name": "Pasadena", "county": "Los Angeles", "estimated_population": "140631",
             "category": "Health and Culture and Leisure", "subcategory_1": "Culture and Leisure",
             "subcategory_2": "Libraries_Current Expenditures", "v": "16205454"},
            {"entity_name": "Pasadena", "county": "Los Angeles", "estimated_population": "140631",
             "category": "Debt Service and Capital Outlay", "subcategory_1": "Capital Outlay",
             "subcategory_2": "Transportation", "v": "1000"},
            {"entity_name": "Pasadena", "county": "Los Angeles", "estimated_population": "140631",
             "category": "Water Enterprise Fund", "subcategory_1": "Operating Expenses",
             "subcategory_2": "Pumping", "v": "5"},
        ]
        doc = lp.shape_year("cities", 2025, rows)
        self.assertEqual(doc["fiscalYear"], "2024-25")
        name, county, pop, values, capital, debt, lines = doc["entities"][0]
        self.assertEqual((name, county, pop), ("Pasadena", "Los Angeles", 140631))
        self.assertEqual(values[doc["groups"].index("police")], 112027030)
        self.assertEqual(values[doc["groups"].index("libraries")], 16205454)
        self.assertEqual(sum(values), 112027030 + 16205454)
        self.assertEqual((capital, debt), (1000, 0))
        self.assertEqual(len(lines), 2)
        self.assertEqual(doc["lines"][lines[0][0]], ["police", "Police"])


if __name__ == "__main__":
    unittest.main()
