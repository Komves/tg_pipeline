"""Offline checks for the approved 16-scene PARUS / Brian soundtrack."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vesya_tools.parus_video_narration import (
    BRIAN_ID,
    SCENES,
    narration_archive,
    scene_manifest,
    speech_text,
)
from vesya_tools.parus_voiceover import MAX_SAMPLE_CHARACTERS, VOICE_PRESETS


class FullNarrationTests(unittest.TestCase):
    def test_16_scenes_and_reference_timings(self):
        manifest = scene_manifest()
        self.assertEqual(len(SCENES), 16)
        self.assertEqual(len(manifest), 16)
        self.assertEqual([m["number"] for m in manifest], list(range(1, 17)))
        self.assertEqual(manifest[0]["source_start_seconds"], 0)
        self.assertAlmostEqual(manifest[-1]["source_end_seconds"], 225.626953)
        for current, following in zip(manifest[:-1], manifest[1:]):
            self.assertEqual(current["source_end_seconds"], following["source_start_seconds"])
        self.assertTrue(all(len(m["speech_text"]) <= MAX_SAMPLE_CHARACTERS for m in manifest))

    def test_stress_applies_to_each_parus_mention(self):
        self.assertEqual(speech_text("ПАРУС — ПАРУСе"), "Па́рус — Па́русе")
        self.assertTrue(scene_manifest()[0]["speech_text"].startswith("Па́рус —"))
        self.assertNotIn("ПАРУС", " ".join(m["speech_text"] for m in scene_manifest()))
        self.assertEqual(BRIAN_ID, next(v for k, _label, v in VOICE_PRESETS if k == "brian"))

    def test_archive_and_cache_do_not_resynthesize(self):
        produced = []
        sample = b"ID3" + b"a" * 2048
        with tempfile.TemporaryDirectory() as workdir:
            with patch.dict(os.environ, {"PARUS_VOICE_CACHE_DIR": workdir}):
                with patch(
                    "vesya_tools.parus_video_narration.render_narration_sample",
                    return_value=sample,
                ) as tts:
                    zip_bytes, made, reused = narration_archive(
                        after_scene=lambda n, total: produced.append((n, total))
                    )
                    self.assertEqual(tts.call_count, 16)
                    self.assertEqual((made, reused), (16, 0))
                    self.assertEqual(produced[-1], (16, 16))
                    with ZipFile(BytesIO(zip_bytes), "r") as archive:
                        filenames = archive.namelist()
                        self.assertIn("scene_manifest.json", filenames)
                        self.assertIn("narration_script.txt", filenames)
                        self.assertEqual(
                            len([p for p in filenames if p.startswith("audio/")]), 16
                        )
                        self.assertEqual(archive.read("audio/01.mp3"), sample)
                with patch(
                    "vesya_tools.parus_video_narration.render_narration_sample",
                    side_effect=AssertionError("TTS should not be called again"),
                ):
                    _archive, made, reused = narration_archive()
                    self.assertEqual((made, reused), (0, 16))

    def test_partial_failure_preserves_successful_mp3(self):
        sample = b"ID3" + b"b" * 2048
        with tempfile.TemporaryDirectory() as workdir:
            with patch.dict(os.environ, {"PARUS_VOICE_CACHE_DIR": workdir}):
                with patch(
                    "vesya_tools.parus_video_narration.render_narration_sample",
                    side_effect=[sample, RuntimeError("Provider stopped")],
                ):
                    with self.assertRaisesRegex(RuntimeError, "Provider stopped"):
                        narration_archive()
                self.assertEqual((Path(workdir) / "01.mp3").read_bytes(), sample)
                self.assertFalse((Path(workdir) / "02.mp3").exists())


if __name__ == "__main__":
    unittest.main()
