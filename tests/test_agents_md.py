"""Check agent guidance and its Python references without running the pipeline."""
import importlib.util
import os
import re
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
NON_PYTHON_REFERENCES = {
    "AGENTS.md", "CLAUDE.md", "README.md", "DESIGN.md",
    "iwb", "prbot", "launchctl",
}
REQUIRED_REFERENCES = {
    "STATE", "CONFIG", "WORK_DIR", "QUEUE_DIR",
    "run_gh_as", "prbot.gh_read", "short", "TOKENISH",
    "DENY_PATHS", "ALWAYS_BLOCKED", "SECRET_ENV", "DRIVE_ENV",
    "denied_command", "sandbox_profile", "run_model", "write_atomic", "save_state", "code_plan",
}


def python_references(text):
    """Extract identifier-shaped code spans, excluding known non-Python names."""
    spans = re.findall(r"(?<!`)`([^`\n]+)`(?!`)", text)
    return {
        span for span in spans
        if re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", span)
        and span not in NON_PYTHON_REFERENCES
    }


def unprefixed(reference):
    return reference[4:] if reference.startswith("iwb.") else reference


def validate_references(module, text):
    """Resolve every candidate; an unknown name must fail, never be filtered out."""
    references = python_references(text)
    for reference in sorted(references):
        value = module
        for name in unprefixed(reference).split("."):
            try:
                value = getattr(value, name)
            except AttributeError as error:
                raise AssertionError("Unknown Python reference: " + reference) from error
    return {unprefixed(reference) for reference in references}


class AgentDocumentation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Use a separate module so existing test_iwb patches cannot affect this check.
        with patch.dict(os.environ, {"PRBOT_PATH": str(ROOT / "tests" / "prbot_stub.py")}):
            loader = SourceFileLoader("iwb_agents_md", str(ROOT / "iwb"))
            spec = importlib.util.spec_from_loader(loader.name, loader)
            cls.iwb = importlib.util.module_from_spec(spec)
            loader.exec_module(cls.iwb)

    def read_agents(self):
        path = ROOT / "AGENTS.md"
        self.assertTrue(path.is_file(), "AGENTS.md must exist")
        return path.read_text(encoding="utf-8")

    def test_headings_and_test_commands(self):
        text = self.read_agents()
        self.assertEqual(re.findall(r"^## (.+)$", text, re.MULTILINE), [
            "What this is",
            "Running the tests",
            "Never run these from an agent session",
            "Review rules",
            "Commits and pushes",
        ])
        for command in (
            "python3 -m unittest discover -s tests",
            "python3 -m unittest tests.test_iwb.Code",
        ):
            with self.subTest(command=command):
                self.assertIn("`" + command + "`", text)

    def test_claude_pointer(self):
        self.assertEqual((ROOT / "CLAUDE.md").read_text(encoding="utf-8"), "Read AGENTS.md.\n")

    def test_readme_contributing_link(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        section = re.search(r"^## Contributing\s*\n(.*?)(?=^## |\Z)", readme, re.MULTILINE | re.DOTALL)
        self.assertIsNotNone(section, "README.md must have a Contributing section")
        self.assertRegex(section.group(1), r"\[[^\]\n]+\]\((?:\./)?AGENTS\.md\)")

    def test_python_references(self):
        references = validate_references(self.iwb, self.read_agents())
        self.assertFalse(REQUIRED_REFERENCES - references,
                         "Missing required Python references: " + str(sorted(REQUIRED_REFERENCES - references)))

    def test_reference_extraction(self):
        text = (
            "`STATE` `iwb.CONFIG` `prbot.gh_read` "
            "`python3 -m unittest discover -s tests` `./iwb tick` "
            "`tests/test_iwb.py` `launchd/` `~/.local/state` `install-intake.sh` "
            "`AGENTS.md` `CLAUDE.md` `README.md` `DESIGN.md` `iwb` `prbot` `launchctl`"
        )
        self.assertEqual(python_references(text), {"STATE", "iwb.CONFIG", "prbot.gh_read"})
        self.assertEqual(validate_references(self.iwb, text), {"STATE", "CONFIG", "prbot.gh_read"})
        self.assertEqual(validate_references(self.iwb, "`iwb.prbot.gh_read`"), {"prbot.gh_read"})

    def test_nonexistent_references_fail(self):
        for reference in ("nonexistent_agent_reference", "iwb.nonexistent_agent_reference",
                          "prbot.nonexistent_agent_reference"):
            with self.subTest(reference=reference):
                text = "Valid `iwb.STATE`, but stale `" + reference + "`."
                with self.assertRaisesRegex(AssertionError, "Unknown Python reference: " + re.escape(reference)):
                    validate_references(self.iwb, text)


if __name__ == "__main__":
    unittest.main()
