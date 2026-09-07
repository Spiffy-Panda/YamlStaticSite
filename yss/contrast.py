"""WCAG contrast arithmetic and a tolerant custom-property scanner (gh-31).

`yss.css` paints the collection bar with `--accent-2` and colours everything on it through the
four on-accent tokens gh-26 introduced, each consumed with the old value as its `var()` fallback:

    color: var(--on-accent-2, var(--fg));

Nothing declares those tokens - not the base stylesheet, not the stock palettes, not the generated
`collections.css` - so the fallback is always the one taken, and the fallback is exactly the wrong
pairing: `--fg`/`--fg-2` are specified against `--bg`, not against `--accent-2`. A theme that uses
`--accent-2` as a real secondary accent therefore ships an unreadable bar and nothing says so.

A `:root` default cannot fix that (a custom property resolves `var()` on the element that declares
it, so `:root { --on-accent-2: var(--fg) }` freezes the *site's* foreground), and deriving a value
into `collections.css` cannot either (that file is linked *before* a collection's `theme.css`, so a
derived body-class rule would beat an author's own `:root` declaration). What is left, and what
generalises to the next contrast regression, is to measure the shipped stylesheets at build time.

This module is pure: it is handed CSS *text* and returns numbers and strings. All the file reading
lives in `build.py`, so everything here is trivially testable.

Tolerance is the design rule. The scanner is a regex, not a CSS parser, and any value that is not a
hex literal - `var()`, `color-mix()`, `oklch()`, a named colour - is *unknown*. An unknown value is
skipped silently and never guessed at: an author using `color-mix()` for their on-accent colour is
doing something reasonable and must not be blocked by a checker that cannot read it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# WCAG 2.x AA for normal-size text. The bar's title, links and pill are all body-size or smaller,
# so the large-text 3:1 allowance never applies here.
AA_NORMAL = 4.5

_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
# The final declaration in a block often has no trailing `;`, and the block text handed to this
# regex stops before the closing `}`, so end-of-string terminates a declaration too.
_DECL_RE = re.compile(r"--([A-Za-z0-9_-]+)\s*:\s*([^;{}]+?)\s*(?=[;}]|$)")
_HEX_RE = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
# A block opener: everything up to the `{` that is not itself a `{`/`}`/`;`.
_BLOCK_RE = re.compile(r"([^{};]*?)\{")
_DARK_RE = re.compile(r"prefers-color-scheme\s*:\s*dark")

LIGHT = "light"
DARK = "dark"


# --- colour arithmetic ---------------------------------------------------------------------


def parse_hex(value: str | None) -> tuple[int, int, int] | None:
    """`#rgb` or `#rrggbb` to an (r, g, b) triple; anything else is None (unknown, not an error).

    `#rrggbbaa` is deliberately *not* accepted: a translucent colour composites against whatever is
    behind it, so its contrast is not a property of the declaration alone.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not _HEX_RE.match(text):
        return None
    digits = text[1:]
    if len(digits) == 3:
        digits = "".join(c * 2 for c in digits)
    return (int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16))


def normalize_hex(value: str | None) -> str | None:
    """The canonical `#rrggbb` spelling of a hex literal, or None if it is not one."""
    rgb = parse_hex(value)
    return None if rgb is None else "#%02x%02x%02x" % rgb


