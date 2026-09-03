"""Scaffolding: `yss init`, `yss new doc|page|prefab`. Skeletons are derived from the schemas."""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

from .config import Config
from .loader import SchemaRegistry, dump_yaml
from .skillpack import install as install_skills


class ScaffoldError(Exception):
    pass


def _placeholder(schema: dict, key: str, defs: dict | None = None) -> Any:
    defs = defs or {}
    schema = _deref(schema, defs)
    if "const" in schema:
        return schema["const"]
    if "default" in schema:
        return schema["default"]
    if "enum" in schema:
        return schema["enum"][0]
    stype = schema.get("type")
    if isinstance(stype, list):
        stype = stype[0]
    if stype == "array":
        item_schema = schema.get("items") or {}
        if "allOf" in item_schema or item_schema.get("type") == "object":
            return [skeleton(item_schema, required_only=True, defs=defs)]
        return []
    if stype == "object":
        return skeleton(schema, required_only=True, defs=defs)
    if stype == "integer":
        return 1
    if stype == "number":
        return 1.0
    if stype == "boolean":
        return False
    if schema.get("format") == "date":
        return date.today().isoformat()
    if schema.get("pattern") or key in ("id", "version"):
        return "todo-" + re.sub(r"[^a-z0-9]+", "-", key.lower()).strip("-")
    return f"TODO {key}"


def _merged_properties(schema: dict) -> tuple[dict, list]:
    props: dict = {}
    required: list = []
    for part in schema.get("allOf") or []:
        p, r = _merged_properties(part)
        props.update(p)
        required += r
    if "$ref" in schema and schema["$ref"].startswith("#/$defs/"):
        # refs are resolved by the caller via registry-level skeleton(); ignore here
        pass
    props.update(schema.get("properties") or {})
    required += schema.get("required") or []
    return props, required


def skeleton(schema: dict, required_only: bool = False, defs: dict | None = None) -> dict:
    defs = defs or schema.get("$defs") or {}
    schema = _deref(schema, defs)
    props, required = _merged_properties(schema)
    out: dict[str, Any] = {}
    for key, sub in props.items():
        sub = _deref(sub, defs)
        if required_only and key not in required:
            continue
        if key in ("private_notes", "x-"):
            continue
        out[key] = _placeholder(sub, key, defs)
    return out


def _deref(schema: dict, defs: dict) -> dict:
    seen = 0
    while isinstance(schema, dict) and "$ref" in schema and seen < 10:
        ref = schema["$ref"]
        if not ref.startswith("#/$defs/"):
            break
        target = defs.get(ref[len("#/$defs/"):])
        if target is None:
            break
        merged = dict(target)
        merged.update({k: v for k, v in schema.items() if k != "$ref"})
        schema = merged
        seen += 1
    if isinstance(schema, dict) and "allOf" in schema:
        # Shared bases first, the schema's own declarations last: a kind that declares `status`
        # with its own vocabulary must win over the open `status` it inherits from item_base,
        # or the scaffold writes a placeholder its own validator rejects. Same order as
        # _merged_properties, which is what everything downstream assumes.
        merged = {k: v for k, v in schema.items() if k != "allOf"}
        props: dict = {}
        required: list = []
        for part in schema["allOf"]:
            part = _deref(part, defs)
            props.update(part.get("properties") or {})
            required += part.get("required") or []
        props.update(merged.get("properties") or {})
        required += merged.get("required") or []
        merged["properties"] = props
        merged["required"] = required
        schema = merged
    return schema


def new_doc(cfg: Config, reg: SchemaRegistry, kind: str, doc_id: str, title: str | None = None, force: bool = False) -> Path:
    name = f"doc.{kind}"
    if name not in reg.schemas:
        raise ScaffoldError(f"unknown doc kind '{kind}' (known: {', '.join(reg.doc_kinds())})")
    schema = reg.resolved(name, cfg.vocabularies, cfg.limits)
    data = skeleton(schema, required_only=True)
    envelope = {
        "kind": kind,
        "id": doc_id,
        "title": title or doc_id.replace("-", " ").title(),
        "summary": "TODO one-paragraph summary",
        "visibility": "public",
        "status": "active",
        "updated": date.today().isoformat(),
        "tags": [],
    }
    body = {k: v for k, v in data.items() if k not in envelope}
    doc = {**envelope, **body}
    path = cfg.path("docs") / f"{doc_id}.yaml"
    _write(path, _header(schema, name) + dump_yaml(doc), force)
    return path


