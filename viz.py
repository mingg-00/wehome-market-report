#!/usr/bin/env python3
"""차트 PNG 렌더 공용 유틸. site.py 가 쓴다."""

from __future__ import annotations

import base64
import io

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.rcParams["font.family"] = "AppleGothic"
plt.rcParams["axes.unicode_minus"] = False

NAVY, MINT, GREY, RED, AMBER = "#1B2A4A", "#00C2A8", "#B9C2CE", "#E2574C", "#F0A93E"


def to_png(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=144, bbox_inches="tight", transparent=True)
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def strip_spines(ax, keep=()):
    for s in ("top", "right", "left", "bottom"):
        if s not in keep:
            ax.spines[s].set_visible(False)
