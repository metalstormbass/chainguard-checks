#!/bin/bash
# Port of dagger/dagger-for-github@v8.4.1's assemble and exec steps.
#
# The action's inputs are read from the same INPUT_* variables the action
# runner sets: `with: {verb: call, args: build --src .}` becomes
# `vars: {INPUT_VERB: call, INPUT_ARGS: build --src .}`. dagger-flags is
# INPUT_DAGGER_FLAGS.
#
# Not ported: version and commit (the dagger CLI is the Wolfi package the
# check's environment pins, not a curl | sh install), cloud-token (a check
# has no secret to pass; set DAGGER_CLOUD_TOKEN if your plane provides one),
# summary-path, enable-github-summary and the stdout and traceURL outputs (the
# log carries both). The engine runs on the docker service.
set -euo pipefail

verb=${INPUT_VERB:-call}
flags=${INPUT_DAGGER_FLAGS---progress plain}
args=${INPUT_ARGS:-}
call=${INPUT_CALL:-}
check=${INPUT_CHECK:-}
shell=${INPUT_SHELL:-}

# Collapse backslash-continuations and newlines into spaces, as the action's
# assemble step does for every CLI-arg input.
collapse() { printf '%s' "$1" | sed -z 's/\\\n/ /g; s/\n/ /g'; }
flags=$(collapse "$flags")
args=$(collapse "$args")
call=$(collapse "$call")
check=$(collapse "$check")

# With nothing to run, the action only installs dagger.
if [[ -z $call && -z $shell && -z $args && -z $check && $verb == call ]]; then
  dagger version
  exit 0
fi

script=""
if [[ -n $call ]]; then
  verb=call
elif [[ -n $check ]]; then
  verb=check
  # The check input is a trigger; its glob patterns are not passed as args.
  check=""
elif [[ -n $shell ]]; then
  verb=""
  script=$(mktemp)
  printf '%s' "$shell" >"$script"
fi

pos=${args:-${call:-${script:-$check}}}

# Tokenize as the action does, with xargs' quoting rules.
flags_arr=()
if [[ -n $flags ]]; then
  while IFS= read -r -d '' t; do flags_arr+=("$t"); done < <(printf '%s' "$flags" | xargs printf '%s\0')
fi
pos_arr=()
if [[ -n $pos ]]; then
  while IFS= read -r -d '' t; do pos_arr+=("$t"); done < <(printf '%s' "$pos" | xargs printf '%s\0')
fi

cd "${INPUT_WORKDIR:-.}"
cmd=(dagger "${flags_arr[@]}" ${verb:+"$verb"} ${INPUT_MODULE:+-m "$INPUT_MODULE"} "${pos_arr[@]}")
echo "Running ${cmd[*]}"
exec "${cmd[@]}"
