#!/usr/bin/env python3
"""
interactive_matplotlib.py

Demonstrate using Matplotlib's interactive mode.
You can click on the toolbar to pan/zoom, or call
methods at the Python prompt to tweak the figure.
"""
import matplotlib
# Ensure a GUI backend; on macOS, 'TkAgg' usually works if you have Tk installed.
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
import numpy as np

def main():
    plt.ion()  # interactive mode on

    x = np.linspace(0, 10, 100)
    y = np.cos(x)

    fig, ax = plt.subplots()
    ax.plot(x, y, label="cos(x)")
    ax.set_title("Interactive Matplotlib")
    ax.set_xlabel("X")
    ax.set_ylabel("cos(x)")
    ax.legend()

    plt.show()

    # Drop into input() so the script doesn't exit immediately:
    print("Interactive figure open. You can now:")
    print("  - click the toolbar (Configure Subplots) to adjust margins")
    print("  - call ax.set_title('New') in this prompt (if you import ax)")
    input("Press Enter to close…")

if __name__ == "__main__":
    main()