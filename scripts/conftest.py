"""Suite-wide pytest setup for scripts/ (and scripts/reverify/).

PROC-19 (2026-11-03): git exports GIT_DIR, GIT_PREFIX & co. into every hook it runs. When the
PROC-18 pre-push ran this suite, those leaked into each test's temp-repo `git init/config/commit/
push`, which then acted on the REAL repo: core.bare=true, core.hooksPath pointed at a pytest temp
dir, user.name=test, and test commits on the pushing worktree's HEAD (one test even tried a real
`git push origin`, stopped only by the recursive hook). Clearing them here protects the real repo
however pytest is launched; the pre-push hook also clears them before invoking anything.
"""
import os
import subprocess

_FALLBACK = (
    "GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_CONFIG GIT_CONFIG_PARAMETERS GIT_CONFIG_COUNT "
    "GIT_OBJECT_DIRECTORY GIT_DIR GIT_WORK_TREE GIT_IMPLICIT_WORK_TREE GIT_GRAFT_FILE "
    "GIT_INDEX_FILE GIT_NO_REPLACE_OBJECTS GIT_REPLACE_REF_BASE GIT_PREFIX GIT_SHALLOW_FILE "
    "GIT_COMMON_DIR"
).split()


def _local_env_vars():
    try:
        out = subprocess.run(["git", "rev-parse", "--local-env-vars"], capture_output=True, text=True)
        names = out.stdout.split()
    except OSError:
        names = []
    return set(names) | set(_FALLBACK)


for _name in _local_env_vars():
    os.environ.pop(_name, None)
# GIT_CONFIG_COUNT-style injected config (GIT_CONFIG_KEY_n / GIT_CONFIG_VALUE_n) goes too.
for _name in [n for n in os.environ if n.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"))]:
    os.environ.pop(_name, None)
