"""Two-port development server.

  private port -> dist/private  (everything, live dynamic sources, COOP/COEP headers for wasm/godot)
  public port  -> dist/public   (served under the public base_url so links behave like GitHub Pages)

Both bind to 127.0.0.1 by default. `--watch` rebuilds when sources change.
"""
from __future__ import annotations

import html
import re
import sys
import threading
import time
import traceback
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .build import BuildError, build, load_all, missing_output_message, output_ok
from .config import Config
from .dynamic import is_stale, write_source

_DYNAMIC_RE = re.compile(r"^/dynamic/([A-Za-z0-9_.-]+)\.json$")


class SiteHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, ctx: dict, **kwargs):
        self.ctx = ctx
        super().__init__(*args, directory=str(ctx["directory"]), **kwargs)

    # strip the base_url prefix so /YamlStaticSite/plan/ maps to dist/public/plan/
    def _strip_prefix(self, path: str) -> str | None:
        prefix = self.ctx["prefix"]
        if prefix == "/":
            return path
        if path == prefix.rstrip("/"):
            return ""
        if path.startswith(prefix):
            return "/" + path[len(prefix):]
        return None

    def translate_path(self, path: str) -> str:
        clean = urlsplit(path).path
        stripped = self._strip_prefix(clean)
        return super().translate_path(stripped if stripped is not None else "/__not_under_prefix__")

    def do_GET(self):
        clean = urlsplit(self.path).path
        stripped = self._strip_prefix(clean)
        if stripped is None or stripped == "":
            if clean in ("/", self.ctx["prefix"].rstrip("/")):
                self.send_response(302)
                self.send_header("Location", self.ctx["prefix"])
                self.end_headers()
                return
            self.send_error(404, f"not under base url {self.ctx['prefix']}")
            return
        match = _DYNAMIC_RE.match(stripped)
        if match and self.ctx.get("live"):
            self._refresh_dynamic(match.group(1))
        super().do_GET()

    def _refresh_dynamic(self, name: str) -> None:
        """Stale-while-revalidate: serve what is on disk and re-collect in the background.

        A missing file or `?refresh=1` collects synchronously so the response carries fresh data.
        One background collection per source at a time; slow providers never block other sources.
        """
        cfg: Config = self.ctx["cfg"]
        target = self.ctx["target"]
        if name not in cfg.dynamic_sources_for(target):
            return
        out_dir = Path(self.ctx["directory"])
        path = out_dir / "dynamic" / f"{name}.json"
        ttl = cfg.dynamic_sources[name].get("ttl", cfg.serve.get("dynamic_ttl", 30))
        force = "refresh=1" in (urlsplit(self.path).query or "")
        if not force and not is_stale(path, ttl):
            return
        inflight: dict = self.ctx["inflight"]
        with self.ctx["lock"]:
            if name in inflight:
                if not force and path.is_file():
                    return  # someone is already refreshing; serve the stale copy
                event = inflight[name]
            else:
                event = inflight[name] = threading.Event()

                def _collect():
                    try:
                        write_source(cfg, target, out_dir, name)
                    except Exception as exc:  # noqa: BLE001
                        print(f"[dynamic] {name}: {exc}", file=sys.stderr)
                    finally:
                        with self.ctx["lock"]:
                            inflight.pop(name, None)
                        event.set()

                threading.Thread(target=_collect, name=f"yss-dynamic-{name}", daemon=True).start()
        if force or not path.is_file():
            event.wait(timeout=cfg.dynamic_sources[name].get("timeout", 600))

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        if self.ctx.get("coop_coep"):
            self.send_header("Cross-Origin-Opener-Policy", "same-origin")
            self.send_header("Cross-Origin-Embedder-Policy", "require-corp")
        super().end_headers()

    def log_message(self, fmt, *args):
        print(f"[{self.ctx['label']}] {fmt % args}", file=sys.stderr)


def make_server(cfg: Config, target: str, host: str, port: int, live: bool) -> ThreadingHTTPServer:
    ctx = {
        "cfg": cfg,
        "target": target,
        "directory": cfg.out_dir(target),
        "prefix": cfg.base_url(target),
        "label": target,
        "live": live and target == "private",
        "coop_coep": cfg.serve.get("coop_coep", True),
        "lock": threading.Lock(),
        "inflight": {},
    }
    server = ThreadingHTTPServer((host, port), partial(SiteHandler, ctx=ctx))
    server.daemon_threads = True
    return server


