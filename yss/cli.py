"""Command line interface: python -m yss <command>."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .binding import BindError, resolve_binding
from .build import BuildError, build, load_all
from .config import Config, ConfigError
from .dynamic import write_all
from .evidence import check as evidence_check, format_report
from .ghpages import GhError, setup as pages_setup
from .loader import LoadError, SchemaRegistry, dump_yaml
from .scaffold import ScaffoldError, init_site, new_doc, new_page, new_prefab
from .skillpack import check as skills_check, install as skills_install
from .visibility import filter_for_target, is_visible, scan_tree


def _cfg(args) -> Config:
    return Config.load(args.root)


def _targets(cfg: Config, value: str) -> list[str]:
    if value == "all":
        return list(cfg.targets)
    return [value]


# --- commands ---------------------------------------------------------------
def cmd_validate(args) -> int:
    cfg = _cfg(args)
    loaded = load_all(cfg)
    if loaded.errors:
        print("validation failed:")
        for err in loaded.errors:
            print("  - " + err)
        return 1
    print(
        f"ok: {len(loaded.docs)} docs, {len(loaded.pages)} pages, {len(loaded.prefabs)} prefabs, "
        f"{len(loaded.registry.schemas)} schemas"
    )
    return 0


def cmd_build(args) -> int:
    cfg = _cfg(args)
    loaded = load_all(cfg)
    if loaded.errors:
        print("validation failed:")
        for err in loaded.errors:
            print("  - " + err)
        return 1
    rc = 0
    for target in _targets(cfg, args.target):
        out = Path(args.out) if args.out and args.target != "all" else None
        try:
            report = build(cfg, target, out_dir=out, strict=args.strict, run_dynamic=not args.no_dynamic, loaded=loaded)
        except BuildError as exc:
            print(str(exc))
            rc = 1
            continue
        print(report.summary())
        for warning in report.warnings:
            print("  warning: " + warning)
        for flag in report.flags:
            print("  flag: " + flag)
    return rc


def cmd_serve(args) -> int:
    from .server import serve

    cfg = _cfg(args)
    targets = tuple(cfg.targets) if args.target == "all" else (args.target,)
    serve(
        cfg,
        watch=not args.no_watch,
        host=args.host,
        private_port=args.private_port,
        public_port=args.public_port,
        targets=targets,
        initial_build=not args.no_build,
        run_dynamic=not args.no_dynamic,
    )
    return 0


def cmd_dynamic(args) -> int:
    cfg = _cfg(args)
    rc = 0
    for target in _targets(cfg, args.target):
        out = cfg.out_dir(target)
        if not out.is_dir():
            print(f"[{target}] no build at {out}; run `python -m yss build` first")
            rc = 1
            continue
        results = write_all(cfg, target, out, names=args.names or None)
        for name, env in results.items():
            status = "ok" if env.get("ok") else f"FAILED: {env.get('error')}"
            print(f"[{target}] {name}: {status} ({env.get('seconds')}s)")
            if not env.get("ok"):
                rc = 1
        if not results:
            print(f"[{target}] no matching dynamic sources")
    return rc


def cmd_check(args) -> int:
    cfg = _cfg(args)
    loaded = load_all(cfg)
    if loaded.errors:
        print("validation failed:")
        for err in loaded.errors:
            print("  - " + err)
        return 1
    docs = loaded.docs
    if args.doc:
        docs = {k: v for k, v in docs.items() if k in args.doc or v.get("_local_id") in args.doc}
    report = evidence_check(cfg, docs, loaded.registry, run_commands=args.run_commands, git_recency=args.git)
    if args.json:
        print(json.dumps({"claims": [c.as_dict() for c in report.claims], "summary": report.summary()}, indent=2))
    else:
        print(format_report(report, verbose=args.verbose))
        print(f"{len(report.claims)} claims: {len(report.stale)} stale, {len(report.warnings)} warnings")
    return 1 if report.stale or (args.strict and report.warnings) else 0


def cmd_refs(args) -> int:
    from .loader import find_inline_refs, iter_strings, parse_ref, resolve_doc_id

    cfg = _cfg(args)
    loaded = load_all(cfg)
    if loaded.errors:
        print("validation failed; run `python -m yss validate`", file=sys.stderr)
        return 1
    want_doc, want_item = parse_ref(args.ref)
    want_doc = resolve_doc_id(want_doc, None, loaded.docs) if want_doc else None
    if want_doc is None:
        print(f"unknown doc in '{args.ref}'", file=sys.stderr)
        return 1
    hits = []
    for doc_id, doc in loaded.docs.items():
        ann = loaded.registry.annotations(f"doc.{doc.get('kind')}")["ref"]
        cid = doc.get("_collection")
        for path, text in iter_strings(doc):
            parts = path.split("/")
            key = parts[-1] if not parts[-1].isdigit() else (parts[-2] if len(parts) > 1 else "")
            if key in ann:
                if ann[key] == "doc" and want_item is None and resolve_doc_id(text, cid, loaded.docs) == want_doc:
                    hits.append((doc["_source"], path, text))
                elif ann[key] == "item":
                    if "/" in text:
                        d, _, i = text.rpartition("/")
                        if resolve_doc_id(d, cid, loaded.docs) == want_doc and (want_item is None or i == want_item):
                            hits.append((doc["_source"], path, text))
                    elif doc_id == want_doc and want_item and text == want_item:
                        hits.append((doc["_source"], path, text))
            for raw, d, i, _label in find_inline_refs(text):
                target = doc_id if d is None else resolve_doc_id(d, cid, loaded.docs)
                if target == want_doc and (want_item is None or i == want_item):
                    hits.append((doc["_source"], path, raw))
    for page in loaded.pages:
        for path, text in iter_strings(page):
            for raw, d, i, _label in find_inline_refs(text):
                target = resolve_doc_id(d, page.get("_collection"), loaded.docs) if d else None
                if target == want_doc and (want_item is None or i == want_item):
                    hits.append((page["_source"], path, raw))
    for where, path, text in hits:
        print(f"{where}: at {path}: {text}")
    print(f"{len(hits)} inbound reference(s) to {args.ref}")
    return 0


def cmd_scan(args) -> int:
    cfg = _cfg(args)
    forbidden, flags = cfg.redaction_lists(args.target)
    if not forbidden and not flags:
        print("no forbidden or flagged strings configured (.yss/local.yaml or env vars); nothing to scan")
        return 0
    root = Path(args.path).resolve() if args.path else cfg.root
    fhits, whits = scan_tree(root, forbidden, flags)
    for rel, line, masked in fhits:
        print(f"FORBIDDEN {rel}:{line}: {masked}")
    for rel, line, masked in whits:
        print(f"flag      {rel}:{line}: {masked}")
    print(f"scanned {root.name}: {len(fhits)} forbidden, {len(whits)} flagged")
    return 1 if fhits else 0


def cmd_ls(args) -> int:
    cfg = _cfg(args)
    loaded = load_all(cfg)
    what = args.what
    if what in ("docs", "all"):
        print("docs:")
        for doc_id, doc in sorted(loaded.docs.items()):
            print(f"  {doc_id:20s} {doc.get('kind', '?'):12s} {doc.get('visibility', 'public'):8s} {doc.get('title', '')}  ({doc['_source']})")
    if what in ("pages", "all"):
        print("pages:")
        for page in loaded.pages:
            print(f"  {page['id']:20s} {page['route']:20s} {page.get('visibility', 'public'):8s} {page['title']}  ({page['_source']})")
    if what in ("prefabs", "all"):
        print("prefabs:")
        for name, prefab in sorted(loaded.prefabs.items()):
            params = ", ".join(f"{k}{'*' if (v or {}).get('required') else ''}" for k, v in (prefab.get("params") or {}).items())
            print(f"  {name:20s} [{params}]  {prefab.get('description', '')[:70]}")
    if what in ("collections", "all"):
        print("collections:")
        for c in cfg.collections():
            n = sum(1 for d in loaded.docs.values() if d.get("_collection", "") == c.id)
            print(f"  {(c.id or '(root)'):20s} {c.title:24s} docs={n:<3d} route={c.route_prefix}  hooks={'yes' if c.hooks_path else 'no'}")
    if what in ("kinds", "all"):
        print("doc kinds: " + ", ".join(loaded.registry.doc_kinds()))
        print("vocabularies: " + "; ".join(f"{k}={'|'.join(v)}" for k, v in cfg.vocabularies.items()))
    if what in ("dynamic", "all"):
        print("dynamic sources:")
        for name, spec in cfg.dynamic_sources.items():
            how = spec.get("provider") or spec.get("command") or spec.get("file")
            print(f"  {name:20s} targets={spec.get('targets') or list(cfg.targets)}  {how}")
    if loaded.errors:
        print(f"({len(loaded.errors)} validation errors; run `python -m yss validate`)")
    return 0


def cmd_query(args) -> int:
    cfg = _cfg(args)
    loaded = load_all(cfg)
    if loaded.errors:
        print("validation failed; run `python -m yss validate`", file=sys.stderr)
        return 1
    target = args.target
    docs = {k: filter_for_target(v, target) for k, v in loaded.docs.items() if is_visible(v, target)}
    pages = [filter_for_target(p, target) for p in loaded.pages if is_visible(p, target)]
    ctx = {"docs": docs, "pages": pages, "site": cfg.site, "prefabs": loaded.prefabs, "all_doc_ids": list(loaded.docs)}
    spec: dict = {"from": args.expr}
    if args.where:
        where = {}
        for pair in args.where:
            key, _, value = pair.partition("=")
            where[key] = value.split(",") if "," in value else value
        spec["where"] = where
    if args.sort:
        spec["sort"] = args.sort
    if args.limit:
        spec["limit"] = args.limit
    if args.fields:
        spec["fields"] = args.fields.split(",")
    try:
        result = resolve_binding(spec, ctx)
    except BindError as exc:
        print(f"query error: {exc}", file=sys.stderr)
        return 1
    if args.yaml:
        print(dump_yaml(result), end="")
    else:
        print(json.dumps(result, indent=2, default=str))
    return 0


def cmd_schema(args) -> int:
    cfg = _cfg(args)
    reg = SchemaRegistry(cfg.schema_dirs())
    if not args.name:
        for name in reg.names():
            print(f"{name:16s} {reg.sources[name]}")
        return 0
    try:
        schema = reg.get(args.name)
    except LoadError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(dump_yaml(schema) if args.yaml else json.dumps(schema, indent=2), end="")
    return 0


def cmd_new(args) -> int:
    cfg = _cfg(args)
    reg = SchemaRegistry(cfg.schema_dirs())
    try:
        if args.what == "doc":
            path = new_doc(cfg, reg, args.kind, args.id, args.title, args.force)
        elif args.what == "page":
            path = new_page(cfg, reg, args.id, args.title, args.doc, args.force)
        else:
            path = new_prefab(cfg, reg, args.id, args.force)
    except ScaffoldError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"wrote {path.relative_to(cfg.root).as_posix()}")
    return 0


def cmd_init(args) -> int:
    root = Path(args.root or ".").resolve()
    try:
        written = init_site(root, args.name or root.name, args.force)
    except ScaffoldError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    for path in written:
        print(f"wrote {path.relative_to(root).as_posix()}")
    return 0


def cmd_pages_setup(args) -> int:
    cfg = _cfg(args)
    try:
        lines = pages_setup(
            cfg,
            repo=args.repo,
            dry_run=args.dry_run,
            secrets=not args.no_secrets,
            pages=not args.no_pages,
            run_workflow=args.run,
        )
    except GhError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for line in lines:
        print(line)
    return 0


def cmd_skills(args) -> int:
    root = Path(args.root).resolve() if args.root else Path.cwd()
    try:
        root = Config.load(root).root
    except ConfigError:
        pass  # a repo without site.yaml yet is fine
    results = skills_install(root, force=args.force) if args.install else skills_check(root)
    for name, status in results:
        print(f"  {name:14s} {status}")
    if not results:
        print("no skills packaged")
        return 1
    if args.install:
        kept = [n for n, s in results if s == "kept"]
        if kept:
            print(f"kept local copies that differ (use --force to overwrite): {', '.join(kept)}")
        return 0
    bad = [n for n, s in results if s != "ok"]
    if bad:
        print(f"{len(bad)} skill(s) missing or out of date; run `python -m yss skills --install --force`")
        return 1
    print(f"{len(results)} skills installed and current in {(root / '.claude' / 'skills').as_posix()}")
    return 0


# --- parser -----------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="yss", description="YAML static site: agent-first docs, human-first site.")
    parser.add_argument("--root", help="site root (directory containing site.yaml); default: search upwards from cwd")
    parser.add_argument("--version", action="version", version=f"yss {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("validate", help="validate docs, pages, prefabs and site.yaml against their schemas")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("build", help="build one or all targets into dist/<target>")
    p.add_argument("--target", "-t", default="all", help="public | private | all (default)")
    p.add_argument("--out", help="output directory (single target only)")
    p.add_argument("--strict", dest="strict", action="store_true", default=None,
                   help="fail on flagged strings and stale evidence too (default: site.yaml build.strict)")
    p.add_argument("--no-strict", dest="strict", action="store_false",
                   help="never fail on flagged strings or stale evidence, whatever site.yaml says")
    p.add_argument("--no-dynamic", action="store_true", help="skip dynamic data sources")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("serve", help="serve dist/private and dist/public on two local ports")
    p.add_argument("--target", "-t", default="all")
    p.add_argument("--host")
    p.add_argument("--private-port", type=int)
    p.add_argument("--public-port", type=int)
    p.add_argument("--no-watch", action="store_true", help="do not rebuild on source changes")
    p.add_argument("--no-build", action="store_true", help="serve existing dist without rebuilding first")
    p.add_argument("--no-dynamic", action="store_true")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("dynamic", help="(re)collect dynamic data sources into an existing build")
    p.add_argument("names", nargs="*", help="source names (default: all)")
    p.add_argument("--target", "-t", default="all")
    p.set_defaults(func=cmd_dynamic)

    p = sub.add_parser("check", help="evaluate evidence claims (paths, globs, symbols, git recency; commands with --run-commands)")
    p.add_argument("doc", nargs="*", help="limit to these doc ids")
    p.add_argument("--run-commands", dest="run_commands", action="store_true", default=None,
                   help="also run command evidence (slow); default: site.yaml / collection.yaml evidence.run_commands")
    p.add_argument("--no-run-commands", dest="run_commands", action="store_false",
                   help="never run command evidence, whatever the config says")
    p.add_argument("--git", dest="git", action="store_true", default=None,
                   help="force git recency warnings on; default: site.yaml / collection.yaml evidence.git_recency")
    p.add_argument("--no-git", dest="git", action="store_false", help="skip git recency warnings")
    p.add_argument("--strict", action="store_true", help="warnings fail too")
    p.add_argument("--verbose", "-v", action="store_true", help="list passing claims as well")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("refs", help="list inbound references to a doc or item: refs plan#m8-evidence")
    p.add_argument("ref")
    p.set_defaults(func=cmd_refs)

    p = sub.add_parser("scan", help="scan the source tree for forbidden/flagged strings before publishing")
    p.add_argument("path", nargs="?", help="directory to scan (default: site root)")
    p.add_argument("--target", "-t", default="public", help="target whose redaction rules apply")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("ls", help="list docs, pages, prefabs, doc kinds and dynamic sources")
    p.add_argument("what", nargs="?", default="all", choices=["all", "docs", "pages", "prefabs", "kinds", "dynamic", "collections"])
    p.set_defaults(func=cmd_ls)

    p = sub.add_parser("query", help="resolve a binding expression against the docs and print JSON")
    p.add_argument("expr", help="e.g. plan.milestones, $docs, codemap.modules")
    p.add_argument("--where", "-w", action="append", help="field=value (repeatable; comma for any-of)")
    p.add_argument("--sort", "-s", help="field or -field")
    p.add_argument("--limit", "-n", type=int)
    p.add_argument("--fields", "-f", help="comma separated fields to keep")
    p.add_argument("--target", "-t", default="private")
    p.add_argument("--yaml", action="store_true")
    p.set_defaults(func=cmd_query)

    p = sub.add_parser("schema", help="print a schema (doc.plan, page, prefab, site ...) or list them")
    p.add_argument("name", nargs="?")
    p.add_argument("--yaml", action="store_true")
    p.set_defaults(func=cmd_schema)

    p = sub.add_parser("new", help="scaffold a doc, page or prefab from its schema")
    p.add_argument("what", choices=["doc", "page", "prefab"])
    p.add_argument("kind_or_id", help="doc: <kind>; page/prefab: <id>")
    p.add_argument("id", nargs="?", help="doc id (docs only)")
    p.add_argument("--title")
    p.add_argument("--doc", help="page: doc id to bind a starter section to")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_new)

    p = sub.add_parser("pages-setup", help="via gh: store redaction secrets from .yss/local.yaml and set Pages source to GitHub Actions")
    p.add_argument("--repo", help="owner/name (default: the repo of the current directory)")
    p.add_argument("--dry-run", action="store_true", help="show what would happen; never prints secret values")
    p.add_argument("--no-secrets", action="store_true")
    p.add_argument("--no-pages", action="store_true")
    p.add_argument("--run", action="store_true", help="trigger the pages workflow afterwards")
    p.set_defaults(func=cmd_pages_setup)

    p = sub.add_parser("skills", help="check or install the agent skill suite into .claude/skills/ of a repo")
    p.add_argument("--install", action="store_true", help="copy the packaged skills into the repo")
    p.add_argument("--force", action="store_true", help="overwrite local copies that differ")
    p.set_defaults(func=cmd_skills)

    p = sub.add_parser("init", help="create site.yaml and the standard directories in a repo")
    p.add_argument("--name")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "new":
        if args.what == "doc":
            if not args.id:
                parser.error("new doc needs: <kind> <id>")
            args.kind = args.kind_or_id
        else:
            args.kind = None
            args.id = args.kind_or_id
    try:
        return int(args.func(args) or 0)
    except (ConfigError, LoadError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
