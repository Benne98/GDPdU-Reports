"""Tests for async FDD script job runner."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app.routers import fdd_bot  # noqa: E402


class TestResolveOutputFolder(unittest.TestCase):
    def test_desktop_resolves_to_home(self):
        out = fdd_bot._resolve_output_folder("Desktop", "sess123")
        self.assertTrue(out.endswith("/Desktop") or out.endswith("\\Desktop"))
        self.assertIn(str(Path.home()), out)


class TestAsyncScriptJob(unittest.TestCase):
    @patch.object(fdd_bot.threading, "Thread")
    @patch.object(fdd_bot, "subprocess")
    @patch.object(fdd_bot, "_file_path_for_id")
    def test_start_async_writes_running_status(self, mock_file, mock_subprocess, mock_thread):
        session_id = "test_async_sess"
        fake_xlsx = ROOT / "uploads" / session_id / "abc_file.xlsx"
        mock_file.return_value = fake_xlsx
        mock_subprocess.Popen.return_value = MagicMock()
        mock_thread.return_value = MagicMock()

        result = fdd_bot._start_async_script_job(
            session_id,
            "abc",
            {"output_file_path": "Desktop", "sheet_name": "Data"},
            "gst",
        )

        self.assertTrue(result.get("success"))
        run_id = result["run_id"]
        status = fdd_bot._read_run_status(session_id, run_id)
        self.assertIsNotNone(status)
        assert status is not None
        self.assertEqual(status["status"], "running")
        self.assertEqual(status["script_key"], "gst")
        mock_subprocess.Popen.assert_called_once()

    def test_read_run_status_missing(self):
        self.assertIsNone(fdd_bot._read_run_status("no_session", "no_run"))

    def test_write_and_read_status(self):
        session_id = "status_roundtrip"
        run_id = "run_test_1"
        fdd_bot._write_run_status(
            session_id,
            run_id,
            {"status": "done", "output_file": "/tmp/out.xlsx", "output_filename": "out.xlsx"},
        )
        status = fdd_bot._read_run_status(session_id, run_id)
        self.assertEqual(status["status"], "done")
        self.assertEqual(status["output_filename"], "out.xlsx")
        # cleanup
        path = fdd_bot._run_status_path(session_id, run_id)
        if path.is_file():
            path.unlink()


if __name__ == "__main__":
    unittest.main()
