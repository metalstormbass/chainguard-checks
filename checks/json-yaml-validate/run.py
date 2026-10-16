"""Port of GrantBirki/json-yaml-validate@v5.0.1.

The action's inputs are read from the same INPUT_* variables the action runner
sets: `with: {json_schema: schemas/a.json, mode: warn}` becomes
`vars: {INPUT_JSON_SCHEMA: schemas/a.json, INPUT_MODE: warn}`, with the
action's defaults. JSON Schema validation uses python-jsonschema in place of
AJV, so violation messages are worded differently; YAML schemas use the
action's own rule format, ported from src/functions/yaml-schema-validator.ts.

Not ported: github_token, comment, comment_on_success and update_comment (a
check has no token that can comment; the log carries the violations), and
ajv_strict_mode (AJV-only).
"""

import glob
import json
import os
import re
import sys

import jsonschema
import pathspec
import yaml

# The whole library is mounted into the workspace (checks/<name>/run.py).
LIBRARY = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
WORKSPACE = os.path.realpath(os.getcwd())

DEFAULTS = {
    "mode": "fail",
    "base_dir": ".",
    "files": "",
    "schema_mappings": "",
    "use_inline_schema": "false",
    "use_dot_match": "true",
    "json_schema": "",
    "json_schema_version": "draft-07",
    "json_extension": ".json",
    "json_exclude_regex": "",
    "use_ajv_formats": "true",
    "yaml_schema": "",
    "yaml_extension": ".yaml",
    "yaml_extension_short": ".yml",
    "yaml_exclude_regex": "",
    "yaml_as_json": "false",
    "exclude_file": "",
    "exclude_file_required": "true",
    "use_gitignore": "true",
    "git_ignore_path": ".gitignore",
    "allow_multiple_documents": "true",
    "ajv_custom_regexp_formats": "",
}

VALIDATORS = {
    "draft-04": jsonschema.Draft4Validator,
    "draft-07": jsonschema.Draft7Validator,
    "draft-2019-09": jsonschema.Draft201909Validator,
    "draft-2020-12": jsonschema.Draft202012Validator,
}
BUILT_IN_SCHEMAS = {
    "http://json-schema.org/draft-04/schema": "draft-04",
    "http://json-schema.org/draft-07/schema": "draft-07",
    "https://json-schema.org/draft/2019-09/schema": "draft-2019-09",
    "https://json-schema.org/draft/2020-12/schema": "draft-2020-12",
}
YAML_SCHEMA_COMMENT = re.compile(r"^\s*#\s*yaml-language-server:\s*\$schema=(\S+)\s*$")


def inp(name):
    return os.environ.get(f"INPUT_{name.upper()}", DEFAULTS[name])


def flag(name):
    value = inp(name).strip().lower()
    if value not in ("true", "false"):
        sys.exit(f"error: INPUT_{name.upper()} must be true or false, got {value!r}")
    return value == "true"


def multiline(name):
    return [line.strip() for line in inp(name).splitlines() if line.strip()]


class Loader(yaml.SafeLoader):
    """The JS yaml parser's semantics: YAML 1.2 keeps timestamps as strings,
    and a duplicate mapping key is an error."""


