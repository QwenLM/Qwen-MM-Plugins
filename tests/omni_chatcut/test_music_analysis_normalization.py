"""Standalone offline regression tests for caption normalization."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "src/capabilities/omni-chatcut/skill/music-to-mv/workflows/storyboard/scripts/normalize_music_analysis.py"
)


class MusicAnalysisNormalizationTests(unittest.TestCase):
    def test_complete_caption_bodies_survive_cli_normalization(self):
        overview = "Soft vocals.\n\nSparse electronic backing."
        first = "Breathy lead continues.\nBass supports the vocal.\n\nPercussion gradually thickens."
        last = "Instrumental release.\n\nReverb tails fade."
        summary = "Energy rises gradually.\n\nThe ending releases tension."
        source = (
            f"## 1) Whole-Track Overview\n\n{overview}\n\n"
            "## 2) Structure Timeline\n\nAnalysis note: final end clamped.\n\n"
            f"### [00:00:00,000 --> 00:00:10,000] [verse]\n\n{first}\n\n"
            "### [00:10.000 --> 00:20.000] [chorus]\nCaption: Full drums enter.\n\n"
            f"### [00:00:20,000 --> 00:00:25,000] [outro]\n{last}\n\n"
            "## 3) Lyrics Timeline\n\nNO_LYRICS\n\n"
            f"## 4) Overall Summary\n\n### Emotional and Energy Arc\n\n{summary}\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "caption.md"
            path.write_text(source, encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(SCRIPT), str(path)], check=True, capture_output=True, text=True
            )
        data = json.loads(result.stdout)
        self.assertEqual(data["whole_track_overview"], overview)
        self.assertEqual([item["caption"] for item in data["structure"]], [first, "Full drums enter.", last])
        self.assertEqual([item["label"] for item in data["structure"]], ["verse", "chorus", "outro"])
        self.assertEqual(
            [(item["start_sec"], item["end_sec"]) for item in data["structure"]],
            [(0.0, 10.0), (10.0, 20.0), (20.0, 25.0)],
        )
        self.assertEqual(data["overall_summary"]["Emotional and Energy Arc"], summary)
        self.assertEqual(data["duration_sec"], 25.0)
        self.assertEqual(data["lyrics"], [])


if __name__ == "__main__":
    unittest.main()
