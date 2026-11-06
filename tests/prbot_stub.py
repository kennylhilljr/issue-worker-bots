"""Stand-in for ../pr-review-bots/prbot so the tests run in a checkout without the sibling repo.
die and git_env match prbot. Anything that would sign in or reach GitHub raises; tests fake those."""
import os, sys


def die(msg, code=2):
    print(f"prbot: {msg}", file=sys.stderr)
    sys.exit(code)


def git_env(token=None, identity=None):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    env.pop("GITHUB_TOKEN", None)
    if identity:
        env["GIT_AUTHOR_NAME"] = env["GIT_COMMITTER_NAME"] = identity[0]
        env["GIT_AUTHOR_EMAIL"] = env["GIT_COMMITTER_EMAIL"] = identity[1]
    if token:
        helper = '!f() { echo username=x-access-token; echo "password=$PRBOT_GIT_TOKEN"; }; f'
        pairs = [("credential.helper", ""), ("credential.https://github.com.helper", ""),
                 ("credential.https://github.com.helper", helper)]
        env.update(PRBOT_GIT_TOKEN=token, GIT_CONFIG_COUNT=str(len(pairs)))
        for i, (k, v) in enumerate(pairs):
            env[f"GIT_CONFIG_KEY_{i}"], env[f"GIT_CONFIG_VALUE_{i}"] = k, v
    return env


def _unfaked(name):
    def f(*a, **k):
        raise AssertionError(f"test called prbot.{name} without faking it")
    f.__name__ = name
    return f


for _name in ("load_config", "bot_config", "installed_repos", "installation_token", "bot_identity",
              "gh_read", "_real_gh", "openclaw_attention"):
    globals()[_name] = _unfaked(_name)