Loader.yaml_implicit_resolvers = {
    k: [(tag, r) for tag, r in v if tag != "tag:yaml.org,2002:timestamp"]
    for k, v in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def _mapping(loader, node, deep=False):
    keys = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in keys:
            raise yaml.constructor.ConstructorError(
                None, None, f"Map keys must be unique: {key!r}", key_node.start_mark
            )
        keys.add(key)
    return loader.construct_mapping(node, deep=deep)


Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def _reject_constant(name):
    raise ValueError(f"invalid JSON constant {name}")


def load_json(source):
    return json.loads(source, parse_constant=_reject_constant)


def load_structured(path):
    with open(path, encoding="utf-8") as f:
        source = f.read()
    return (
        load_json(source)
        if path.lower().endswith(".json")
        else yaml.load(source, Loader)
    )


class Result:
    def __init__(self):
        self.success = True
        self.passed = self.failed = self.skipped = 0
        self.violations = []

    def fail(self, file, errors):
        self.success = False
        self.failed += 1
        self.violations.append({"file": file, "errors": errors})


class Exclude:
    def __init__(self):
        lines = []
        path = inp("exclude_file")
        if path:
            try:
                with open(path, encoding="utf-8") as f:
                    lines += f.read().splitlines()
            except OSError as e:
                if flag("exclude_file_required"):
                    sys.exit(f"error reading exclude_file: {path}: {e}")
                print("exclude_file was not found, but it is not required - OK")
        if flag("use_gitignore"):
            try:
                with open(inp("git_ignore_path"), encoding="utf-8") as f:
                    lines += f.read().splitlines()
            except OSError as e:
                print(f"warning: error reading .gitignore file: {e}")
        self.spec = pathspec.GitIgnoreSpec.from_lines(lines)

    def excluded(self, rel):
        return self.spec.match_file(rel)


def resolve(path):
    """(full, rel, display) for a file inside the workspace, or an error."""
    full = os.path.realpath(path)
    if not (full == WORKSPACE or full.startswith(WORKSPACE + os.sep)):
        return None, f"path resolves outside the workspace: {path}"
    if not os.path.isfile(full):
        return None, f"path is not a regular file: {path}"
    rel = os.path.relpath(full, WORKSPACE)
    return (full, rel, os.path.normpath(path)), None


def in_library(full):
    # Only when the library is mounted inside the workspace being validated.
    if not LIBRARY.startswith(WORKSPACE + os.sep):
        return False
    return full == LIBRARY or full.startswith(LIBRARY + os.sep)


def expand(patterns):
    files = []
    for pattern in patterns:
        files += sorted(glob.glob(pattern, recursive=True, include_hidden=True))
    return files


def discover_explicit(patterns):
    """file-discovery.ts discoverExplicitFiles: a single line that matches
    nothing is retried as a whitespace-separated list."""
    files = expand(patterns)
    flat = patterns[0].split() if len(patterns) == 1 else []
    return files if files or len(flat) <= 1 else expand(flat)


def discover_by_extension(base, extensions, dot_match):
    found = []
    for root, dirs, names in os.walk(base):
        if not dot_match:
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            names = [n for n in names if not n.startswith(".")]
        dirs.sort()
        for name in sorted(names):
            path = os.path.normpath(os.path.join(root, name))
            if any(path.endswith(ext) for ext in extensions) and os.path.isfile(path):
                found.append(path)
    return found


def base_directory():
    base = inp("base_dir").rstrip("/") or "."
    full = os.path.realpath(base)
    if not (
        full == WORKSPACE or full.startswith(WORKSPACE + os.sep)
    ) or not os.path.isdir(full):
        return None, f"base_dir must be a directory inside the workspace: {base}"
    return base, None


def schema_mappings(yaml_as_json):
    raw = inp("schema_mappings")
    if not raw.strip():
        return []
    mappings = yaml.load(raw, Loader)
    if not isinstance(mappings, list):
        sys.exit("error: schema_mappings must be a YAML list")
    out = []
    for i, m in enumerate(mappings):
        if (
            not isinstance(m, dict)
            or m.get("type") not in ("json", "yaml")
            or not m.get("schema")
        ):
            sys.exit(
                f"error: schema_mappings[{i}] needs type (json or yaml), schema and files"
            )
        if m["type"] == "yaml" and yaml_as_json:
            sys.exit(
                "error: YAML schema mappings cannot be used when yaml_as_json is true"
            )
        patterns = m.get("files")
        patterns = [patterns] if isinstance(patterns, str) else patterns
        if not patterns or not all(isinstance(p, str) for p in patterns):
            sys.exit(
                f"error: schema_mappings[{i}].files must be a pattern or a list of patterns"
            )
        files = expand(patterns)
        if not files:
            sys.exit(f"error: schema_mappings[{i}].files matched no files")
        out.append(
            {
                "type": m["type"],
                "schema": m["schema"],
                "files": files,
                "version": m.get("json_schema_version", inp("json_schema_version")),
            }
        )
    return out


def format_checker():
    checker = (
        jsonschema.FormatChecker()
        if flag("use_ajv_formats")
        else jsonschema.FormatChecker(())
    )
    for line in multiline("ajv_custom_regexp_formats"):
        if not re.match(r"^[\w-]+=.+$", line):
            sys.exit(
                f'error: Invalid ajv_custom_regexp_formats format: "{line}" is not in expected format "key=regex"'
            )
        name, pattern = line.split("=", 1)
        regex = re.compile(pattern)
        checker.checks(name)(
            lambda v, r=regex: not isinstance(v, str) or r.search(v) is not None
        )
    return checker


def compile_schema(schema, version):
    if version not in VALIDATORS:
        print(
            f"warning: json_schema_version '{version}' is not supported. Defaulting to 'draft-07'"
        )
        version = "draft-07"
    cls = VALIDATORS[version]
    cls.check_schema(schema)
    return cls(schema, format_checker=format_checker())


def schema_from_file(path, version):
    schema = load_json(open(path, encoding="utf-8").read()) if path else True
    return compile_schema(schema, version)


def built_in(schema_id):
    version = BUILT_IN_SCHEMAS[schema_id.rstrip("#")]
    cls = VALIDATORS[version]
    return cls(cls.META_SCHEMA, format_checker=format_checker())


def inline_reference(full, source, data, is_yaml):
    """('none'|'built-in'|'local'|'error', value) as inline-schema.ts."""
    ref = None
    if is_yaml:
        for line in source.splitlines():
            if not line.strip():
                continue
            m = YAML_SCHEMA_COMMENT.match(line)
            if m:
                ref = m.group(1)
                break
            if not line.lstrip().startswith("#"):
                break
    elif isinstance(data, dict) and isinstance(data.get("$schema"), str):
        ref = data["$schema"]
    if ref is None:
        return "none", None
    if ref.rstrip("#") in BUILT_IN_SCHEMAS:
        return "built-in", ref
    if re.match(r"^[a-z][a-z0-9+.-]*://", ref, re.I):
        return "error", f"Remote inline schemas are not supported: {ref}"
    path = os.path.realpath(
        ref if os.path.isabs(ref) else os.path.join(os.path.dirname(full), ref)
    )
    if not path.startswith(WORKSPACE + os.sep) or not os.path.isfile(path):
        return "error", f"Inline schema must be a file inside the workspace: {ref}"
    return "local", path


def pointer(error):
    return (
        "/" + "/".join(str(p) for p in error.absolute_path)
        if error.absolute_path
        else None
    )


def validate_json_files(files, ctx, result):
    seen = set()
    cache = {}
    for path in files:
        resolved, err = resolve(path)
        if err:
            result.fail(path, [{"path": None, "message": err}])
            continue
        full, rel, display = resolved
        if in_library(full):
            continue
        if ctx["schema"] and os.path.realpath(ctx["schema"]) == full:
            continue
        is_yaml = rel.endswith(ctx["yaml_ext"]) or rel.endswith(ctx["yaml_ext_short"])
        if is_yaml and not ctx["yaml_as_json"]:
            continue
        if full in seen:
            continue
        seen.add(full)
        if (ctx["skip"] and ctx["skip"].search(rel)) or ctx["exclude"].excluded(rel):
            print(f"skipping due to exclude match: {display}")
            result.skipped += 1
            continue

        with open(full, encoding="utf-8") as f:
            source = f.read()
        try:
            if is_yaml:
                docs = (
                    list(yaml.load_all(source, Loader))
                    if ctx["multi"]
                    else [yaml.load(source, Loader)]
                )
            else:
                docs = [load_json(source)]
        except (ValueError, yaml.YAMLError):
            print(f"❌ failed to parse JSON file: {display}")
            result.fail(display, [{"path": None, "message": "Invalid JSON"}])
            continue

        validator = ctx["validator"]
        if ctx["inline"]:
            kind, value = inline_reference(
                full, source, docs[0] if docs else None, is_yaml
            )
            if kind == "error":
                result.fail(display, [{"path": None, "message": value}])
                continue
            if kind != "none":
                if value not in cache:
                    try:
                        cache[value] = (
                            built_in(value)
                            if kind == "built-in"
                            else schema_from_file(value, inp("json_schema_version"))
                        )
                    except Exception as e:  # noqa: BLE001 - reported as a violation
                        result.fail(
                            display,
                            [{"path": None, "message": f"Invalid inline schema: {e}"}],
                        )
                        continue
                validator = cache[value]

        errors = []
        for index, doc in enumerate(docs):
            for e in sorted(
                validator.iter_errors(doc), key=lambda e: list(e.absolute_path)
            ):
                entry = {"path": pointer(e), "message": e.message}
                if is_yaml and len(docs) > 1:
                    entry["document"] = index
                errors.append(entry)
        if errors:
            print(f"❌ failed to parse JSON file: {display}\n{json.dumps(errors)}")
            result.fail(display, errors)
            continue
        result.passed += 1
        print(f"{display} is valid")


# yaml-schema-validator.ts. JS typeof semantics: a bool is not a number, and
# null, lists and maps are all "object".
TYPES = {"boolean": (bool,), "number": (int, float), "string": (str,)}
DISPLAY = {"boolean": "Boolean", "number": "Number", "string": "String"}


def _type(value):
    return value.lower() if isinstance(value, str) and value.lower() in TYPES else None


def _is_type(value, t):
    if t == "number" and isinstance(value, bool):
        return False
    return isinstance(value, TYPES[t])


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_length(v):
    return _num(v) or (
        isinstance(v, dict) and (_num(v.get("min")) or _num(v.get("max")))
    )


def _is_rule(node):
    return isinstance(node, dict) and (
        _type(node.get("type")) is not None
        or isinstance(node.get("required"), bool)
        or _is_length(node.get("length"))
        or isinstance(node.get("enum"), list)
    )


def _is_leaf(node):
    return _is_rule(node) and (
        _type(node.get("type")) is not None or isinstance(node.get("required"), bool)
    )


def _join(parent, child):
    return f"{parent}.{child}" if parent else str(child)


def _js(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    return "null" if v is None else str(v)


def _validate_length(value, rule, path, errors):
    if not isinstance(value, str):
        return
    length = rule.get("length")
    if _num(length) and len(value) != length:
        errors.append(
            {"path": path, "message": f"{path} must have a length of {_js(length)}."}
        )
        return
    if not isinstance(length, dict):
        return
    lo = length.get("min") if _num(length.get("min")) else None
    hi = length.get("max") if _num(length.get("max")) else None
    if lo is not None and hi is not None and (len(value) < lo or len(value) > hi):
        errors.append(
            {
                "path": path,
                "message": f"{path} must have a length between {_js(lo)} and {_js(hi)}.",
            }
        )
    elif lo is not None and len(value) < lo:
        errors.append(
            {
                "path": path,
                "message": f"{path} must have a minimum length of {_js(lo)}.",
            }
        )
    elif hi is not None and len(value) > hi:
        errors.append(
            {
                "path": path,
                "message": f"{path} must have a maximum length of {_js(hi)}.",
            }
        )


def _validate_rule(value, rule, path, errors):
    if rule.get("required") is True and value is None:
        errors.append({"path": path, "message": f"{path} is required."})
        return
    if value is None:
        return
    t = _type(rule.get("type"))
    if t is not None and not _is_type(value, t):
        errors.append(
            {"path": path, "message": f"{path} must be of type {DISPLAY[t]}."}
        )
        return
    _validate_length(value, rule, path, errors)
    enum = rule.get("enum")
    if isinstance(enum, list) and not any(
        type(e) is type(value) and e == value for e in enum
    ):
        shown = [_js(e) for e in enum]
        listed = (
            shown[0] if len(shown) <= 1 else f"{', '.join(shown[:-1])} or {shown[-1]}"
        )
        errors.append({"path": path, "message": f"{path} must be either {listed}."})


_MISSING = object()


def _validate_node(value, node, path, errors):
    if value is _MISSING:
        value = None
    if isinstance(node, list):
        if value is None:
            return
        if not isinstance(value, list):
            errors.append({"path": path, "message": f"{path} must be of type Array."})
            return
        for i, item in enumerate(value):
            _validate_node(item, node[0] if node else None, _join(path, i), errors)
        return
    if not isinstance(node, dict):
        return
    if _is_rule(node):
        _validate_rule(value, node, path, errors)
        return
    if value is not None and not isinstance(value, (dict, list)):
        return
    for key, child in node.items():
        if isinstance(value, dict):
            child_value = value.get(key)
        elif isinstance(value, list) and str(key).isdigit() and int(key) < len(value):
            child_value = value[int(key)]
        else:
            child_value = None
        _validate_node(child_value, child, _join(path, key), errors)


def _validate_extra(target, node, path, errors):
    if not isinstance(target, (dict, list)):
        return
    items = enumerate(target) if isinstance(target, list) else target.items()
    for key, value in items:
        if isinstance(target, list):
            child = node[0] if isinstance(node, list) and node else _MISSING
        else:
            child = node.get(key, _MISSING) if isinstance(node, dict) else _MISSING
        child_path = _join(path, key)
        is_object = value is None or isinstance(value, (dict, list))
        if child is _MISSING or (not is_object and not _is_leaf(child)):
            errors.append(
                {
                    "path": child_path,
                    "message": f"{child_path} is not present in schema",
                }
            )
            continue
        _validate_extra(value, child, child_path, errors)


def validate_yaml_schema(target, schema_path):
    schema = load_structured(schema_path)
    errors, extra = [], []
    _validate_node(target, schema, "", errors)
    _validate_extra(target, schema, "", extra)
    return errors + extra


def validate_yaml_files(files, ctx, result):
    seen = set()
    for path in files:
        resolved, err = resolve(path)
        if err:
            result.fail(path, [{"path": None, "message": err}])
            continue
        full, rel, display = resolved
        if in_library(full):
            continue
        if ctx["schema"] and os.path.realpath(ctx["schema"]) == full:
            continue
        if (
            ctx["json_ext"]
            and rel.endswith(ctx["json_ext"])
            and not ctx["yaml_as_json"]
        ):
            continue
        if full in seen:
            continue
        seen.add(full)
        if ctx["yaml_as_json"]:
            result.skipped += 1
            continue
        if (ctx["skip"] and ctx["skip"].search(rel)) or ctx["exclude"].excluded(rel):
            print(f"skipping due to exclude match: {display}")
            result.skipped += 1
            continue
        try:
            with open(full, encoding="utf-8") as f:
                docs = list(yaml.load_all(f.read(), Loader))
            if not ctx["multi"] and len(docs) > 1:
                raise ValueError(
                    'Multiple YAML documents found; set allow_multiple_documents: "true" '
                    "to validate Kubernetes-style multi-document YAML files."
                )
        except (ValueError, yaml.YAMLError) as e:
            print(f"❌ failed to parse YAML file: {display}")
            result.fail(
                display,
                [
                    {
                        "path": None,
                        "message": "Invalid YAML",
                        "error": str(e).splitlines()[0],
                    }
                ],
            )
            continue
        if len(docs) > 1:
            print(f"multiple documents found in file: {display}")
        if not ctx["schema"]:
            result.passed += 1
            print(f"{display} is valid")
            continue
        errors = []
        for index, doc in enumerate(docs):
            for e in validate_yaml_schema(doc, ctx["schema"]):
                entry = {"path": e["path"] or None, "message": e["message"]}
                if len(docs) > 1:
                    entry["document"] = index
                errors.append(entry)
        if errors:
            print(f"❌ failed to parse YAML file: {display}\n{json.dumps(errors)}")
            result.fail(display, errors)
            continue
        result.passed += 1
        print(f"{display} is valid")


def json_validator(exclude, mappings, ctx):
    result = Result()
    ctx = dict(
        ctx,
        skip=(
            re.compile(inp("json_exclude_regex")) if inp("json_exclude_regex") else None
        ),
    )
    if mappings:
        for m in (m for m in mappings if m["type"] == "json"):
            validate_json_files(
                m["files"],
                dict(
                    ctx,
                    schema=m["schema"],
                    inline=False,
                    validator=schema_from_file(m["schema"], m["version"]),
                ),
                result,
            )
        return result
    patterns = multiline("files")
    files = discover_explicit(patterns)
    if patterns and not files:
        sys.exit("error: files input matched no files")
    if not files:
        base, err = base_directory()
        if err:
            result.fail(inp("base_dir"), [{"path": None, "message": err}])
            return result
        exts = [ctx["json_ext"]] + (
            [ctx["yaml_ext"], ctx["yaml_ext_short"]] if ctx["yaml_as_json"] else []
        )
        files = discover_by_extension(base, exts, flag("use_dot_match"))
    schema = inp("json_schema")
    validate_json_files(
        files,
        dict(
            ctx,
            schema=schema,
            inline=flag("use_inline_schema") and not schema,
            validator=schema_from_file(schema, inp("json_schema_version")),
        ),
        result,
    )
    return result


def yaml_validator(exclude, mappings, ctx):
    result = Result()
    ctx = dict(
        ctx,
        skip=(
            re.compile(inp("yaml_exclude_regex")) if inp("yaml_exclude_regex") else None
        ),
    )
    if mappings:
        for m in (m for m in mappings if m["type"] == "yaml"):
            validate_yaml_files(m["files"], dict(ctx, schema=m["schema"]), result)
        return result
    patterns = multiline("files")
    files = discover_explicit(patterns)
    if patterns and not files:
        sys.exit("error: files input matched no files")
    if not files:
        base, err = base_directory()
        if err:
            result.fail(inp("base_dir"), [{"path": None, "message": err}])
            return result
        files = discover_by_extension(
            base, [ctx["yaml_ext"], ctx["yaml_ext_short"]], flag("use_dot_match")
        )
    validate_yaml_files(files, dict(ctx, schema=inp("yaml_schema")), result)
    return result


def check(result, kind):
    if result.passed == 0 and result.failed == 0:
        print(f"🔎 no {kind} files were detected")
        return True
    if result.success:
        print(f"✅ all {result.passed} detected {kind} files are valid")
        return True
    print(
        f"{kind} Validation Results:\n  - Passed: {result.passed}\n  - Failed: {result.failed}\n"
        f"  - Skipped: {result.skipped}\n  - Violations: {json.dumps(result.violations, indent=2, ensure_ascii=False)}"
    )
    print(f"❌ {result.failed} {kind} files failed validation")
    return False


def main():
    yaml_as_json = flag("yaml_as_json")
    exclude = Exclude()
    mappings = schema_mappings(yaml_as_json)
    ctx = {
        "exclude": exclude,
        "yaml_as_json": yaml_as_json,
        "multi": flag("allow_multiple_documents"),
        "json_ext": inp("json_extension"),
        "yaml_ext": inp("yaml_extension"),
        "yaml_ext_short": inp("yaml_extension_short"),
    }
    ok_json = check(json_validator(exclude, mappings, ctx), "JSON")
    ok_yaml = check(yaml_validator(exclude, mappings, ctx), "YAML")
    if ok_json and ok_yaml:
        return 0
    mode = inp("mode")
    if mode == "warn":
        print('warning: mode is set to "warn" - this check will not fail')
        print("❌ JSON or YAML files failed validation")
        return 0
    if mode != "fail":
        print(f"warning: unrecognized mode: {mode}")
    print("❌ JSON or YAML files failed validation")
    return 1


if __name__ == "__main__":
    sys.exit(main())
