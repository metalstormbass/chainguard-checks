"""Port of cschleiden/replace-tokens@v1.4 (src/main.ts, src/replace.ts).

The action's inputs are read from the same INPUT_* variables the action runner
sets: `with: {files: '["**/*.js"]', tokenPrefix: "{"}` becomes
`vars: {INPUT_FILES: '["**/*.js"]', INPUT_TOKENPREFIX: "{"}`. Token values come
from the environment, as the action's `env:` does, so they go in `vars:` too.

As upstream, a token with no variable is replaced with the empty string, and
the files that changed are printed. Run it as a step before the steps that
read the rendered files.
"""

import glob
import json
import os
import re
import sys

# The whole library is mounted into the workspace (checks/<name>/run.py).
LIBRARY = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))


def get_files():
    files = os.environ.get("INPUT_FILES", "")
    if not files.strip():
        sys.exit("error: Input required and not supplied: files")
    if files.strip().startswith("["):
        return json.loads(files)
    return [files]


def expand(patterns):
    # replace-in-file globs with node-glob: `**` recurses and dotfiles are
    # skipped, as Python's glob does by default.
    seen = []
    for pattern in patterns:
        for path in sorted(glob.glob(pattern, recursive=True)):
            path = os.path.realpath(path)
            # Never rewrite the library mounted into the workspace.
            if path.startswith(LIBRARY + os.sep) and LIBRARY.startswith(
                os.path.realpath(os.getcwd()) + os.sep
            ):
                continue
            if os.path.isfile(path) and path not in seen:
                seen.append(path)
    return seen


def main():
    prefix = os.environ.get("INPUT_TOKENPREFIX") or "#{"
    suffix = os.environ.get("INPUT_TOKENSUFFIX") or "}#"
    token = re.compile(f"{re.escape(prefix)}(.+?){re.escape(suffix)}")

    changed = []
    for path in expand(get_files()):
        with open(path, encoding="utf-8") as f:
            before = f.read()
        after = token.sub(lambda m: os.environ.get(m.group(1)) or "", before)
        if after != before:
            with open(path, "w", encoding="utf-8") as f:
                f.write(after)
            changed.append(os.path.relpath(path))
    print(f"Replaced tokens in files: {','.join(changed)}")


if __name__ == "__main__":
    main()
