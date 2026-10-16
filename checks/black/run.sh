#!/bin/sh
# Port of psf/black@26.10.1's action (action/main.py).
#
# The action's inputs are read from the same INPUT_* variables the action
# runner sets: `with: {options: --check, src: ./src}` becomes
# `vars: {INPUT_OPTIONS: --check, INPUT_SRC: ./src}`. output-file is
# INPUT_OUTPUT_FILE. The summary input is dropped: a check has no job summary,
# and the log carries Black's output.
#
# Wolfi packages no Black, so it is installed from PyPI into a venv, as the
# action does. With no version or use_pyproject it installs the release this
# port tracks, as the action installs its own tag.
set -eu

DEFAULT_VERSION=26.10.1
PYTHON=${PYTHON:-python3}
venv=${BLACK_VENV:-/var/tmp/black-env}

# Resolve the pip requirement exactly as main.py's determine_version_specifier.
spec=$(
  INPUT_VERSION=${INPUT_VERSION:-} INPUT_USE_PYPROJECT=${INPUT_USE_PYPROJECT:-false} \
    "$PYTHON" - <<'EOF'
import os, re, sys

VERSION = os.getenv("INPUT_VERSION", "")
USE_PYPROJECT = os.getenv("INPUT_USE_PYPROJECT") == "true"
BLACK_VERSION_RE = re.compile(
    r"^black((?:\s*(?:~=|==|!=|<=|>=|<|>|===)\s*[A-Za-z0-9*+._-]+)"
    r"(?:\s*,\s*(?:~=|==|!=|<=|>=|<|>|===)\s*[A-Za-z0-9*+._-]+)*)\s*$",
    re.IGNORECASE,
)
EXTRAS_RE = re.compile(r"\[.*\]")


def fail(msg):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def from_array(array):
    if not isinstance(array, list):
        return None
    for item in array:
        if not isinstance(item, str):
            continue
        item = EXTRAS_RE.sub("", item.split(";")[0]).strip()
        if item == "black":
            fail("Version specifier missing for 'black' dependency in pyproject.toml.")
        if m := BLACK_VERSION_RE.match(item):
            return m.group(1).strip()
    return None


def from_pyproject():
    import tomllib

    try:
        with open("pyproject.toml", "rb") as fp:
            pyproject = tomllib.load(fp)
    except FileNotFoundError:
        fail("'use_pyproject' requires a pyproject.toml file.")
    version = pyproject.get("tool", {}).get("black", {}).get("required-version")
    if version is not None:
        version = str(version).strip()
        if not version:
            return ""
        if version[0] in "0123456789":
            version = f"=={version}" if "." in version else f"~={version}.0"
        if m := BLACK_VERSION_RE.match(f"black{version}"):
            return m.group(1).strip()
        fail("Invalid 'required-version' in pyproject.toml: expected a version"
             " or version specifier, for example '26.1.0' or '>=26,<27'.")
    project = pyproject.get("project", {})
    for array in [
        *pyproject.get("dependency-groups", {}).values(),
        project.get("dependencies"),
        *project.get("optional-dependencies", {}).values(),
    ]:
        if (version := from_array(array)) is not None:
            return version
    fail("'black' dependency missing from pyproject.toml.")


if USE_PYPROJECT and VERSION:
    fail("'version' and 'use_pyproject' inputs are mutually exclusive.")
if USE_PYPROJECT:
    print(from_pyproject())
elif VERSION and VERSION[0] in "0123456789":
    print(f"=={VERSION}")
else:
    print(VERSION)
EOF
)
[ -n "$spec" ] || spec="==$DEFAULT_VERSION"

if [ "${INPUT_JUPYTER:-false}" = true ]; then
  req="black[colorama,jupyter]$spec"
else
  req="black[colorama]$spec"
fi

echo "Installing $req..."
"$PYTHON" -m venv "$venv"
if ! out=$("$venv/bin/python" -m pip install --disable-pip-version-check --quiet "$req" 2>&1); then
  printf '%s\n' "$out"
  echo "error: Failed to install Black." >&2
  exit 1
fi

# black_args is deprecated upstream but still honored there, so here too.
# Options and src are shell-split, as main.py's shlex.split does.
if [ -n "${INPUT_BLACK_ARGS:-}" ]; then
  cmd="$INPUT_BLACK_ARGS"
else
  cmd="${INPUT_OPTIONS---check --diff} ${INPUT_SRC-.}"
fi

eval "set -- $cmd"

# The whole library is mounted into the workspace; never format it. Black
# takes one --extend-exclude, so one given in the options wins over this.
lib=$(cd "$(dirname "$0")/../.." && pwd)
case $lib in
  "$PWD"/*)
    case " $cmd " in
      *" --extend-exclude"*) ;;
      *)
        # Black matches the regex against the path from its project root,
        # which is not always the workspace, so it is not anchored.
        rel=$(printf '%s' "${lib#"$PWD"/}" | sed 's/[].[^$*+?(){}|\\]/\\&/g')
        set -- --extend-exclude "/$rel/" "$@"
        ;;
    esac
    ;;
esac

set +e
out=$("$venv/bin/black" "$@" 2>&1)
res=$?
set -e

if [ -n "${INPUT_OUTPUT_FILE:-}" ]; then
  printf '%s\n' "$out" >"$INPUT_OUTPUT_FILE"
  echo "Black output written to $INPUT_OUTPUT_FILE"
fi
printf '%s\n' "$out"
exit "$res"
