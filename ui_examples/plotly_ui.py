"""Small Plotly UI demo.

Creates an interactive sine wave plot with a range slider.
Run with:
    python ui_examples/plotly_ui.py
"""

import numpy as np
import plotly.graph_objects as go

def main() -> None:
    x = np.linspace(0, 10, 500)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=x, y=np.sin(x), name="sin(x)"))
    fig.update_layout(title="Plotly Sine Wave", xaxis=dict(rangeslider=dict(visible=True)))
    fig.show()

if __name__ == "__main__":
    main()

