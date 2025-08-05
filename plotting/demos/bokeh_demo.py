#!/usr/bin/env python3
"""
bokeh_demo.py

A tiny Bokeh app: a sine wave whose title and line color
are bound to widget controls.
Launch with:
    bokeh serve --show bokeh_demo.py
"""
import numpy as np
from bokeh.io import curdoc
from bokeh.plotting import figure
from bokeh.layouts import column, row
from bokeh.models import TextInput, ColorPicker

# Data
x = np.linspace(0, 10, 200)
y = np.sin(x)

# Figure
p = figure(title="Bokeh Sine Wave", width=600, height=350)
line = p.line(x, y, line_width=3, color="#1f77b4")

# Widgets
title_input = TextInput(value=p.title.text, title="Plot title:")
color_picker = ColorPicker(color=line.glyph.line_color, title="Line color")

# Callbacks
def update_title(attr, old, new):
    p.title.text = new

def update_color(attr, old, new):
    line.glyph.line_color = new

title_input.on_change("value", update_title)
color_picker.on_change("color", update_color)

# Layout
controls = column(title_input, color_picker, width=250)
layout = row(controls, p, sizing_mode="stretch_width")
curdoc().add_root(layout)
curdoc().title = "Interactive Bokeh Demo"