"""Regenerate the profile README: the top repos by stars, as pinned-style cards.

Run from anywhere; paths resolve from the repository root. Reads GITHUB_TOKEN
when set (the Actions token is enough, everything read here is public). Writes
cards/*.svg and the marked section of README.md. With --preview it also writes
preview.html, a local mock of the profile README box in both themes.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import logging.handlers
import os
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

API = "https://api.github.com"
ACCEPT_JSON = "application/vnd.github+json"
HTTP_TIMEOUT_SECONDS = 30
PER_PAGE = 100
ENV_TOKEN = "GITHUB_TOKEN"
ENV_USER = "PROFILE_USER"
DEFAULT_USER = "psyb0t"
TOP_N = 12

ROOT = Path(__file__).resolve().parent.parent
CARDS_DIR = ROOT / "cards"
README = ROOT / "README.md"
PREVIEW = ROOT / "preview.html"
LOG_FILE = ROOT / ".logs" / "update.log"
LOG_MAX_BYTES = 5_000_000
LOG_BACKUPS = 3

REPOS_START = "<!-- top-repos:start -->"
REPOS_END = "<!-- top-repos:end -->"

SVG_NS = "http://www.w3.org/2000/svg"
FONT = '-apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans",Helvetica,Arial,sans-serif'
FONT_SIZE_NAME = 14
FONT_SIZE_TEXT = 12
ELLIPSIS = "…"

# Card geometry: two cards side by side fill the README column at about 1:1.
CARD_W = 416
CARD_RADIUS = 6
PAD = 16
ICON_SIZE = 16
NAME_ROW_H = 20
ROW_GAP = 8
DESC_LINE_H = 18
DESC_MAX_LINES = 3
DESC_CHARS_PER_LINE = 64
FOOTER_GAP = 12
FOOTER_H = 18
LABEL_TEXT = "Public"
LABEL_H = 18
LABEL_PAD_X = 7
LANGUAGE_DOT_R = 6
FOOTER_ITEM_GAP = 16
CARD_IMG_WIDTH = "49%"

THEME_LIGHT = "light"
THEME_DARK = "dark"
DARK_MEDIA = "(prefers-color-scheme: dark)"

# Primer colors of GitHub's default light and dark themes.
THEMES = {
    THEME_LIGHT: {
        "bg": "#ffffff",
        "border": "#d1d9e0",
        "link": "#0969da",
        "muted": "#59636e",
        "fg": "#1f2328",
    },
    THEME_DARK: {
        "bg": "#0d1117",
        "border": "#3d444d",
        "link": "#4493f8",
        "muted": "#9198a1",
        "fg": "#f0f6fc",
    },
}

# Linguist colors for the languages these repos use; anything else is grey.
LANGUAGE_COLORS = {
    "Go": "#00ADD8",
    "Python": "#3572A5",
    "Shell": "#89e051",
    "JavaScript": "#f1e05a",
    "TypeScript": "#3178c6",
    "HTML": "#e34c26",
    "CSS": "#663399",
    "Makefile": "#427819",
    "Dockerfile": "#384d54",
    "Kotlin": "#A97BFF",
    "MQL5": "#4A76B8",
    "MQL4": "#62A8D6",
    "C": "#555555",
    "C++": "#f34b7d",
    "Rust": "#dea584",
    "Lua": "#000080",
}
DEFAULT_LANGUAGE_COLOR = "#8b949e"
LANGUAGE_DOT_STROKE = "rgba(31,35,40,0.15)"

# Octicons 16px, MIT licensed, https://primer.style/octicons
ICON_REPO = (
    "M2 2.5A2.5 2.5 0 0 1 4.5 0h8.75a.75.75 0 0 1 .75.75v12.5a.75.75 0 0 1-.75.75h-2.5a.75.75"
    " 0 0 1 0-1.5h1.75v-2h-8a1 1 0 0 0-.714 1.7.75.75 0 1 1-1.072 1.05A2.495 2.495 0 0 1 2"
    " 11.5Zm10.5-1h-8a1 1 0 0 0-1 1v6.708A2.486 2.486 0 0 1 4.5 9h8ZM5 12.25a.25.25 0 0 1"
    " .25-.25h3.5a.25.25 0 0 1 .25.25v3.25a.25.25 0 0 1-.4.2l-1.45-1.087a.249.249 0 0 0-.3"
    " 0L5.4 15.7a.25.25 0 0 1-.4-.2Z"
)
ICON_STAR = (
    "M8 .25a.75.75 0 0 1 .673.418l1.882 3.815 4.21.612a.75.75 0 0 1 .416 1.279l-3.046 2.97.719"
    " 4.192a.751.751 0 0 1-1.088.791L8 12.347l-3.766 1.98a.75.75 0 0 1-1.088-.79l.72-4.194L.818"
    " 6.374a.75.75 0 0 1 .416-1.28l4.21-.611L7.327.668A.75.75 0 0 1 8 .25Zm0 2.445L6.615"
    " 5.5a.75.75 0 0 1-.564.41l-3.097.45 2.24 2.184a.75.75 0 0 1 .216.664l-.528 3.084"
    " 2.769-1.456a.75.75 0 0 1 .698 0l2.77 1.456-.53-3.084a.75.75 0 0 1 .216-.664l2.24-2.183"
    "-3.096-.45a.75.75 0 0 1-.564-.41L8 2.694Z"
)
ICON_FORK = (
    "M5 5.372v.878c0 .414.336.75.75.75h4.5a.75.75 0 0 0 .75-.75v-.878a2.25 2.25 0 1 1 1.5"
    " 0v.878a2.25 2.25 0 0 1-2.25 2.25h-1.5v2.128a2.251 2.251 0 1 1-1.5 0V8.5h-1.5A2.25 2.25 0"
    " 0 1 3.5 6.25v-.878a2.25 2.25 0 1 1 1.5 0ZM5 3.25a.75.75 0 1 0-1.5 0 .75.75 0 0 0 1.5"
    " 0Zm6.75.75a.75.75 0 1 0 0-1.5.75.75 0 0 0 0 1.5Zm-3 8.75a.75.75 0 1 0-1.5 0 .75.75 0 0"
    " 0 1.5 0Z"
)

# Average glyph widths of the system UI font, as a fraction of the font size.
GLYPH_NARROW = set("iljtfr.,:;'!|() -")
GLYPH_WIDE = set("mwMW")
GLYPH_W_NARROW = 0.30
GLYPH_W_WIDE = 0.85
GLYPH_W_UPPER = 0.66
GLYPH_W_DEFAULT = 0.55
BOLD_WIDTH_FACTOR = 1.06

ET.register_namespace("", SVG_NS)


class UpdateError(Exception):
    """A GitHub API call or a file update failed; the README is left as it was."""


@dataclass
class Repo:
    name: str
    url: str
    description: str
    language: str
    stars: int
    forks: int
    is_fork: bool
    is_archived: bool


def configure_logging() -> None:
    formatter = JsonFormatter()
    LOG_FILE.parent.mkdir(exist_ok=True)
    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stderr),
        logging.handlers.RotatingFileHandler(
            LOG_FILE,
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUPS,
        ),
    ]
    for handler in handlers:
        handler.setFormatter(formatter)
    logging.basicConfig(level=logging.INFO, handlers=handlers)


class JsonFormatter(logging.Formatter):
    """One JSON object per line with time, level, file, line, func, msg and extras."""

    _RESERVED = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message"}

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "time": dt.datetime.fromtimestamp(record.created, dt.timezone.utc).isoformat(),
            "level": record.levelname,
            "file": record.filename,
            "line": record.lineno,
            "func": record.funcName,
            "msg": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key in self._RESERVED:
                continue
            entry[key] = value
        if record.exc_info:
            entry["err"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def api_get(path: str, accept: str = ACCEPT_JSON) -> Any:
    """GET an API path; a list result is followed through every Link page."""
    url = path if path.startswith("http") else API + path
    collected: list[Any] = []
    while url:
        request = urllib.request.Request(url, headers={"Accept": accept})
        token = os.environ.get(ENV_TOKEN)
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
                body = json.loads(response.read())
                link = response.headers.get("Link", "")
        except (urllib.error.URLError, json.JSONDecodeError) as err:
            raise UpdateError(f"GET {url}") from err
        if not isinstance(body, list):
            return body
        collected.extend(body)
        match = re.search(r'<([^>]+)>;\s*rel="next"', link)
        url = match.group(1) if match else ""
    return collected


def fetch_repos(user: str) -> list[Repo]:
    raw = api_get(f"/users/{user}/repos?type=owner&per_page={PER_PAGE}")
    repos = [
        Repo(
            name=r["name"],
            url=r["html_url"],
            description=r["description"] or "",
            language=r["language"] or "",
            stars=r["stargazers_count"],
            forks=r["forks_count"],
            is_fork=r["fork"],
            is_archived=r["archived"],
        )
        for r in raw
    ]
    logger.info("repos fetched", extra={"user": user, "count": len(repos)})
    return repos


def top_repos(repos: list[Repo]) -> list[Repo]:
    candidates = [r for r in repos if not r.is_fork and not r.is_archived]
    candidates.sort(key=lambda r: (-r.stars, r.name.lower()))
    top = candidates[:TOP_N]
    logger.info(
        "top repos picked",
        extra={
            "candidates": len(candidates),
            "skipped_forks_or_archived": len(repos) - len(candidates),
            "top": [f"{r.name}:{r.stars}" for r in top],
        },
    )
    return top


def text_width(text: str, size: float, is_bold: bool = False) -> float:
    """Estimated rendered width of text in pixels; SVG cannot measure text itself."""
    width = 0.0
    for ch in text:
        if ch in GLYPH_NARROW:
            width += GLYPH_W_NARROW
            continue
        if ch in GLYPH_WIDE:
            width += GLYPH_W_WIDE
            continue
        width += GLYPH_W_UPPER if ch.isupper() else GLYPH_W_DEFAULT
    return width * size * (BOLD_WIDTH_FACTOR if is_bold else 1.0)


def wrap(text: str, chars: int, max_lines: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if len(candidate) <= chars:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = word
    if current:
        lines.append(current)
    if len(lines) <= max_lines:
        return lines
    kept = lines[:max_lines]
    kept[-1] = kept[-1][: chars - 1].rstrip() + ELLIPSIS
    return kept


def svg_root(width: int, height: int) -> ET.Element:
    return ET.Element(
        f"{{{SVG_NS}}}svg",
        {
            "width": str(width),
            "height": str(height),
            "viewBox": f"0 0 {width} {height}",
            "font-family": FONT,
        },
    )


def svg(parent: ET.Element, tag: str, text: str | None = None, **attrs: object) -> ET.Element:
    """Append an SVG child; attribute names use _ for - (font_size -> font-size)."""
    element = ET.SubElement(
        parent,
        f"{{{SVG_NS}}}{tag}",
        {key.replace("_", "-"): str(value) for key, value in attrs.items()},
    )
    if text is not None:
        element.text = text
    return element


def frame(root: ET.Element, width: int, height: int, colors: dict[str, str]) -> None:
    svg(
        root,
        "rect",
        x=0.5,
        y=0.5,
        width=width - 1,
        height=height - 1,
        rx=CARD_RADIUS,
        fill=colors["bg"],
        stroke=colors["border"],
    )


def card_height(desc_lines: int) -> int:
    return (
        PAD + NAME_ROW_H + ROW_GAP + max(desc_lines, 1) * DESC_LINE_H + FOOTER_GAP + FOOTER_H + PAD
    )


def fit_name(name: str, max_width: float) -> str:
    fitted = name
    while text_width(fitted, FONT_SIZE_NAME, True) > max_width and len(fitted) > 4:
        fitted = fitted[:-2] + ELLIPSIS
    return fitted


def render_card(repo: Repo, theme: str, desc_lines: int) -> str:
    colors = THEMES[theme]
    height = card_height(desc_lines)
    root = svg_root(CARD_W, height)
    frame(root, CARD_W, height, colors)
    svg(root, "path", transform=f"translate({PAD},{PAD + 2})", fill=colors["muted"], d=ICON_REPO)

    name_x = PAD + ICON_SIZE + ROW_GAP
    label_w = text_width(LABEL_TEXT, FONT_SIZE_TEXT) + 2 * LABEL_PAD_X
    name = fit_name(repo.name, CARD_W - name_x - PAD - label_w - ROW_GAP)
    svg(
        root,
        "text",
        name,
        x=name_x,
        y=PAD + 15,
        font_size=FONT_SIZE_NAME,
        font_weight=600,
        fill=colors["link"],
    )

    label_x = name_x + text_width(name, FONT_SIZE_NAME, True) + ROW_GAP
    svg(
        root,
        "rect",
        x=f"{label_x:.1f}",
        y=PAD + 1,
        width=f"{label_w:.1f}",
        height=LABEL_H,
        rx=LABEL_H / 2,
        fill="none",
        stroke=colors["border"],
    )
    svg(
        root,
        "text",
        LABEL_TEXT,
        x=f"{label_x + label_w / 2:.1f}",
        y=PAD + 14,
        font_size=FONT_SIZE_TEXT,
        font_weight=500,
        text_anchor="middle",
        fill=colors["muted"],
    )

    desc_y = PAD + NAME_ROW_H + ROW_GAP + 13
    for i, line in enumerate(wrap(repo.description, DESC_CHARS_PER_LINE, desc_lines)):
        svg(
            root,
            "text",
            line,
            x=PAD,
            y=desc_y + i * DESC_LINE_H,
            font_size=FONT_SIZE_TEXT,
            fill=colors["muted"],
        )

    render_footer(root, repo, colors, height - PAD - 4)
    return ET.tostring(root, encoding="unicode")


def render_footer(root: ET.Element, repo: Repo, colors: dict[str, str], baseline: int) -> None:
    x = float(PAD)
    if repo.language:
        svg(
            root,
            "circle",
            cx=x + LANGUAGE_DOT_R,
            cy=baseline - 4,
            r=LANGUAGE_DOT_R,
            fill=LANGUAGE_COLORS.get(repo.language, DEFAULT_LANGUAGE_COLOR),
            stroke=LANGUAGE_DOT_STROKE,
        )
        svg(
            root,
            "text",
            repo.language,
            x=x + 2 * LANGUAGE_DOT_R + 4,
            y=baseline,
            font_size=FONT_SIZE_TEXT,
            fill=colors["muted"],
        )
        x += 2 * LANGUAGE_DOT_R + 4 + text_width(repo.language, FONT_SIZE_TEXT) + FOOTER_ITEM_GAP

    for icon, count in ((ICON_STAR, repo.stars), (ICON_FORK, repo.forks)):
        if count == 0:
            continue
        svg(
            root,
            "path",
            transform=f"translate({x:.1f},{baseline - 12})",
            fill=colors["muted"],
            d=icon,
        )
        svg(
            root,
            "text",
            f"{count:,}",
            x=f"{x + ICON_SIZE + 4:.1f}",
            y=baseline,
            font_size=FONT_SIZE_TEXT,
            fill=colors["muted"],
        )
        x += ICON_SIZE + 4 + text_width(f"{count:,}", FONT_SIZE_TEXT) + FOOTER_ITEM_GAP


def write_cards(repos: list[Repo]) -> None:
    CARDS_DIR.mkdir(exist_ok=True)
    desc_lines = max(
        (len(wrap(r.description, DESC_CHARS_PER_LINE, DESC_MAX_LINES)) for r in repos),
        default=1,
    )
    wanted: set[str] = set()
    for repo in repos:
        for theme in THEMES:
            path = CARDS_DIR / f"{repo.name}-{theme}.svg"
            path.write_text(render_card(repo, theme, desc_lines), encoding="utf-8")
            wanted.add(path.name)
    removed = []
    for stale in CARDS_DIR.glob("*.svg"):
        if stale.name in wanted:
            continue
        stale.unlink()
        removed.append(stale.name)
    logger.info(
        "cards written",
        extra={"cards": len(wanted), "removed": removed, "desc_lines": desc_lines},
    )


def picture(name: str, folder: str, alt: str, width: str, href: str | None = None) -> str:
    """A <picture> that GitHub swaps between the light and dark SVG by viewer theme."""
    element = ET.Element("picture")
    ET.SubElement(
        element, "source", {"media": DARK_MEDIA, "srcset": f"{folder}/{name}-{THEME_DARK}.svg"}
    )
    ET.SubElement(
        element,
        "img",
        {"alt": alt, "src": f"{folder}/{name}-{THEME_LIGHT}.svg", "width": width},
    )
    if href is not None:
        link = ET.Element("a", {"href": href})
        link.append(element)
        element = link
    return ET.tostring(element, encoding="unicode", method="html")


def count_noun(count: int, noun: str) -> str:
    return f"{count:,} {noun}" if count == 1 else f"{count:,} {noun}s"


def card_alt(repo: Repo) -> str:
    """Everything the card draws, as text, for screen readers and text-only agents."""
    facts = [repo.language] if repo.language else []
    facts += [count_noun(repo.stars, "star"), count_noun(repo.forks, "fork")]
    if not repo.description:
        return f"{repo.name} ({', '.join(facts)})"
    return f"{repo.name}: {repo.description.strip()} ({', '.join(facts)})"


def repos_section(repos: list[Repo]) -> str:
    cards = [picture(r.name, "cards", card_alt(r), CARD_IMG_WIDTH, r.url) for r in repos]
    return "\n".join(" ".join(cards[i : i + 2]) for i in range(0, len(cards), 2))


def replace_between(text: str, start: str, end: str, body: str) -> str:
    block = f"{start}\n{body}\n{end}"
    if start in text and end in text:
        pattern = re.escape(start) + r".*?" + re.escape(end)
        return re.sub(pattern, lambda _: block, text, flags=re.S)
    return f"{text.rstrip()}\n\n{block}\n"


def write_readme(repos: list[Repo]) -> None:
    text = README.read_text(encoding="utf-8") if README.exists() else ""
    text = replace_between(text, REPOS_START, REPOS_END, repos_section(repos))
    README.write_text(text.lstrip(), encoding="utf-8")
    logger.info("readme written", extra={"path": str(README.relative_to(ROOT))})


def write_preview(user: str, repos: list[Repo]) -> None:
    """Frame the cards the way the profile README box does, in both themes."""
    page = ET.Element("html")
    head = ET.SubElement(page, "head")
    ET.SubElement(head, "meta", {"charset": "utf-8"})
    ET.SubElement(head, "title").text = "Profile preview"
    body = ET.SubElement(page, "body", {"style": "margin:0;display:flex;flex-wrap:wrap"})
    for theme, colors in THEMES.items():
        column = ET.SubElement(
            body,
            "div",
            {"style": f"background:{colors['bg']};padding:24px;flex:1;min-width:0"},
        )
        box = ET.SubElement(
            column,
            "div",
            {"style": f"border:1px solid {colors['border']};border-radius:6px"},
        )
        header = ET.SubElement(
            box,
            "div",
            {
                "style": f"padding:8px 16px;font:12px {FONT};color:{colors['muted']};"
                f"border-bottom:1px solid {colors['border']}"
            },
        )
        header.text = f"{user} / README.md"
        content = ET.SubElement(box, "div", {"style": "padding:24px"})
        for repo in repos:
            ET.SubElement(
                content,
                "img",
                {"src": f"cards/{repo.name}-{theme}.svg", "style": "width:49%;margin:0 0.5% 4px 0"},
            )
    html_text = "<!doctype html>" + ET.tostring(page, encoding="unicode", method="html")
    PREVIEW.write_text(html_text, encoding="utf-8")
    logger.info("preview written", extra={"path": str(PREVIEW.relative_to(ROOT))})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preview", action="store_true", help="also write preview.html")
    args = parser.parse_args()
    configure_logging()

    user = os.environ.get(ENV_USER, DEFAULT_USER)
    logger.info("update started", extra={"user": user, "authenticated": ENV_TOKEN in os.environ})

    repos = fetch_repos(user)
    top = top_repos(repos)
    write_cards(top)
    write_readme(top)
    if args.preview:
        write_preview(user, top)
    logger.info("update finished", extra={"top": len(top)})


if __name__ == "__main__":
    main()
