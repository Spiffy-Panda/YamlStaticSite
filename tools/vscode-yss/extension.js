/**
 * yss code reader - the editor half of the `yss-code` bridge (adr-039).
 *
 * The site's `module-list` prefab already turns a code map export into a source pane with
 * highlighting and seek-to-definition, using an index computed at build time. Two things it
 * cannot do from inside a browser: open the real file, and answer for a name the index never
 * held - a local binding, a method on a type from a dependency, anything a macro generated.
 * Both are ordinary in an editor, so when the page is framed by this extension it asks.
 *
 * The protocol is four messages and deliberately dull:
 *
 *   page -> host   {source: "yss-code", type: "hello", version: 1}
 *   host -> page   {source: "yss-code-host", type: "ready", name: "VS Code"}
 *   page -> host   {source: "yss-code", type: "open"|"reveal", file, line, endLine, symbol}
 *   page -> host   {source: "yss-code", type: "definition", file, line, symbol}
 *   host -> page   {source: "yss-code-host", type: "definition", ok, file, line}
 *
 * `file` is always repo-relative, because an absolute path must never be written into a build
 * (adr-004) and the host is the side that knows where the repository is. That is also the whole
 * of the trust boundary here: the page is content this extension frames, so every path it sends
 * is treated as hostile until `resolve()` has proved it stays inside the configured root.
 */
const path = require("path");
const vscode = require("vscode");

/** Message types the host will act on. Anything else is dropped without a reply. */
const HANDLED = new Set(["hello", "open", "reveal", "definition"]);

/**
 * The workspace folder repo-relative paths resolve against.
 * @returns {vscode.Uri | null}
 */
function rootUri() {
  const folders = vscode.workspace.workspaceFolders;
  if (!folders || !folders.length) return null;
  const configured = vscode.workspace.getConfiguration("yss").get("root") || "";
  const base = folders[0].uri;
  return configured ? vscode.Uri.joinPath(base, ...configured.split(/[\\/]+/).filter(Boolean)) : base;
}

/**
 * Turn a path the page sent into a Uri inside the root, or null.
 *
 * Rejects anything absolute, anything with a drive letter, any `..` segment and any scheme.
 * A page cannot name a file outside the workspace, however it spells the attempt.
 * @param {unknown} rel
 * @returns {vscode.Uri | null}
 */
function resolve(rel) {
  if (typeof rel !== "string" || !rel || rel.length > 1024) return null;
  if (/^[a-zA-Z]+:/.test(rel) || rel.startsWith("/") || rel.startsWith("\\")) return null;
  const parts = rel.split(/[\\/]+/).filter(Boolean);
  if (!parts.length || parts.some((p) => p === "." || p === "..")) return null;
  const root = rootUri();
  if (!root) return null;
  const uri = vscode.Uri.joinPath(root, ...parts);
  // joinPath normalises, so compare afterwards as well: the answer, not the input, is what counts.
  const inside = path.relative(root.fsPath, uri.fsPath);
  if (inside.startsWith("..") || path.isAbsolute(inside)) return null;
  return uri;
}

/**
 * Show `uri` with lines start..end selected.
 * @param {vscode.Uri} uri
 * @param {number} start 1-based
 * @param {number} end 1-based
 * @param {boolean} preserveFocus
 */
async function reveal(uri, start, end, preserveFocus) {
  const document = await vscode.workspace.openTextDocument(uri);
  const from = Math.max(0, (Number(start) || 1) - 1);
  const to = Math.max(from, (Number(end) || start || 1) - 1);
  const range = new vscode.Range(from, 0, to, 0);
  await vscode.window.showTextDocument(document, {
    preserveFocus,
    preview: true,
    selection: new vscode.Range(from, 0, from, 0),
  });
  const editor = vscode.window.activeTextEditor;
  if (editor && editor.document.uri.toString() === uri.toString()) {
    editor.revealRange(range, vscode.TextEditorRevealType.InCenterIfOutsideViewport);
  }
  return document;
}

/**
 * Ask the language server where `symbol` is defined, starting from the line the page is showing.
 *
 * This is the part the page cannot do for itself: rust-analyzer, Pylance and the rest already
 * know what the build-time index does not, and they are a command away.
 * @returns {{file: string, line: number} | null}
 */
