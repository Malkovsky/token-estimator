"""Small, dependency-free SVG badges for repository token summaries."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import Literal


BadgeStyle = Literal[
    "blueprint", "classic", "outline", "capsule", "terminal",
    "paper", "signal", "mono", "soft", "minimal",
]
BADGE_STYLES: tuple[BadgeStyle, ...] = (
    "blueprint", "classic", "outline", "capsule", "terminal",
    "paper", "signal", "mono", "soft", "minimal",
)


@dataclass(frozen=True)
class BadgeVisual:
    label_fill: str
    value_fill: str
    label_text: str
    value_text: str
    icon_stroke: str
    icon_fill: str | None = None
    dash_stroke: str | None = None
    height: int = 20
    radius: int = 2
    font: str = "Verdana,DejaVu Sans,sans-serif"
    border: str | None = None
    icon: bool = True
    shine: bool = False
    divider: bool = False


_VISUALS: dict[BadgeStyle, BadgeVisual] = {
    "blueprint": BadgeVisual(
        "#0d376b", "#145eb5", "#fff", "#fff", "#8fc1ff",
        icon_fill="#082b55", dash_stroke="#ffcc55", shine=True,
    ),
    "classic": BadgeVisual(
        "#555", "#007ec6", "#fff", "#fff", "#fff", radius=3,
        icon=False, shine=True,
    ),
    "outline": BadgeVisual(
        "#fff", "#fff", "#184f8d", "#0d58b5", "#145eb5", radius=3,
        icon_fill="#e8f1fb", dash_stroke="#ffcc55",
        border="#8bb0dc", divider=True,
    ),
    "capsule": BadgeVisual(
        "#0d376b", "#ffcc55", "#fff", "#342800", "#8fc1ff",
        icon_fill="#082b55", dash_stroke="#ffcc55",
        height=24, radius=12, shine=True,
    ),
    "terminal": BadgeVisual(
        "#101813", "#101813", "#a9b8ad", "#8ff0b6", "#72e6a2",
        icon_fill="#1c2a21", height=22, radius=2,
        font="ui-monospace,SFMono-Regular,Consolas,monospace",
        border="#33453a", divider=True,
    ),
    "paper": BadgeVisual(
        "#eee4d1", "#6b4f3d", "#563c2e", "#fff8ec", "#8c4d32",
        icon_fill="#ddceb4", height=24, radius=0,
        font="Georgia,DejaVu Serif,serif", border="#bba98f",
    ),
    "signal": BadgeVisual(
        "#111620", "#c8ff45", "#f4f6f8", "#10150a", "#ff7948",
        icon_fill="#262d3a", height=22, radius=5, border="#394254",
    ),
    "mono": BadgeVisual(
        "#202124", "#46484b", "#fff", "#fff", "#b8bcc2",
        icon_fill="#0f1011", radius=0,
    ),
    "soft": BadgeVisual(
        "#e7f0fc", "#d6e8ff", "#315f96", "#0d58b5", "#5588c6",
        icon_fill="#d4e5f8", dash_stroke="#ffcc55",
        height=24, radius=7, border="#b0c8e8",
    ),
    "minimal": BadgeVisual(
        "#fff", "#fff", "#526579", "#0d58b5", "#526579", radius=2,
        border="#ced9e7", icon=False, divider=True,
    ),
}


def compact_tokens(tokens: int) -> str:
    """Format a non-negative token count for a compact README badge."""
    if tokens < 0:
        raise ValueError("token count must not be negative")
    if tokens < 1_000:
        return str(tokens)
    if tokens < 1_000_000:
        value = tokens / 1_000
        return f"{value:.1f}k" if value < 10 else f"{value:.0f}k"
    value = tokens / 1_000_000
    return f"{value:.1f}M" if value < 10 else f"{value:.0f}M"


def token_badge_svg(
    tokens: int | None = None,
    label: str = "total tokens",
    style: BadgeStyle = "blueprint",
) -> str:
    """Return a styled token badge, or an unavailable badge on failure."""
    visual = _VISUALS[style]
    value = compact_tokens(tokens) if tokens is not None else "unavailable"
    label_padding = 10 if label == "metadata tokens" else 0
    icon_width = 32 if visual.icon else 0
    label_width = max(88, 18 + icon_width + len(label) * 6 + label_padding)
    value_width = max(48, 14 + len(value) * 7)
    width = label_width + value_width
    label_center = icon_width + (label_width - icon_width) / 2
    value_center = label_width + value_width / 2
    baseline = visual.height / 2 + 4
    accessible = escape(f"{label}: {value}", quote=True)
    value_fill = visual.value_fill if tokens is not None else "#a33a3a"
    value_text = visual.value_text if tokens is not None else "#fff"
    shine = (
        f'<rect width="{width}" height="{visual.height}" fill="url(#s)"/>'
        if visual.shine else ""
    )
    border = (
        f'<rect x=".5" y=".5" width="{width - 1}" height="{visual.height - 1}" '
        f'rx="{max(0, visual.radius - 1)}" fill="none" stroke="{visual.border}"/>'
        if visual.border else ""
    )
    divider = (
        f'<path d="M{label_width} 1v{visual.height - 2}" stroke="{visual.border or "#fff"}" '
        f'stroke-opacity=".55"/>' if visual.divider else ""
    )
    icon_panel = (
        f'<rect width="{icon_width}" height="{visual.height}" fill="{visual.icon_fill}"/>'
        if visual.icon and visual.icon_fill else ""
    )
    icon = (
        f'<g transform="translate(0 {(visual.height - 20) / 2:g})">'
        f'<path d="M9 5H6v10h3M23 5h3v10h-3" fill="none" '
        f'stroke="{visual.icon_stroke}" stroke-width="1"/>'
        f'<path d="M12 7.5h8M12 10h8M12 12.5h8" fill="none" '
        f'stroke="{visual.dash_stroke or visual.icon_stroke}" stroke-width="1"/></g>'
        if visual.icon else ""
    )
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{visual.height}" role="img" aria-label="{accessible}">
  <title>{accessible}</title>
  <linearGradient id="s" x2="0" y2="100%">
    <stop offset="0" stop-color="#fff" stop-opacity=".14"/>
    <stop offset="1" stop-opacity=".09"/>
  </linearGradient>
  <clipPath id="r"><rect width="{width}" height="{visual.height}" rx="{visual.radius}"/></clipPath>
  <g clip-path="url(#r)">
    <rect width="{label_width}" height="{visual.height}" fill="{visual.label_fill}"/>
    <rect x="{label_width}" width="{value_width}" height="{visual.height}" fill="{value_fill}"/>
    {icon_panel}{shine}{divider}{icon}
  </g>
  {border}
  <g text-anchor="middle" font-family="{visual.font}" font-size="11">
    <text x="{label_center:g}" y="{baseline:g}" fill="{visual.label_text}">{escape(label)}</text>
    <text x="{value_center:g}" y="{baseline:g}" fill="{value_text}" font-weight="700">{escape(value)}</text>
  </g>
</svg>'''


def token_summary_badge_svg(
    metadata_tokens: int | None = None,
    total_tokens: int | None = None,
    style: BadgeStyle = "blueprint",
) -> str:
    """Return a three-segment metadata/total token summary badge."""
    visual = _VISUALS[style]
    metadata = (
        compact_tokens(metadata_tokens) if metadata_tokens is not None else "unavailable"
    )
    total = compact_tokens(total_tokens) if total_tokens is not None else "unavailable"
    icon_width = 32 if visual.icon else 0
    label_width = max(78, 18 + icon_width + len("tokens") * 6)
    metadata_width = max(48, 14 + len(metadata) * 7)
    total_width = max(48, 14 + len(total) * 7)
    total_start = label_width + metadata_width
    width = total_start + total_width
    label_center = icon_width + (label_width - icon_width) / 2
    metadata_center = label_width + metadata_width / 2
    total_center = total_start + total_width / 2
    baseline = visual.height / 2 + 4
    accessible = escape(
        f"tokens: metadata {metadata}, total {total}", quote=True
    )
    metadata_fill = visual.value_fill if metadata_tokens is not None else "#a33a3a"
    metadata_text = visual.value_text if metadata_tokens is not None else "#fff"
    total_fill = visual.label_fill if total_tokens is not None else "#a33a3a"
    total_text = visual.label_text if total_tokens is not None else "#fff"
    shine = (
        f'<rect width="{width}" height="{visual.height}" fill="url(#s)"/>'
        if visual.shine else ""
    )
    border = (
        f'<rect x=".5" y=".5" width="{width - 1}" height="{visual.height - 1}" '
        f'rx="{max(0, visual.radius - 1)}" fill="none" stroke="{visual.border}"/>'
        if visual.border else ""
    )
    divider_stroke = visual.border or "#fff"
    dividers = (
        f'<path d="M{label_width} 1v{visual.height - 2}M{total_start} 1v{visual.height - 2}" '
        f'stroke="{divider_stroke}" stroke-opacity=".45"/>'
    )
    icon_panel = (
        f'<rect width="{icon_width}" height="{visual.height}" fill="{visual.icon_fill}"/>'
        if visual.icon and visual.icon_fill else ""
    )
    icon = (
        f'<g transform="translate(0 {(visual.height - 20) / 2:g})">'
        f'<path d="M9 5H6v10h3M23 5h3v10h-3" fill="none" '
        f'stroke="{visual.icon_stroke}" stroke-width="1"/>'
        f'<path d="M12 7.5h8M12 10h8M12 12.5h8" fill="none" '
        f'stroke="{visual.dash_stroke or visual.icon_stroke}" stroke-width="1"/></g>'
        if visual.icon else ""
    )
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{visual.height}" role="img" aria-label="{accessible}">
  <title>{accessible}</title>
  <linearGradient id="s" x2="0" y2="100%">
    <stop offset="0" stop-color="#fff" stop-opacity=".14"/>
    <stop offset="1" stop-opacity=".09"/>
  </linearGradient>
  <clipPath id="r"><rect width="{width}" height="{visual.height}" rx="{visual.radius}"/></clipPath>
  <g clip-path="url(#r)">
    <rect width="{label_width}" height="{visual.height}" fill="{visual.label_fill}"/>
    <rect x="{label_width}" width="{metadata_width}" height="{visual.height}" fill="{metadata_fill}"/>
    <rect x="{total_start}" width="{total_width}" height="{visual.height}" fill="{total_fill}"/>
    {icon_panel}{shine}{dividers}{icon}
  </g>
  {border}
  <g text-anchor="middle" font-family="{visual.font}" font-size="11">
    <text x="{label_center:g}" y="{baseline:g}" fill="{visual.label_text}">tokens</text>
    <text x="{metadata_center:g}" y="{baseline:g}" fill="{metadata_text}" font-weight="700">{escape(metadata)}</text>
    <text x="{total_center:g}" y="{baseline:g}" fill="{total_text}" font-weight="700">{escape(total)}</text>
  </g>
</svg>'''
