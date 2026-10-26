"""Exercise the Codex-to-Claude boundary without running product checks."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location(
    "codex_checks", str(Path(__file__).resolve().parents[1] / "hooks/claude-checks.py"))
checks = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checks)


class AdapterTests(unittest.TestCase):
    def test_patch_tracks_every_changed_code_file(self):
        paths = checks.changed({"tool_name": "apply_patch", "cwd": str(checks.REPO),
                                "tool_input": {"command": "\n".join([
                                    "*** Update File: observatory/app/api.py",
                                    "*** Update File: observatory/web/index.html",
                                    "*** Update File: AGENTS.md"])}})
        self.assertEqual(set(paths), {checks.REPO / "observatory/app/api.py",
                                      checks.REPO / "observatory/web/index.html"})

    def test_shell_paths_resolve_from_tool_workdir(self):
        paths = checks.changed({"tool_name": "Bash", "cwd": str(checks.REPO),
                                "tool_input": {"workdir": str(checks.REPO / "observatory"),
                                               "command": "sed -i '' 's/a/b/' app/api.py"}})
        self.assertEqual(paths, [checks.REPO / "observatory/app/api.py"])

    def test_reads_do_not_add_files_to_gate(self):
        self.assertEqual(checks.changed({"tool_name": "Bash", "tool_input": {
            "command": "cat observatory/app/api.py"}}), [])

    def test_syntax_failure_is_blocking(self):
        with mock.patch.object(checks.gate, "_syntax", return_value=["bad syntax"]):
            with contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(checks.syntax(["broken.py"]), 2)
            self.assertIn("bad syntax", err.getvalue())

    def test_real_invalid_json_is_blocked_by_shared_syntax_check(self):
        with tempfile.TemporaryDirectory(dir=str(checks.REPO / ".codex")) as tmp:
            path = Path(tmp) / "broken.json"
            path.write_text('{"broken":')
            with contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(checks.syntax([path]), 2)
            self.assertIn("broken.json", err.getvalue())
            path.write_text('{"fixed": true}')
            self.assertEqual(checks.syntax([path]), 0)

    def test_browser_hook_denies_broad_kill_and_allows_scoped_kill(self):
        script = checks.REPO / ".claude/hooks/block-browser-kill.sh"
        for command, blocked in [("killall 'Google Chrome'", True),
                                 ('pkill -f -- "--headless=new"', False)]:
            result = subprocess.run(["sh", str(script)], input=json.dumps({
                "tool_input": {"command": command}}), capture_output=True, text=True)
            self.assertEqual(result.returncode, 0)
            if blocked:
                self.assertEqual(json.loads(result.stdout)["hookSpecificOutput"]
                                 ["permissionDecision"], "deny")
            else:
                self.assertEqual(result.stdout, "")

    def test_done_passes_real_reply_and_paths_to_shared_gate(self):
        def shared_gate():
            data = json.load(checks.sys.stdin)
            turn = checks.gate.load_turn(data["transcript_path"])
            files = checks.gate.touched_files(turn, data["cwd"])
            self.assertEqual(files, [checks.REPO / "observatory/app/api.py"])
            self.assertEqual(data["last_assistant_message"], "Tried as a user: searched CPI.")
            print(json.dumps({"decision": "block", "reason": "related tests failed"}))
            return 0

        with mock.patch.object(checks.gate, "main", side_effect=shared_gate):
            with contextlib.redirect_stdout(io.StringIO()) as out:
                result = checks.done([checks.REPO / "observatory/app/api.py"],
                                     "Tried as a user: searched CPI.", "test")
            self.assertEqual(result, 2)
            self.assertEqual(json.loads(out.getvalue())["decision"], "block")

    def test_hook_state_isolated_by_turn_and_block_uses_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            def event(mode, turn):
                data = {"session_id": "test", "turn_id": turn,
                        "tool_name": "apply_patch", "cwd": str(checks.REPO),
                        "tool_input": {"command": "*** Update File: observatory/app/api.py"}}
                with mock.patch.object(checks.sys, "argv", ["checks", mode]):
                    with mock.patch.object(checks.sys, "stdin", io.StringIO(json.dumps(data))):
                        return checks.main()

            with mock.patch.object(checks.tempfile, "gettempdir", return_value=tmp):
                with mock.patch.object(checks, "syntax", return_value=0):
                    self.assertEqual(event("post", "turn-one"), 0)
                with mock.patch.object(checks, "done", return_value=2) as gate:
                    self.assertEqual(event("stop", "turn-one"), 0)
                    self.assertEqual(gate.call_args[0][0], [str(checks.REPO / "observatory/app/api.py")])
                    self.assertEqual(event("stop", "turn-two"), 0)
                    self.assertEqual(gate.call_args[0][0], [])


if __name__ == "__main__":
    unittest.main()
