#!/bin/sh
# Port of codespell-project/actions-codespell@v2.4.3's entrypoint.sh.
#
# The action's inputs are read from the same INPUT_* variables the action
# runner sets, so `with: {skip: ./vendor}` becomes `vars: {INPUT_SKIP: ./vendor}`.
# The problem matcher is dropped: the check's log carries codespell's
# file:line: findings.
set -u

# The whole library is mounted into the workspace; never spell-check it.
lib=$(cd "$(dirname "$0")/../.." && pwd)
case $lib in
  "$PWD"/*) self="./${lib#"$PWD"/}" ;;
  *) self="" ;;
esac

set -- --count
[ -n "${INPUT_BUILTIN:-}" ] && set -- "$@" --builtin "$INPUT_BUILTIN"
[ -n "${INPUT_CHECK_FILENAMES:-}" ] && set -- "$@" --check-filenames
[ -n "${INPUT_CHECK_HIDDEN:-}" ] && set -- "$@" --check-hidden
[ -n "${INPUT_CONFIG:-}" ] && set -- "$@" --config "$INPUT_CONFIG"
[ -n "${INPUT_EXCLUDE_FILE:-}" ] && set -- "$@" --exclude-file "$INPUT_EXCLUDE_FILE"
[ -n "${INPUT_IGNORE_WORDS_FILE:-}" ] && set -- "$@" --ignore-words "$INPUT_IGNORE_WORDS_FILE"
[ -n "${INPUT_IGNORE_WORDS_LIST:-}" ] && set -- "$@" --ignore-words-list "$INPUT_IGNORE_WORDS_LIST"
[ -n "${INPUT_URI_IGNORE_WORDS_LIST:-}" ] && set -- "$@" --uri-ignore-words-list "$INPUT_URI_IGNORE_WORDS_LIST"

# The action defaults skip to ./.git; an explicit empty INPUT_SKIP clears it.
skip=${INPUT_SKIP-./.git}
if [ -n "$self" ]; then
  skip=${skip:+$skip,}$self
fi
[ -n "$skip" ] && set -- "$@" --skip "$skip"

# INPUT_PATH is word-split, as the action does, so it can name several paths.
# shellcheck disable=SC2086
set -- "$@" ${INPUT_PATH:-}

echo "Running codespell $*"
codespell "$@"
res=$?
if [ "$res" = 0 ]; then
  echo "Codespell found no problems"
else
  echo "Codespell found one or more problems"
fi

# only_warn: report, never fail.
if [ -n "${INPUT_ONLY_WARN:-}" ]; then
  exit 0
fi
exit "$res"