def new_page(cfg: Config, reg: SchemaRegistry, page_id: str, title: str | None = None, doc_id: str | None = None, force: bool = False) -> Path:
    page: dict[str, Any] = {
        "id": page_id,
        "route": "/" if page_id == "index" else f"/{page_id}/",
        "title": title or page_id.replace("-", " ").title(),
        "summary": "TODO what this page is for and who reads it",
        "visibility": "public",
        "nav": {"label": title or page_id.title(), "order": 50},
        "docs": [doc_id] if doc_id else [],
        "sections": [
            {"id": "intro", "type": "markdown", "markdown": "TODO framing text for the human reader.\n"},
        ],
    }
    if doc_id:
        page["sections"].append(
            {
                "id": "items",
                "type": "prefab",
                "heading": "Items",
                "prefab": "card-grid",
                "args": {"items": {"from": f"{doc_id}.TODO_list_field", "map": {"title": "title", "body": "summary"}}},
            }
        )
    path = cfg.path("pages") / f"{page_id}.yaml"
    _write(path, _header(reg.get("page"), "page") + dump_yaml(page), force)
    return path


def new_prefab(cfg: Config, reg: SchemaRegistry, name: str, force: bool = False) -> Path:
    prefab = {
        "name": name,
        "description": "TODO what this prefab renders and when to use it",
        "category": "item",
        "params": {
            "title": {"type": "string", "required": True, "description": "Heading text"},
            "body": {"type": "markdown", "description": "Markdown body"},
        },
        "template": (
            f'<div class="{name}">\n'
            "  <h3>{{ title }}</h3>\n"
            "  {% if body %}<div class=\"prose\">{{ body | md }}</div>{% endif %}\n"
            "</div>\n"
        ),
        "css": f".{name} {{ padding: .5rem 0; }}\n",
        "examples": [{"args": {"title": "Example", "body": "Some **markdown**."}}],
    }
    path = cfg.path("prefabs") / f"{name}.yaml"
    _write(path, _header(reg.get("prefab"), "prefab") + dump_yaml(prefab), force)
    return path


def new_collection(
    cfg: Config,
    reg: SchemaRegistry,
    collection_id: str,
    title: str | None = None,
    root: str | None = None,
    force: bool = False,
) -> list[Path]:
    specs = cfg.data.get("collections") or []
    patterns = [(s if isinstance(s, dict) else {"root": s}).get("root") for s in specs]
    patterns = [p for p in patterns if p]
    if not patterns:
        raise ScaffoldError(
            "site.yaml has no 'collections:' root glob (e.g. `collections: [{root: musings/*}]`); "
            "add one before scaffolding a collection"
        )
    if root:
        if root not in patterns:
            raise ScaffoldError(f"--root '{root}' is not one of site.yaml's collections[].root patterns ({', '.join(patterns)})")
        pattern = root
    elif len(patterns) == 1:
        pattern = patterns[0]
    else:
        raise ScaffoldError(
            f"site.yaml declares multiple collections[].root patterns ({', '.join(patterns)}); pass --root to choose one"
        )
    if not pattern.endswith("/*"):
        raise ScaffoldError(f"collections[].root '{pattern}' is not a '<dir>/*' glob; the scaffolder only supports that shape")
    base_dir = cfg.root / pattern[: -len("/*")]
    dest = base_dir / collection_id

    title = title or collection_id.replace("-", " ").replace("_", " ").title()
    written: list[Path] = []

    schema = reg.get("collection")
    collection_data = {
        "title": title,
        "summary": "TODO one paragraph about this collection.",
        "emblem": "TODO emoji, or a path under this collection's assets/",
        "order": 100,
    }
    collection_comment = (
        _header(schema, "collection")
        + "#\n"
        + "# The card contract (uncomment and adjust to show this collection on its parent page):\n"
        + "# hero: false\n"
        + "# links:\n"
        + '#   - {label: Overview, href: "", kind: page}\n'
        + '#   - {label: The playable, href: "play/index.html", kind: play}\n'
        + "#\n"
        + "# mounts:\n"
        + "#   - {path: play, at: play/, targets: [private]}\n"
        + "#\n"
        + "# Anything unique to this collection - extra generated docs, dynamic providers, custom\n"
        + "# markdown, build hooks - goes in a hooks.py next to this file, not upstream in yss.\n"
        + "# See the collection schema (`python -m yss schema collection --yaml`) and the yss-publish skill.\n"
    )
    collection_path = dest / "collection.yaml"
    _write(collection_path, collection_comment + dump_yaml(collection_data), force)
    written.append(collection_path)

    plan_schema = reg.resolved("doc.plan", cfg.vocabularies, cfg.limits)
    plan_data = skeleton(plan_schema, required_only=True)
    plan_envelope = {
        "kind": "plan",
        "id": "plan",
        "title": f"{title} plan",
        "summary": "TODO one-paragraph summary",
        "visibility": "public",
        "status": "active",
        "updated": date.today().isoformat(),
        "tags": [],
    }
    plan_body = {k: v for k, v in plan_data.items() if k not in plan_envelope}
    if not plan_body.get("milestones"):
        plan_body["milestones"] = [{"id": "m1-todo", "title": "TODO first milestone", "status": "planned"}]
    plan_doc = {**plan_envelope, **plan_body}
    plan_path = dest / "docs" / "plan.yaml"
    _write(plan_path, _header(plan_schema, "doc.plan") + dump_yaml(plan_doc), force)
    written.append(plan_path)

    index_page = {
        "id": "index",
        "route": "/",
        "title": title,
        "summary": "TODO what this collection is about.",
        "visibility": "public",
        "nav": {"label": title, "order": 1},
        "docs": ["plan"],
        "sections": [
            {"id": "intro", "type": "markdown", "markdown": "TODO framing text for the human reader.\n"},
            {
                "id": "tasks",
                "type": "prefab",
                "heading": "Plan",
                "prefab": "task-list",
                "args": {"milestones": {"from": "plan.milestones"}},
            },
        ],
    }
    index_path = dest / "pages" / "index.yaml"
    _write(index_path, _header(reg.get("page"), "page") + dump_yaml(index_page), force)
    written.append(index_path)

    return written


