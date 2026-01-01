import json
import tempfile
import unittest
from pathlib import Path

from tools.install_fixtures import install_all_cases, list_cases, load_case


class P04FixturesTest(unittest.TestCase):
    def test_install_all_cases_creates_expected_files(self) -> None:
        cases = list_cases()
        self.assertGreaterEqual(len(cases), 3)

        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "baseline_inputs"
            installed = install_all_cases(dest_dir=dest)
            self.assertEqual(len(installed), len(cases))

            for cid in cases:
                case = load_case(cid)
                self.assertTrue(case.case_id)
                self.assertTrue(case.top_task)
                self.assertTrue(case.expected_outcome)

                case_dir = dest / cid
                self.assertTrue(case_dir.exists() and case_dir.is_dir(), f"missing installed dir: {case_dir}")

                # At least one file installed per case.
                files = [p for p in case_dir.rglob("*") if p.is_file()]
                self.assertGreaterEqual(len(files), 1, f"no files installed for {cid}")

                # Ensure naming includes stable requirement hints for baseline matcher.
                basenames = [p.name.lower() for p in files]
                self.assertTrue(
                    any("product_spec" in n or "requirements" in n or "constraints" in n for n in basenames),
                    f"fixture files should include requirement hints in filename for {cid}",
                )

    def test_case_json_has_required_fields(self) -> None:
        for cid in list_cases():
            d = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "cases" / cid / "case.json"
            data = json.loads(d.read_text(encoding="utf-8"))
            for k in ("case_id", "top_task", "expected_outcome"):
                self.assertIn(k, data)
                self.assertTrue(isinstance(data[k], str) and data[k].strip())


if __name__ == "__main__":
    unittest.main()

