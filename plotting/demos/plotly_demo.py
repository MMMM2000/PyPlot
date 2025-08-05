"""Demonstrate an interactive Plotly graph.

Running this script creates a small sine wave plot
that can be edited directly in the browser.  The figure
is also saved as ``plotly_demo.html`` so it can be
reopened and modified later.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import plotly.graph_objects as go


def build_figure() -> go.Figure:
    """Return a basic sine wave figure."""
    x = np.linspace(0, 10, 100)
    y = np.sin(x)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=x, y=y, mode="lines", name="sin(x)"))
    fig.update_layout(
        title="Interactive Plotly Demo",
        xaxis_title="X value",
        yaxis_title="sin(x)",
    )
    return fig


def main(output: Path = Path("plotly_demo.html")) -> None:
    """Create the demo figure and display it.

    The figure is shown with ``editable=True`` to allow live
    edits in the browser window.  It is also written to *output*
    so that the interactive plot can be reopened later.
    """

    fig = build_figure()
    fig.show(config={"editable": True})
    fig.write_html(output, include_plotlyjs="cdn")


if __name__ == "__main__":
    main()
