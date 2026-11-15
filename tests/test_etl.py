"""Unit tests for the ETL helpers (no database needed).  Run:  python -m unittest discover -s tests -v"""
import datetime as dt
import os
import sys
import tempfile
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "etl"))

# pyodbc is only needed to talk to SQL Server; stub it so the pure helpers can be tested anywhere
try:
    import pyodbc  # noqa: F401
except ImportError:
    sys.modules["pyodbc"] = types.ModuleType("pyodbc")

import bma_db        # noqa: E402
import export_html   # noqa: E402
import load_plan     # noqa: E402
import load_sales    # noqa: E402


class CellHelpers(unittest.TestCase):
    """Cleaning of spreadsheet cells in load_plan."""

    def test_text(self):
        self.assertEqual(load_plan.s("  Shop   A \n"), "Shop A")
        self.assertIsNone(load_plan.s("   "))
        self.assertIsNone(load_plan.s(None))
        self.assertIsNone(load_plan.s(float("nan")))
        self.assertEqual(load_plan.s(12345.0), "12345")          # Excel numbers stored as float

    def test_numbers(self):
        self.assertEqual(load_plan.num("1,5"), None)             # not a number -> None, not a crash
        self.assertEqual(load_plan.num("12.345", 1), 12.3)
        self.assertEqual(load_plan.i("41.6"), 42)
        self.assertIsNone(load_plan.i(""))

    def test_partner_code_range(self):
        self.assertEqual(load_plan.code("1234"), 1234)
        self.assertIsNone(load_plan.code(0))
        self.assertIsNone(load_plan.code(3_000_000_000))         # does not fit SQL INT

    def test_day_is_day_first(self):
        self.assertEqual(load_plan.day("05/07/2026"), dt.date(2026, 7, 5))
        self.assertIsNone(load_plan.day("not a date"))

    def test_share_accepts_fraction_or_percent(self):
        self.assertEqual(load_plan.share(0.35), 0.35)
        self.assertEqual(load_plan.share(35), 0.35)
        self.assertEqual(load_plan.share("50"), 0.5)
        self.assertIsNone(load_plan.share(None))

    def test_need_reports_missing_columns(self):
        import pandas as pd
        df = pd.DataFrame(columns=["A", "B"])
        load_plan.need(df, "Tab", ["A"])
        with self.assertRaisesRegex(ValueError, "missing columns \\['C'\\]"):
            load_plan.need(df, "Tab", ["A", "C"])


class SharedHelpers(unittest.TestCase):
    def test_norm_name(self):
        self.assertEqual(bma_db.norm_name("  7-11  Buddy   ถนน "), "7-11 buddy ถนน")
        self.assertEqual(bma_db.norm_name(None), "")

    def test_dictionary_encoding(self):
        d = export_html.Dict(["fixed"])
        self.assertEqual([d("fixed"), d("a"), d("b"), d("a"), d(None)], [0, 1, 2, 1, 3])
        self.assertEqual(d.items, ["fixed", "a", "b", ""])


class ReadSalesFile(unittest.TestCase):
    """load_sales.read_file: province filter, bad codes, aggregation to day x shop x SIM x doc type."""

    def _write(self, rows):
        cols = load_sales.NEEDED
        lines = ["|".join(cols)]
        for r in rows:
            lines.append("|".join(str(r.get(c, "0")) for c in cols))
        fd, path = tempfile.mkstemp(suffix=".txt")
        with os.fdopen(fd, "w", encoding="cp874") as fh:
            fh.write("\n".join(lines) + "\n")
        self.addCleanup(os.remove, path)
        return path

    def test_filters_and_aggregates(self):
        base = dict(TM_KEY_DAY=20260701, COMPANY="True", SIM_TYPE="S1", GROUP_SIM="Prepaid", PARTNER_CODE=111,
                    PARTNER_NAME="Shop A", TDS_PROVINCE=load_sales.PROVINCE, TDS_CHANNEL="7-11", DISTRICT_EN="D",
                    SUB_DISTRICT_EN="SD", GA=1, AP1D=1, AP1D_AMT=10, REV_AMT=5, DOC_TYP_GP="Thai ID")
        rows = [
            base,
            dict(base, GA=2),                                  # same key -> summed
            dict(base, SIM_TYPE="S2", GA=4),                   # other SIM type -> own row
            dict(base, TDS_PROVINCE="Other province", GA=100), # outside the area -> dropped
            dict(base, PARTNER_CODE="abc", GA=50),             # bad code -> dropped and counted
        ]
        fact, store, sim, stats = load_sales.read_file(self._write(rows), load_sales.PROVINCE)
        self.assertEqual(stats, {"rows_read": 5, "rows_kept": 3, "bad_code": 1})
        self.assertEqual(len(fact), 2)
        ga = dict(zip(fact["SIM_TYPE"], fact["GA"]))
        self.assertEqual(ga, {"S1": 3, "S2": 4})
        self.assertEqual(list(store["PARTNER_CODE"]), [111])
        self.assertEqual(sorted(sim["SIM_TYPE"]), ["S1", "S2"])

    def test_missing_column_is_an_error(self):
        fd, path = tempfile.mkstemp(suffix=".txt")
        with os.fdopen(fd, "w", encoding="cp874") as fh:
            fh.write("TM_KEY_DAY|GA\n20260701|1\n")
        self.addCleanup(os.remove, path)
        with self.assertRaisesRegex(ValueError, "columns missing"):
            load_sales.read_file(path, load_sales.PROVINCE)


class DemoData(unittest.TestCase):
    """make_demo_data writes a valid version-3 file and never overwrites a real export."""

    def test_generates_valid_file_and_guards_real_data(self):
        import json
        import subprocess
        script = os.path.join(HERE, "..", "etl", "make_demo_data.py")
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "dashboard-data.js")
            subprocess.run([sys.executable, script, "--out", out], check=True, capture_output=True)
            with open(out, encoding="utf-8") as fh:
                head, body = fh.readline(), fh.read()
            self.assertIn("DEMO DATA", head)
            data = json.loads(body[body.index("{"):body.rindex("}") + 1])
            self.assertEqual(data["version"], 3)
            self.assertEqual(len(data["f"]["day"]), len(data["f"]["ga"]))
            self.assertTrue({"Mass 350", "Mass 199"} <= set(data["dict"]["stg"]))   # needed by the game plan SIM rules
            self.assertEqual({p[1] for p in data["plans"]}, {"7-11", "RTR"})

            real = os.path.join(tmp, "real.js")
            with open(real, "w", encoding="utf-8") as fh:
                fh.write("/* Generated from SQL Server */\nwindow.DASH_DATA = {};\n")
            r = subprocess.run([sys.executable, script, "--out", real], capture_output=True, text=True)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("refusing", r.stderr + r.stdout)


if __name__ == "__main__":
    unittest.main()
