# chainguard-checks

Reusable [Chainguard Checks](https://edu.chainguard.dev/) ported from the five
most-starred actions in [chainguard-actions](https://github.com/orgs/chainguard-actions/repositories?q=sort%3Astars):

| Check | Ported from | Kind |
| --- | --- | --- |
| [`json-yaml-validate`](checks/json-yaml-validate) | GrantBirki/json-yaml-validate@v5.0.1 | check |
| [`dagger`](checks/dagger) | dagger/dagger-for-github@v8.4.1 | check (root, docker service) |
| [`replace-tokens`](checks/replace-tokens) | cschleiden/replace-tokens@v1.4 | step |
| [`black`](checks/black) | psf/black@26.10.1 | check |
| [`codespell`](checks/codespell) | codespell-project/actions-codespell@v2.4.3 | check |

## Using a check

Checks has no registry for shared blocks yet (`uses:` is reserved), so a
check is shared the way the schema supports today: a check in your repository
mounts this library at a pinned commit with `inputs:` and runs its script.

1. Copy `checks/<name>/check.yaml` to `.chainguard/ci/<name>.yaml` in your
   repository. For `dagger`, also copy `.chainguard/ci/services/docker/` to
   the same path.
2. Set the action's `with:` inputs as `vars:`, named `INPUT_<NAME>`
   uppercased, as the Actions runner names them. Each stub lists the common
   ones; each script's header lists what is not ported. A var cannot hold a
   newline, so give a multi-line input on one line (`schema_mappings` as
   flow-style YAML, `files` space-separated), or export it in a `script:`.
3. Keep `ref:` pinned to a full commit SHA of this repository; bump it to
   upgrade.

```yaml
# .chainguard/ci/codespell.yaml
version: 1

environment:
  packages: [py3.13-codespell-bin~2.4.3, busybox]

checks:
  codespell:
    trigger: [presubmit, postsubmit]
    inputs:
      - path: .chainguard-checks
        git:
          repo: https://github.com/metalstormbass/chainguard-checks
          ref: dd78433fe8dd96c4b874935c0dfcb60c60ae6191 # v1.0.0
    vars:
      INPUT_SKIP: ./.git,./vendor
      INPUT_IGNORE_WORDS_LIST: crate
    cmd: [sh, .chainguard-checks/checks/codespell/run.sh]
```

The executor fetches the whole library into the workspace at `path:`, so each
script excludes that directory from what it scans, formats or rewrites.

## What changes from the actions

- No PR comments, annotations, job summaries or step outputs: the check's
  log and pass/fail are the result.
- No secrets as inputs (`github_token`, Dagger's `cloud-token`).
- Tools come from Wolfi packages pinned in each stub's `environment:`, not
  from a download at run time; Black, which Wolfi does not package, is
  installed from PyPI as the action installs it.
- `replace-tokens` mutates the tree, so it is a step to put before the steps
  that read the rendered files, not a check on its own.

## Developing

`.chainguard/ci/` runs each script against `testdata/<name>/`, with passing
and failing cases, and runs the ports on this repository itself. `run-ci` in a
Chainguard develop session runs them locally; `chainctl checks validate`
lints the configuration and each `checks/*/check.yaml`.

## License

Apache-2.0. The ports keep the upstream projects' notices; see [NOTICE](NOTICE).
