import unittest
from pathlib import Path


class WrapperModulesAreThinTest(unittest.TestCase):
    def test_observability_is_thin_wrapper(self) -> None:
        text = Path("core/observability.py").read_text(encoding="utf-8")
        self.assertNotIn("conn.execute", text)
        self.assertNotIn("generate_plan_report", text)
        self.assertNotIn("def get_plan_snapshot", text)

    def test_manifest_is_thin_wrapper(self) -> None:
        text = Path("core/manifest.py").read_text(encoding="utf-8")
        self.assertNotIn("conn.execute", text)
        self.assertNotIn("SELECT ", text)
        self.assertNotIn("INSERT ", text)
        self.assertNotIn("UPDATE ", text)
        self.assertIn("deliverables_write_manifest_json", text)


if __name__ == "__main__":
    unittest.main()

