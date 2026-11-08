"""Shared viewer renderer with compatible current and historical imagery modes."""

import json
from pathlib import Path

from model_configuration import prepare_template


def render_viewer(*, imagery="current"):
    """Assemble imagery first, then apply the same model hooks for both entry points."""
    if imagery not in ("current", "historical-naip"):
        raise ValueError(
            f"Unsupported imagery mode: {imagery!r}; use current or historical-naip"
        )
    assets = Path(__file__).parent / "viewer_assets"
    html = (assets / "base.html").read_text(encoding="utf-8")
    fragments = json.loads((assets / "imagery.json").read_text(encoding="utf-8"))[
        imagery
    ]
    for name, fragment in fragments.items():
        marker = "@@IMAGERY_" + name.upper() + "@@"
        if html.count(marker) != 1:
            raise ValueError(
                f"Shared viewer template must contain exactly one {marker}"
            )
        html = html.replace(marker, fragment)
    if "@@IMAGERY_" in html:
        raise ValueError("Unresolved imagery fragment in shared viewer template")
    return prepare_template(html)