def init_site(root: Path, name: str, force: bool = False) -> list[Path]:
    root = root.resolve()
    written = []
    site = {
        "site": {"name": name, "description": "TODO one line about this project"},
        "targets": {
            "public": {"base_url": f"/{root.name}/", "redact": True},
            "private": {"base_url": "/", "redact": False},
        },
        "dynamic": {
            "sources": {
                "buildinfo": {"provider": "yss.providers.buildinfo:collect"},
            }
        },
    }
    site_path = root / "site.yaml"
    _write(site_path, "# yss site configuration. Schema: yss/schemas/site.schema.yaml\n" + dump_yaml(site), force)
    written.append(site_path)
    for sub in ("docs", "site/pages", "site/prefabs", "site/layouts", "site/assets", "schemas", ".yss"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    example = root / ".yss" / "local.example.yaml"
    _write(
        example,
        "# Copy to .yss/local.yaml (gitignored). Strings here must never appear in a public build.\n"
        "forbidden_strings: []\n# Strings that only produce a warning:\nflag_strings: []\n",
        force,
    )
    written.append(example)
    gitignore = root / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("dist/\n.yss/local.yaml\n__pycache__/\n*.pyc\n", encoding="utf-8")
        written.append(gitignore)
    cfg = Config.load(root)
    reg = SchemaRegistry(cfg.schema_dirs())
    written.append(new_doc(cfg, reg, "plan", "plan", "Plan", force))
    written.append(new_page(cfg, reg, "index", name, None, force))
    for skill, status in install_skills(root, force=force):
        if status in ("installed", "updated"):
            written.append(root / ".claude" / "skills" / skill / "SKILL.md")
    claude_md = root / "CLAUDE.md"
    if not claude_md.exists():
        claude_md.write_text(
            f"# {name}\n\nProject knowledge lives in `docs/*.yaml` (schema-validated structured docs), never in new "
            "markdown files. Load the `yss` skill first; then `yss-doc`, `yss-page`, `yss-prefab` or `yss-publish`.\n\n"
            "```bash\nyss validate\nyss build --target all --no-dynamic\nyss serve\n```\n",
            encoding="utf-8",
        )
        written.append(claude_md)
    return written


def _header(schema: dict, name: str) -> str:
    lines = [f"# {schema.get('title', name)} - schema: {name}"]
    if schema.get("description"):
        lines.append("# " + schema["description"].strip().replace("\n", "\n# "))
    optional = [k for k in (schema.get("properties") or {}) if k not in (schema.get("required") or [])]
    if optional:
        lines.append("# optional top-level fields: " + ", ".join(sorted(optional)))
    lines.append("# run `python -m yss validate` after editing.")
    return "\n".join(lines) + "\n"


def _write(path: Path, text: str, force: bool) -> None:
    if path.exists() and not force:
        raise ScaffoldError(f"{path} exists (use --force to overwrite)")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
