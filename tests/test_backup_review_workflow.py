"""Check that the backup-review workflow never runs pull request code with its secrets.

The repository is public and the workflow runs on a schedule with
CLAUDE_CODE_OAUTH_TOKEN, so a fork PR must not be reviewed and the checkout
Claude Code runs in must not be the PR head, where CLAUDE.md or
.claude/settings.json hooks could be planted. Plain text parsing, as in
test_ci_docs.
"""
import re
import unittest
from pathlib import Path

from test_ci_docs import children, direct, parse


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "backup-review.yml"
PINNED = re.compile(r"^[\w.-]+/[\w.-]+(/[\w./-]+)?@[0-9a-f]{40}(\s+#.*)?$")


class BackupReview(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        cls.lines = parse(cls.text)

    def find_script(self):
        return "\n".join(text for _, text in children(self.lines, ("jobs", "find")))

    def review_steps(self):
        return children(self.lines, ("jobs", "review", "steps"))

    def test_find_skips_fork_prs(self):
        script = self.find_script()
        self.assertIn("isCrossRepository", script)
        self.assertIn("(.isCrossRepository | not)", script)

    def test_review_rechecks_fork_before_running_claude(self):
        steps = "\n".join(text for _, text in self.review_steps())
        self.assertIn("--json headRefOid,isCrossRepository", steps)
        self.assertRegex(steps, r'if \[ "\$fork" != "false" \]; then')

    def test_checkout_is_default_branch_not_pr_head(self):
        steps = self.review_steps()
        texts = [text for _, text in steps]
        checkout = [i for i, text in enumerate(texts) if text.startswith("- uses: actions/checkout@")]
        self.assertEqual(len(checkout), 1)
        level = steps[checkout[0]][0]
        options = []
        for child_level, text in steps[checkout[0] + 1:]:
            if child_level <= level:
                break
            options.append(text)
        self.assertIn("ref: ${{ github.event.repository.default_branch }}", options)
        self.assertIn("persist-credentials: false", options)
        for text in self.text.splitlines():
            if "ref:" in text:
                self.assertNotIn("matrix.pr", text)
                self.assertNotIn("head", text.lower())

    def test_actions_pinned_to_commit_sha(self):
        uses = [text.split("uses:", 1)[1].strip() for _, text in self.lines
                if text.startswith(("uses:", "- uses:"))]
        self.assertGreaterEqual(len(uses), 2)
        for ref in uses:
            self.assertRegex(ref, PINNED)

    def test_secret_only_in_claude_step(self):
        self.assertEqual(self.text.count("secrets."), 1)
        self.assertIn("claude_code_oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}", self.text)

    def test_claude_tools_stay_read_only(self):
        self.assertIn("--allowedTools Read,Grep,Glob", self.text)
        self.assertIn("--disallowedTools Bash,Edit,Write,NotebookEdit,WebFetch,WebSearch", self.text)

    def test_no_pull_request_target_trigger(self):
        self.assertEqual(direct(children(self.lines, ("on",))), ["schedule:", "workflow_dispatch:"])


if __name__ == "__main__":
    unittest.main()