def _channel(component: int) -> float:
    c = component / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(color: str | tuple[int, int, int]) -> float | None:
    """WCAG 2.x relative luminance, or None when the colour is not a hex literal."""
    rgb = color if isinstance(color, tuple) else parse_hex(color)
    if rgb is None:
        return None
    r, g, b = (_channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(
    a: str | tuple[int, int, int], b: str | tuple[int, int, int]
) -> float | None:
    """WCAG 2.x contrast ratio in [1, 21], or None when either colour is unknown."""
    la, lb = relative_luminance(a), relative_luminance(b)
    if la is None or lb is None:
        return None
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


# --- the scanner ---------------------------------------------------------------------------


def scan_stylesheet(text: str) -> dict[str, dict[str, str]]:
    """Custom properties declared in a stylesheet's `:root`, split by colour scheme.

    Returns `{"light": {token: raw value}, "dark": {...}}` with the leading `--` stripped from the
    names and the values left exactly as written - resolving them is `resolve_color`'s job, and a
    value it cannot read has to survive this far to be recognised as *declared but unknown*.

    Declarations inside a `@media (prefers-color-scheme: dark)` block go to "dark"; declarations in
    a `:root` block outside any at-rule go to "light". Everything else - a `.some-class` rule, a
    `print` media block, a `@supports` - is ignored, because a token whose scope the checker cannot
    reason about is exactly the kind of thing it must not guess at.
    """
    out: dict[str, dict[str, str]] = {LIGHT: {}, DARK: {}}
    if not text:
        return out
    css = _COMMENT_RE.sub(" ", text)
    for scheme, body in _scopes(css):
        for selector, block in _blocks(body):
            if ":root" not in selector:
                continue
            for name, value in _DECL_RE.findall(block):
                out[scheme][name] = value.strip()
    return out


def _scopes(css: str) -> list[tuple[str, str]]:
    """(scheme, css text) pairs: the top level as light, each dark media block as dark."""
    scopes: list[tuple[str, str]] = []
    top: list[str] = []
    i = 0
    while True:
        at = css.find("@media", i)
        if at < 0:
            top.append(css[i:])
            break
        top.append(css[i:at])
        open_brace = css.find("{", at)
        if open_brace < 0:
            break
        prelude = css[at:open_brace]
        end = _match_brace(css, open_brace)
        body = css[open_brace + 1 : end]
        if _DARK_RE.search(prelude):
            scopes.append((DARK, body))
        # A non-dark media block (print, a width query) contributes nothing: its tokens apply only
        # under conditions this checker has no opinion about.
        i = end + 1
    scopes.append((LIGHT, "".join(top)))
    return scopes


def _match_brace(css: str, open_brace: int) -> int:
    """Index of the `}` closing the `{` at `open_brace`; len(css) if the stylesheet is truncated."""
    depth = 0
    for idx in range(open_brace, len(css)):
        if css[idx] == "{":
            depth += 1
        elif css[idx] == "}":
            depth -= 1
            if depth == 0:
                return idx
    return len(css)


def _blocks(css: str) -> list[tuple[str, str]]:
    """(selector, declaration text) for each top-level rule in `css`."""
    rules = []
    i = 0
    while True:
        match = _BLOCK_RE.search(css, i)
        if not match:
            break
        end = _match_brace(css, match.end() - 1)
        rules.append((match.group(1).strip(), css[match.end() : end]))
        i = end + 1
    return rules


# --- resolution ----------------------------------------------------------------------------


def merge(*layers: dict[str, str]) -> dict[str, str]:
    """Later layers win, mirroring the order the build links the stylesheets in."""
    out: dict[str, str] = {}
    for layer in layers:
        out.update({k: v for k, v in (layer or {}).items()})
    return out


def cascade(scans: list[dict[str, dict[str, str]]]) -> dict[str, dict[str, str]]:
    """Flatten scanned stylesheets, in link order, into one token set per colour scheme.

    All of these declarations sit on `:root`, so specificity never separates them and document
    order decides: within one stylesheet its dark block follows its light block, and a stylesheet
    linked later beats every one before it. The dark set therefore inherits every light-scheme
    token that no dark block overrides, which is how a browser resolves it too.
    """
    light = merge(*[s.get(LIGHT) or {} for s in scans])
    dark: dict[str, str] = {}
    for scan in scans:
        dark.update(scan.get(LIGHT) or {})
        dark.update(scan.get(DARK) or {})
    return {LIGHT: light, DARK: dark}


def resolve_color(tokens: dict[str, str], chain: tuple[str, ...] | list[str]) -> str | None:
    """Walk a `var(--a, var(--b))` fallback chain the way the browser does.

    The first token in the chain that is *declared* decides the outcome: if its value is a hex
    literal that is the answer, and if it is anything else the answer is unknown (None) - a
    declared `color-mix()` shadows the fallback just as it does in the cascade. Only an entirely
    undeclared token moves on to the next link. A chain with nothing declared is unknown too.
    """
    for name in chain:
        if name in tokens:
            return normalize_hex(tokens[name])
    return None


# --- the pairs the collection bar puts on screen ---------------------------------------------


@dataclass(frozen=True)
class Pair:
    """One foreground/background pairing `yss.css` actually paints, with its fallback chains."""

    label: str
    fg: tuple[str, ...]
    bg: tuple[str, ...]
    hint: str  # the token an author should declare to fix a failure


PAIRS: tuple[Pair, ...] = (
    # .collection-title: color: var(--on-accent-2, var(--fg)) on .collection-bar's background.
    Pair("collection title", ("on-accent-2", "fg"), ("collection-bar-bg", "accent-2"), "--on-accent-2"),
    # .sub-nav a - the 1.06:1 link gh-26 and gh-31 were both filed for.
    Pair("sub-nav link", ("on-accent-2-muted", "fg-2"), ("collection-bar-bg", "accent-2"), "--on-accent-2-muted"),
    # .sub-nav a.active / :hover: color: var(--accent) on var(--on-accent-2-surface, var(--bg-2)).
    Pair("active pill", ("accent",), ("on-accent-2-surface", "bg-2"), "--on-accent-2-surface"),
)


@dataclass(frozen=True)
class Finding:
    label: str  # what the human calls the thing being checked: "collection bamboo", "site theme"
    scheme: str
    pair: Pair
    fg: str
    bg: str
    ratio: float

    def __str__(self) -> str:
        return (
            f"{self.label} ({self.scheme}): {self.pair.label} {self.fg} on {self.bg} = "
            f"{self.ratio:.2f}:1 (< {AA_NORMAL}:1); declare {self.pair.hint} in its theme.css"
        )


def check_scheme(
    tokens: dict[str, str], label: str, scheme: str, threshold: float = AA_NORMAL
) -> list[Finding]:
    """Every pair in one colour scheme. Unresolvable pairs are skipped, never reported."""
    findings = []
    for pair in PAIRS:
        fg = resolve_color(tokens, pair.fg)
        bg = resolve_color(tokens, pair.bg)
        ratio = contrast_ratio(fg, bg) if fg and bg else None
        if ratio is not None and ratio < threshold:
            findings.append(Finding(label, scheme, pair, fg, bg, ratio))
    return findings


def check_theme(
    schemes: dict[str, dict[str, str]], label: str, threshold: float = AA_NORMAL
) -> list[str]:
    """Both colour schemes of one resolved token set, as warning strings ready for the report."""
    return [
        str(f)
        for scheme in (LIGHT, DARK)
        for f in check_scheme(schemes.get(scheme) or {}, label, scheme, threshold)
    ]
