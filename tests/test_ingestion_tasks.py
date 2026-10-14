"""Offline rules of the task layer. The full run is checked with `python -m ingestion.tasks run` + `parity <run_id>` (needs the DB)."""
import json
import tempfile
import unittest
from pathlib import Path

from collection import collect
from ingestion import tasks
from ingestion.contracts import ROOT


class TaskRules(unittest.TestCase):
    def setUp(self):
        self.cfg = json.loads((ROOT / "configs/collection/targets.json").read_text(encoding="utf-8"))

    def test_collector_default_work_folder_is_unchanged(self):
        self.assertEqual(collect.Collector().work, collect.ROOT / "data/catalog")
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(collect.Collector(work=tmp).work, Path(tmp))

    def test_plan_runs_a_step_after_the_step_it_reads(self):
        keys = tasks.plan(self.cfg)
        position = {k: i for i, k in enumerate(keys)}
        for (step, product), i in position.items():
            if step in tasks.READS:
                self.assertLess(position[(tasks.READS[step], product)], i, (step, product))

    def test_plan_skips_steps_a_product_cannot_have(self):
        keys = set(tasks.plan(self.cfg))
        self.assertNotIn(("health_canada", "BAT2206"), keys)   # no Canadian licence
        self.assertNotIn(("clinical_trials", "CNTO1275"), keys)  # reference product: no trials by default
        self.assertIn(("guidelines", ""), keys)
        self.assertEqual({p for s, p in tasks.plan(self.cfg, products=["SB17"])}, {"SB17"})

    def test_merge_never_writes_into_the_project_catalog(self):
        with self.assertRaises(SystemExit):
            tasks.write({}, ROOT / "data/catalog")


if __name__ == "__main__":
    unittest.main()