async function definitionOf(uri, line, symbol) {
  const document = await vscode.workspace.openTextDocument(uri);
  const row = Math.min(Math.max(0, (Number(line) || 1) - 1), document.lineCount - 1);
  const text = document.lineAt(row).text;
  const column = typeof symbol === "string" && symbol ? text.indexOf(symbol.split(/::|\./).pop()) : -1;
  const position = new vscode.Position(row, column >= 0 ? column : text.length - text.trimStart().length);

  const hits = await vscode.commands.executeCommand("vscode.executeDefinitionProvider", uri, position);
  if (!hits || !hits.length) return null;
  const hit = hits[0];
  const target = hit.targetUri || hit.uri;
  const range = hit.targetSelectionRange || hit.targetRange || hit.range;
  if (!target || !range) return null;
  await reveal(target, range.start.line + 1, range.end.line + 1, true);
  const root = rootUri();
  const rel = root ? path.relative(root.fsPath, target.fsPath).split(path.sep).join("/") : "";
  return { file: rel.startsWith("..") ? "" : rel, line: range.start.line + 1 };
}

/**
 * The webview's own document: an iframe of the preview and nothing else.
 *
 * It is a relay, not a UI. Everything a reader interacts with is the yss page inside the frame,
 * which is the point - the site stays one site, whether it is read in a browser or in here.
 */
function shell(url) {
  const origin = new URL(url).origin;
  // A nonce rather than 'unsafe-inline': the relay below is the only script this document is
  // allowed to run, and the framed page is content, not code this webview trusts.
  const nonce = require("crypto").randomBytes(16).toString("base64");
  return `<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <meta http-equiv="Content-Security-Policy"
          content="default-src 'none'; frame-src ${origin} http://localhost:* http://127.0.0.1:*; script-src 'nonce-${nonce}'; style-src 'unsafe-inline';">
    <style>
      html, body { height: 100%; margin: 0; }
      iframe { border: 0; width: 100%; height: 100%; display: block; }
      .down { font: 13px sans-serif; padding: 1rem; color: var(--vscode-descriptionForeground); }
    </style>
  </head>
  <body>
    <iframe id="site" src="${url}"></iframe>
    <script nonce="${nonce}">
      // Two hops in each direction, because the page and the extension cannot see each other:
      // page <-> this document (window messages) <-> extension (vscode.postMessage).
      const vscode = acquireVsCodeApi();
      const frame = document.getElementById("site");
      window.addEventListener("message", (event) => {
        const msg = event.data;
        if (!msg || typeof msg !== "object") return;
        if (msg.source === "yss-code") { vscode.postMessage(msg); return; }
        if (msg.source === "yss-code-host" && frame.contentWindow) {
          frame.contentWindow.postMessage(msg, "*");
        }
      });
    </script>
  </body>
</html>`;
}

function activate(context) {
  context.subscriptions.push(
    vscode.commands.registerCommand("yss.openPreview", () => {
      const url = vscode.workspace.getConfiguration("yss").get("url") || "http://127.0.0.1:8800/";
      let port = 8800;
      try {
        port = Number(new URL(url).port) || 80;
      } catch (err) {
        vscode.window.showErrorMessage(`yss: ${url} is not a URL.`);
        return;
      }

      const panel = vscode.window.createWebviewPanel("yss.preview", "yss preview", vscode.ViewColumn.Beside, {
        enableScripts: true,
        retainContextWhenHidden: true,
        portMapping: [{ webviewPort: port, extensionHostPort: port }],
      });
      panel.webview.html = shell(url);

      panel.webview.onDidReceiveMessage(async (msg) => {
        if (!msg || msg.source !== "yss-code" || !HANDLED.has(msg.type)) return;

        if (msg.type === "hello") {
          panel.webview.postMessage({ source: "yss-code-host", type: "ready", name: "VS Code" });
          return;
        }

        const uri = resolve(msg.file);
        if (!uri) {
          // Not an error worth a popup: a page may legitimately cite a path outside this
          // workspace, and the pane it came from still shows the source either way.
          console.warn("yss: refused a path outside the workspace root:", msg.file);
          return;
        }

        try {
          if (msg.type === "open") {
            await reveal(uri, msg.line, msg.endLine, false);
          } else if (msg.type === "reveal") {
            if (vscode.workspace.getConfiguration("yss").get("followSeeks")) {
              await reveal(uri, msg.line, msg.endLine, true);
            }
          } else if (msg.type === "definition") {
            const found = await definitionOf(uri, msg.line, msg.symbol);
            panel.webview.postMessage({
              source: "yss-code-host",
              type: "definition",
              ok: Boolean(found && found.file),
              file: found ? found.file : "",
              line: found ? found.line : 0,
            });
            if (!found) {
              vscode.window.setStatusBarMessage("yss: no definition found for that symbol", 4000);
            }
          }
        } catch (err) {
          vscode.window.showErrorMessage(`yss: ${err && err.message ? err.message : err}`);
        }
      });
    })
  );
}

function deactivate() {}

module.exports = { activate, deactivate, resolve, shell };
