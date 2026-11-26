"""A strict, dependency-free reader for the small YAML subset used by resolvers/*.yaml.

Supported: block mappings and block lists by indentation (spaces only), "- key: value" list items, inline
lists [a, b], quoted and plain scalars, ints, floats, true/false/null, and # comments. Anything else raises
ValueError, so a resolver file can never be read differently by two readers. Keeping the core package free of
dependencies is a project rule; PyYAML reads these files the same way (tested).
"""
import json, re

_NUM = re.compile(r"^-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][-+]?[0-9]+)?$")


def _strip_comment(line):
    out, q = [], None
    for i, ch in enumerate(line):
        if q:
            out.append(ch)
            if ch == q and (q == "'" or line[i - 1] != "\\"):
                q = None
        elif ch in "\"'":
            q = ch; out.append(ch)
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            break
        else:
            out.append(ch)
    return "".join(out).rstrip()


def scalar(s):
    s = s.strip()
    if s == "" or s in ("null", "~"):
        return None
    if s == "true":
        return True
    if s == "false":
        return False
    if s[0] == '"':
        if not s.endswith('"') or len(s) < 2:
            raise ValueError(f"unterminated string: {s}")
        return json.loads(s)
    if s[0] == "'":
        if not s.endswith("'") or len(s) < 2:
            raise ValueError(f"unterminated string: {s}")
        return s[1:-1].replace("''", "'")
    if s[0] == "[":
        if not s.endswith("]"):
            raise ValueError(f"unterminated list: {s}")
        inner = s[1:-1].strip()
        return [scalar(p) for p in _split_flow(inner)] if inner else []
    if s[0] in "{&*!|>%@`":
        raise ValueError(f"unsupported YAML construct: {s}")
    if s.lower() in ("yes", "no", "on", "off", "y", "n"):
        # YAML 1.1 readers (PyYAML) turn these into booleans: require quotes so every reader agrees.
        raise ValueError(f"ambiguous plain scalar {s!r}: quote it")
    if _NUM.match(s):
        return float(s) if any(c in s for c in ".eE") else int(s)
    return s


def _split_flow(s):
    parts, cur, q = [], [], None
    for ch in s:
        if q:
            cur.append(ch)
            if ch == q:
                q = None
        elif ch in "\"'":
            q = ch; cur.append(ch)
        elif ch == ",":
            parts.append("".join(cur)); cur = []
        elif ch in "[]{}":
            raise ValueError("nested flow collections are not supported")
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return parts


def _key_value(text):
    m = re.match(r'^("(?:[^"\\]|\\.)*"|[A-Za-z0-9_.\-/]+):(?:\s+(.*))?$', text)
    if not m:
        raise ValueError(f"expected 'key: value', got: {text}")
    k = m.group(1)
    return (json.loads(k) if k.startswith('"') else k), (m.group(2) or "")


def loads(text):
    lines = []
    for n, raw in enumerate(text.splitlines(), 1):
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise ValueError(f"line {n}: tabs are not allowed for indentation")
        line = _strip_comment(raw)
        if line.strip() in ("", "---"):
            continue
        lines.append((len(line) - len(line.lstrip(" ")), line.strip(), n))
    pos = [0]

    def block(indent):
        if pos[0] >= len(lines):
            return None
        ind, t, n = lines[pos[0]]
        if ind != indent:
            raise ValueError(f"line {n}: bad indentation")
        if t.startswith("- ") or t == "-":
            return seq(indent)
        return mapping(indent)

    def child(parent_indent, n):
        if pos[0] < len(lines) and lines[pos[0]][0] > parent_indent:
            return block(lines[pos[0]][0])
        return None

    def mapping(indent, first=None):
        out = {}
        while pos[0] < len(lines):
            ind, t, n = lines[pos[0]]
            if ind < indent:
                break
            if ind != indent or t.startswith("- "):
                raise ValueError(f"line {n}: bad indentation")
            k, v = _key_value(t)
            if k in out:
                raise ValueError(f"line {n}: duplicate key {k}")
            pos[0] += 1
            out[k] = scalar(v) if v.strip() else child(indent, n)
        return out

    def seq(indent):
        out = []
        while pos[0] < len(lines):
            ind, t, n = lines[pos[0]]
            if ind < indent:
                break
            if ind != indent or not (t.startswith("- ") or t == "-"):
                raise ValueError(f"line {n}: bad list item")
            rest = t[1:].strip()
            if not rest:
                pos[0] += 1
                out.append(child(indent, n)); continue
            if re.match(r'^("(?:[^"\\]|\\.)*"|[A-Za-z0-9_.\-/]+):(\s|$)', rest):
                # "- key: value" starts a mapping whose keys sit at indent + 2
                lines[pos[0]] = (indent + 2, rest, n)
                out.append(mapping(indent + 2)); continue
            pos[0] += 1
            out.append(scalar(rest))
        return out

    doc = block(lines[0][0]) if lines else None
    if pos[0] != len(lines):
        raise ValueError(f"line {lines[pos[0]][2]}: unexpected content")
    return doc
