"""Offline tests for the data pipelines (no network). Run: python -m unittest discover tests"""
import io
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
        self.assertEqual(sp.period_month(2025, 11), "May 2026")

    def test_to_milli_is_exact(self):
        self.assertEqual(sp.to_milli("-606.770"), -606770)
        self.assertEqual(sp.to_milli("20809.400"), 20809400)
        self.assertEqual(sp.to_milli(""), 0)
        self.assertEqual(sp.to_milli("12345678901.235"), 12345678901235)

    def test_pointer_parsing(self):
        text = ('FileName,UploadDate,FileSize,Download\n'
                '"Spending_FY25P11.csv","2026-08-01","1 GB","https://x.blob.core.windows.net/t/Spending_FY25P11.csv"\n'
                '"Spending_FY16P01.csv","2026-08-01","148 MB","https://x.blob.core.windows.net/t/Spending_FY16P01.csv"\n'
                '"README.txt","2026-08-01","1 KB","https://x/readme"\n')
        files = sp.read_pointer(text)
        self.assertEqual([(f["beginYear"], f["period"]) for f in files], [(2016, 1), (2025, 11)])

    def test_plan(self):
        pointer = [{"beginYear": y, "period": p} for y in (2021, 2022, 2023, 2024) for p in range(1, 13)]
        pointer = [f for f in pointer if not (f["beginYear"] == 2024 and f["period"] > 5)]
        existing = {2021: {"periods": list(range(1, 13))}, 2022: {"periods": list(range(1, 12))}}
        self.assertEqual(sp.plan_years(pointer, existing, full=False), [2022, 2023, 2024])
        self.assertEqual(sp.plan_years(pointer, existing, full=True), [2021, 2022, 2023, 2024])

    def test_aggregate_and_summary(self):
        with open(FIXTURE, newline="") as fh:
            agg = sp.aggregate_rows(fh)
        self.assertEqual(agg["rows"], 8)
        self.assertEqual(agg["bad"], 0)
        doc = sp.build_summary(2025, [({"file": "Spending_FY25P11.csv", "period": 11}, agg)])
        self.assertEqual(doc["fiscalYear"], "2025-26")
        self.assertFalse(doc["complete"])
        self.assertEqual(doc["through"], "May 2026")
        # quoted agency name with commas survives intact
        self.assertIn("Legislative, Judicial, & Exec", doc["agencies"])
        # net total = sum of all amounts, rounded to dollars
        expected = 24.350 - 606.770 + 319.080 + 9435.000 + 20809.400 + 33243.430 - 36.050 + 1000.004
        self.assertEqual(doc["totals"]["net"], round(expected))
        # every flow points at valid dictionary entries
        for f, d, t, v in doc["flows"]:
            self.assertLess(f, len(doc["funds"]))
            self.assertLess(d, len(doc["departments"]))
            self.assertLess(t, len(doc["spendTypes"]))
            self.assertIsInstance(v, int)
        # the two Scholarshare rows (same fund/dept/type) are combined
        sch = [i for i, dep in enumerate(doc["departments"]) if dep[1] == "Scholarshare Investment Board"]
        self.assertEqual(len(sch), 1)
        self.assertEqual(sum(v for _f, d, _t, v in doc["flows"] if d == sch[0]), round(319.080 + 1000.004))

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
