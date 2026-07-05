import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from stylus.paths import state_path
from stylus.state import AnalysisRecord, BaselineChange, MAX_ANALYSES_PER_BRANCH, StylusState


class StateTests(unittest.TestCase):
    def test_init_creates_versioned_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"STYLUS_HOME": str(Path(tmp) / "stylus")}):
                state = StylusState.load_or_create()

                self.assertEqual(state.data["version"], 1)
                self.assertTrue(state_path().exists())

    def test_records_branch_specific_baseline_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"STYLUS_HOME": str(Path(tmp) / "stylus")}):
                state = StylusState.load_or_create()

                change = BaselineChange(
                    id="abc123",
                    base_revision="HEAD",
                    diff_path="/abs/path/to/abc123.diff",
                    summary="rename helper",
                    created_at="2026-06-29T00:00:00Z",
                    task="make helper clearer",
                )
                state.set_last_baseline("repo", "main", change)
                state.save()

                reloaded = StylusState.load_or_create()
                found = reloaded.get_last_baseline("repo", "main")

                self.assertIsNotNone(found)
                self.assertEqual(found.id, "abc123")
                self.assertIsNone(reloaded.get_last_baseline("repo", "feature"))

    def test_appends_analysis_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"STYLUS_HOME": str(Path(tmp) / "stylus")}):
                state = StylusState.load_or_create()
                record = AnalysisRecord(
                    commit="deadbeef",
                    baseline_change_id="abc123",
                    result="updated",
                    created_at="2026-06-29T00:00:00Z",
                )

                state.append_analysis("repo", "main", record)
                state.save()

                raw = json.loads(state_path().read_text())
                analyses = raw["repositories"]["repo"]["branches"]["main"]["analyses"]
                self.assertEqual(analyses[0]["commit"], "deadbeef")

    def test_analysis_records_are_capped_per_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"STYLUS_HOME": str(Path(tmp) / "stylus")}):
                state = StylusState.load_or_create()

                for index in range(MAX_ANALYSES_PER_BRANCH + 5):
                    state.append_analysis("repo", "main", AnalysisRecord(
                        commit=f"commit-{index}",
                        baseline_change_id="abc123",
                        result="updated",
                        created_at="2026-06-29T00:00:00Z",
                    ))

                analyses = state.data["repositories"]["repo"]["branches"]["main"]["analyses"]
                self.assertEqual(len(analyses), MAX_ANALYSES_PER_BRANCH)
                self.assertEqual(analyses[0]["commit"], "commit-5")


if __name__ == "__main__":
    unittest.main()
