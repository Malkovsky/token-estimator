"""Small, dependency-free SVG badges for repository token summaries."""

from __future__ import annotations

from html import escape


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


def token_badge_svg(tokens: int | None = None, label: str = "total tokens") -> str:
    """Return a Blueprint-styled badge, or an unavailable badge on failure."""
    value = compact_tokens(tokens) if tokens is not None else "unavailable"
    value_color = "#145eb5" if tokens is not None else "#a33a3a"
    label_padding = 10 if label == "metadata tokens" else 0
    label_width = max(100, 32 + len(label) * 6 + label_padding)
    value_width = max(48, 14 + len(value) * 7)
    width = label_width + value_width
    label_center = 32 + (label_width - 32) / 2
    value_center = label_width + value_width / 2
    accessible = escape(f"{label}: {value}", quote=True)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="20" role="img" aria-label="{accessible}">
  <title>{accessible}</title>
  <linearGradient id="s" x2="0" y2="100%">
    <stop offset="0" stop-color="#fff" stop-opacity=".12"/>
    <stop offset="1" stop-opacity=".08"/>
  </linearGradient>
  <clipPath id="r"><rect width="{width}" height="20" rx="2"/></clipPath>
  <g clip-path="url(#r)">
    <rect width="{label_width}" height="20" fill="#0d376b"/>
    <rect x="{label_width}" width="{value_width}" height="20" fill="{value_color}"/>
    <rect width="{width}" height="20" fill="url(#s)"/>
    <path d="M10 5H7v10h3M24 5h3v10h-3M13 7.5h8M13 10h8M13 12.5h8" fill="none" stroke="#8fc1ff" stroke-width="1"/>
  </g>
  <g fill="#fff" text-anchor="middle" font-family="Verdana,DejaVu Sans,sans-serif" font-size="11">
    <text x="{label_center}" y="14">{label}</text>
    <text x="{value_center}" y="14" font-weight="700">{escape(value)}</text>
  </g>
</svg>'''