def _snapshot(paths: list[Path]) -> dict[str, float]:
    snap: dict[str, float] = {}
    for base in paths:
        if base.is_file():
            snap[str(base)] = base.stat().st_mtime
        elif base.is_dir():
            for p in base.rglob("*"):
                if p.is_file() and "__pycache__" not in p.parts:
                    try:
                        snap[str(p)] = p.stat().st_mtime
                    except OSError:
                        pass
    return snap


def _write_error_page(cfg: Config, target: str, message: str) -> None:
    out = cfg.out_dir(target)
    out.mkdir(parents=True, exist_ok=True)
    body = html.escape(message) if target == "private" else "Build failed. See the yss console for details."
    (out / "index.html").write_text(
        f"<!doctype html><meta charset='utf-8'><title>build failed</title>"
        f"<body style='font-family:system-ui;padding:2rem'><h1>[{target}] build failed</h1><pre>{body}</pre>",
        encoding="utf-8",
    )


def rebuild(cfg: Config, targets: list[str], run_dynamic: bool = True) -> bool:
    ok = True
    try:
        loaded = load_all(cfg)
    except Exception as exc:  # noqa: BLE001
        print(f"[build] load failed: {exc}")
        for t in targets:
            _write_error_page(cfg, t, str(exc))
        return False
    for target in targets:
        try:
            report = build(cfg, target, loaded=loaded, run_dynamic=run_dynamic)
            print(f"[build] {report.summary()}")
            if not output_ok(report):
                # Someone else is building into the same dist/. Say so, but do NOT write the
                # error page: that would clobber the other builder's output in turn (gh-19).
                ok = False
                print("[build] " + missing_output_message(report))
            for warning in report.warnings:
                print(f"[build] warning: {warning}")
            for flag in report.flags:
                print(f"[build] flag: {flag}")
        except BuildError as exc:
            ok = False
            print(f"[build] {exc}")
            _write_error_page(cfg, target, str(exc))
        except Exception:  # noqa: BLE001
            ok = False
            print("[build] unexpected error:\n" + traceback.format_exc())
            _write_error_page(cfg, target, traceback.format_exc())
    return ok


def serve(
    cfg: Config,
    watch: bool = True,
    host: str | None = None,
    private_port: int | None = None,
    public_port: int | None = None,
    targets: tuple[str, ...] = ("private", "public"),
    initial_build: bool = True,
    run_dynamic: bool = True,
) -> None:
    host = host or cfg.serve.get("host", "127.0.0.1")
    ports = {
        "private": private_port or cfg.serve.get("private_port", 8800),
        "public": public_port or cfg.serve.get("public_port", 8801),
    }
    targets = tuple(t for t in targets if t in cfg.targets)
    if initial_build:
        rebuild(cfg, list(targets), run_dynamic=run_dynamic)
    servers = []
    for target in targets:
        cfg.out_dir(target).mkdir(parents=True, exist_ok=True)
        server = make_server(cfg, target, host, ports[target], live=cfg.serve.get("live_dynamic", True))
        thread = threading.Thread(target=server.serve_forever, name=f"yss-{target}", daemon=True)
        thread.start()
        servers.append(server)
        print(f"[serve] {target:8s} http://{host}:{ports[target]}{cfg.base_url(target)}  <- {cfg.out_dir(target).relative_to(cfg.root).as_posix()}")
    print("[serve] Ctrl+C to stop" + (" (watching for changes)" if watch else ""))
    try:
        if watch:
            paths = cfg.watch_paths()
            last = _snapshot(paths)
            while True:
                time.sleep(1.0)
                current = _snapshot(paths)
                if current != last:
                    changed = [p for p in current if current.get(p) != last.get(p)] + [p for p in last if p not in current]
                    print(f"[watch] {len(changed)} change(s), rebuilding...")
                    last = current
                    rebuild(cfg, list(targets), run_dynamic=run_dynamic)
        else:
            while True:
                time.sleep(3600)
    except KeyboardInterrupt:
        print("\n[serve] stopping")
    finally:
        for server in servers:
            server.shutdown()
