# yss code reader (VS Code)

The editor half of the `yss-code` bridge. It frames the **private** yss preview in a webview and
answers two things the page cannot do from a browser: open the real file at the real line, and
ask a language server where a symbol is defined.

Everything works without it. The code map's source pane already highlights, already resolves a
line range, and already turns a known identifier into a jump — this adds the editor, not the
feature.

## Use it

1. `python -m yss serve` in the repository (the private target is <http://127.0.0.1:8800/>).
2. Open that repository in VS Code.
3. Load the extension, either way:
   - **Development host** — open `tools/vscode-yss/` in VS Code and press <kbd>F5</kbd>.
   - **Installed** — copy or symlink this folder into `~/.vscode/extensions/yss-code-reader/`
     (`%USERPROFILE%\.vscode\extensions\` on Windows) and restart VS Code.
4. Run **yss: Open the private preview** from the command palette.
5. Open a code map export. `open in VS Code` and `go to definition` are in the pane's bar.

`go to definition` uses whatever language server the workspace already has, so it answers for
Rust through rust-analyzer, Python through Pylance or Pyright, and anything else the same way —
including names the build-time index never held, because a language server sees local bindings,
dependency types and macro output and a text parser does not.

## Settings

| Setting | Default | What it does |
|---|---|---|
| `yss.url` | `http://127.0.0.1:8800/` | The preview to frame. Use the private port; the public one on 8801 is redacted and carries no source text. |
| `yss.root` | *(first workspace folder)* | Workspace-relative folder the site's repo-relative paths resolve against. |
| `yss.followSeeks` | `false` | Move the editor every time you follow a link inside the page, not only when you ask. |

## What it will not do

The page is framed content, so every path it sends is checked before anything is opened:
absolute paths, drive letters, URL schemes and `..` segments are refused, and the resolved
path is re-tested against the root afterwards. A page can only ever name a file inside the
configured workspace folder.

Nothing is ever written. The bridge opens documents and moves the cursor; it has no message
that edits, saves or runs anything.
