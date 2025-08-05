#!/usr/bin/env python3
"""
plotly_interactive.py

Build an interactive Plotly sine plot, enable in-browser editing,
export to JSON+HTML, then demonstrate reloading from JSON.
"""
import numpy as np
import plotly.graph_objects as go
import plotly.io as pio

JSON_OUT = "plotly_plot.json"
HTML_OUT = "plotly_plot.html"

def build_figure():
    x = np.linspace(0, 10, 100)
    y = np.sin(x)
    fig = go.Figure(go.Scatter(x=x, y=y, mode="lines", name="sin(x)"))
    fig.update_layout(
        title="Editable Plotly Chart",
        xaxis_title="X axis",
        yaxis_title="sin(x)",
    )
    return fig

def main():
    fig = build_figure()

    # 1) Show in browser with editing turned on
    fig.show(config={"editable": True})

    # 2) Write JSON spec and standalone HTML
    pio.write_json(fig, JSON_OUT)
    fig.write_html(HTML_OUT, include_plotlyjs="cdn", config={"editable": True})
    print(f"Wrote {JSON_OUT} and {HTML_OUT}")

    # 3) (Optional) reload from JSON
    print("Reloading from JSON to verify…")
    fig2 = pio.read_json(JSON_OUT)
    fig2.show(config={"editable": True})

if __name__ == "__main__":
    main()