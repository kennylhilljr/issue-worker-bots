"""Validate the documented CI workflow and the contributing guidance with plain text parsing."""
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def workflow_block(text):
    blocks = re.findall(r"^```ya?ml\n(.*?)^```", text, re.MULTILINE | re.DOTALL)
    if len(blocks) != 1:
        raise AssertionError(f"docs/ci.md must contain exactly one YAML block, found {len(blocks)}")
    return blocks[0]


def parse(block):
    """Return (indent, stripped line) pairs for non-blank, non-comment lines."""
    lines = []
    for raw in block.splitlines():
        stripped = raw.strip()
        if stripped and not stripped.startswith("#"):
            lines.append((len(raw) - len(raw.lstrip(" ")), stripped))
    return lines


def children(lines, path):
    """Lines nested under the mapping key path, e.g. ("jobs", "tests")."""
    block = lines
    for key in path:
        top = min((level for level, _ in block), default=0)
        for index, (level, text) in enumerate(block):
            if level == top and (text == key + ":" or text.startswith(key + ": ")):
                break
        else:
            raise AssertionError(f"key {key!r} not found under {path}")
        nested = []
        for child_level, child_text in block[index + 1:]:
            if child_level <= level:
                break
            nested.append((child_level, child_text))
        block = nested
    return block


def direct(lines):
    """Only the shallowest entries of a nested block."""
    if not lines:
        return []
    top = min(level for level, _ in lines)
    return [text for level, text in lines if level == top]


class Workflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.block = workflow_block((ROOT / "docs" / "ci.md").read_text(encoding="utf-8"))
        cls.lines = parse(cls.block)

    def test_top_level_keys(self):
        self.assertEqual(direct(self.lines), ["name: tests", "on:", "permissions:", "jobs:"])

    def test_triggers(self):
        self.assertEqual(direct(children(self.lines, ("on",))), ["pull_request:", "push:"])
        self.assertEqual(direct(children(self.lines, ("on", "push"))), ["branches:"])
        self.assertEqual(direct(children(self.lines, ("on", "push", "branches"))), ["- main"])
        self.assertEqual(children(self.lines, ("on", "pull_request")), [])

    def test_only_contents_read(self):
        self.assertEqual([text for _, text in children(self.lines, ("permissions",))], ["contents: read"])
        self.assertEqual(sum(text.startswith("permissions:") for _, text in self.lines), 1)

    def test_single_job_on_macos(self):
        self.assertEqual(direct(children(self.lines, ("jobs",))), ["tests:"])
        self.assertIn("runs-on: macos-latest", direct(children(self.lines, ("jobs", "tests"))))

    def test_matrix_versions_quoted(self):
        versions = direct(children(self.lines, ("jobs", "tests", "strategy", "matrix", "python-version")))
        self.assertEqual(versions, ["- '3.9'", "- '3.13'"])

    def test_steps(self):
        steps = children(self.lines, ("jobs", "tests", "steps"))
        self.assertEqual(direct(steps), [
            "- uses: actions/checkout@v4",
            "- uses: actions/setup-python@v5",
            "- run: python3 scripts/check.py",
        ])
        self.assertIn("python-version: ${{ matrix.python-version }}", [text for _, text in steps])

    def test_single_run_step_and_no_installs(self):
        runs = [text for _, text in self.lines if re.match(r"^(- )?run:", text)]
        self.assertEqual(runs, ["- run: python3 scripts/check.py"])
        self.assertNotRegex(self.block, r"pip |pip3 |install")

    def test_no_secrets(self):
        self.assertNotIn("secrets.", self.block)

    def test_parser_rejects_missing_key(self):
        with self.assertRaises(AssertionError):
            children(parse("on:\n  push:\n"), ("on", "pull_request"))


class Guidance(unittest.TestCase):
    def test_readme_contributing_sentence(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        section = re.search(r"^## Contributing\s*\n(.*?)(?=^## |\Z)", readme, re.MULTILINE | re.DOTALL)
        self.assertIsNotNone(section, "README.md must have a Contributing section")
        self.assertIn("Run `python3 scripts/check.py` before opening a PR.", section.group(1))

    def test_design_links_ci_doc(self):
        design = (ROOT / "DESIGN.md").read_text(encoding="utf-8")
        self.assertRegex(design, r"\]\((?:\./)?docs/ci\.md\)")


if __name__ == "__main__":
    unittest.main()
