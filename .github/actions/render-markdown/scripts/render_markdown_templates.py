#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import pathlib
import re
import sys
import json
import os
import yaml
from typing import Set, Optional, Iterable, List, Dict

INCLUDE_RE = re.compile(r"\{file:([^\}]+)\}")
VAR_RE = re.compile(r"\{var:([a-zA-Z0-9_\-]+)\}")

def abort(msg: str, code: int = 1):
    print(f"[render-md] ERROR: {msg}", file=sys.stderr)
    sys.exit(code)

def read_text(p: pathlib.Path) -> str:
    if not p.exists():
        abort(f"Datei nicht gefunden: {p}")
    return p.read_text(encoding="utf-8")

def resolve_include_path(current_tpl: pathlib.Path, rel: str, snippets_dir: pathlib.Path) -> pathlib.Path:
    rel = rel.strip()
    if rel.startswith("snippets/"):
        return (snippets_dir / rel[len("snippets/"):]).resolve()
    return (current_tpl.parent / rel).resolve()

def render_template(template_path: pathlib.Path, out_path: pathlib.Path, snippets_dir: pathlib.Path, variables: Dict[str, str],
                    seen: Optional[Set[str]] = None, depth: int = 0, max_depth: int = 20) -> None:
    if depth > max_depth:
        abort(f"Include-Tiefe überschritten (> {max_depth}) in {template_path}")
    if seen is None:
        seen = set()
    key = str(template_path.resolve())
    if key in seen:
        abort(f"Zyklischer Include erkannt bei {template_path}")
    seen.add(key)

    source = read_text(template_path)

    # Includes ersetzen
    def _include_repl(m: re.Match) -> str:
        rel = m.group(1).strip()
        inc_path = resolve_include_path(template_path, rel, snippets_dir)
        if ".tpl." in inc_path.name:
            tmp_out = inc_path.with_name(inc_path.name.replace(".tpl.", "."))
            render_template(inc_path, tmp_out, snippets_dir, variables, seen=seen, depth=depth + 1, max_depth=max_depth)
            return read_text(tmp_out)
        return read_text(inc_path)

    rendered = INCLUDE_RE.sub(_include_repl, source)

    # Variablen ersetzen
    def _var_repl(m: re.Match) -> str:
        k = m.group(1).strip()
        if k not in variables:
            abort(f"Variable '{k}' nicht definiert (in {template_path})")
        return str(variables[k])

    rendered = VAR_RE.sub(_var_repl, rendered)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(rendered, encoding="utf-8")
    print(f"[render-md] wrote: {out_path.relative_to(pathlib.Path.cwd())}")

def find_templates(paths: Iterable[str]) -> List[pathlib.Path]:
    out: List[pathlib.Path] = []
    for pattern in paths:
        p = pathlib.Path(pattern)
        if p.exists() and p.is_file():
            out.append(p.resolve())
        else:
            for m in pathlib.Path().glob(pattern):
                if m.is_file():
                    out.append(m.resolve())
    out = [p for p in out if (".tpl." in p.name and p.suffix.lower() == ".md")]
    return sorted(set(out))

def load_vars(var_args: List[str], vars_file: Optional[str]) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for arg in var_args or []:
        if "=" not in arg:
            abort(f"Ungültiges --var Format: {arg} (erwartet key=value)")
        k, v = arg.split("=", 1)
        result[k] = v
    if vars_file:
        vf = pathlib.Path(vars_file)
        if not vf.exists():
            abort(f"Vars-Datei nicht gefunden: {vf}")
        if vf.suffix.lower() in [".yaml", ".yml"]:
            extra = yaml.safe_load(vf.read_text(encoding="utf-8")) or {}
        elif vf.suffix.lower() == ".json":
            extra = json.loads(vf.read_text(encoding="utf-8"))
        else:
            abort("Vars-Datei muss .json oder .yaml sein")
        if not isinstance(extra, dict):
            abort("Vars-Datei muss ein Mapping enthalten")
        result.update({str(k): str(v) for k, v in extra.items()})
    return result

def derive_repo_name() -> str:
    gh_repo = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" in gh_repo:
        return gh_repo.split("/")[-1]
    return gh_repo or "unknown-repo"

def derive_branch_name() -> str:
    return os.environ.get("GITHUB_REF_NAME", "")

def derive_version(repo_root: pathlib.Path) -> str:
    composer = repo_root / "composer.json"
    if composer.exists():
        try:
            data = json.loads(composer.read_text(encoding="utf-8"))
            v = data.get("version")
            if isinstance(v, str) and v.strip():
                return v.strip()
        except Exception:
            pass
    return ""

def merge_vars(user_vars: Dict[str, str], repo_root: pathlib.Path) -> Dict[str, str]:
    merged = dict(user_vars)
    merged.setdefault("repo_name", derive_repo_name())
    merged.setdefault("branch_name", derive_branch_name())
    merged.setdefault("version", derive_version(repo_root))
    return merged

def main():
    parser = argparse.ArgumentParser(description="Render Markdown-Templates mit {file:...} und {var:...}")
    parser.add_argument("--snippets-dir", default="snippets", help="Pfad zum Snippets-Verzeichnis (Default: ./snippets)")
    parser.add_argument("--inputs", nargs="+", default=["**/*.tpl.*.md"], help="Glob für Templates")
    parser.add_argument("--var", dest="vars", action="append", help="Variable im Format key=value (mehrfach nutzbar)")
    parser.add_argument("--vars-file", help="JSON oder YAML Datei mit Variablen")
    args = parser.parse_args()

    repo_root = pathlib.Path(".").resolve()
    snippets_dir = (repo_root / args.snippets_dir).resolve()

    user_vars = load_vars(args.vars or [], args.vars_file)
    variables = merge_vars(user_vars, repo_root)

    templates = find_templates(args.inputs)
    if not templates:
        print("[render-md] Keine Templates gefunden")
        return

    for tpl in templates:
        out = tpl.with_name(tpl.name.replace(".tpl.", "."))
        render_template(tpl, out, snippets_dir, variables)

if __name__ == "__main__":
    main()
