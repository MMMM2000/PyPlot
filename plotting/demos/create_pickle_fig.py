#!/usr/bin/env python3
"""
create_pickle_fig.py

Build a simple sine‐wave plot and pickle the Figure object
to disk so you can re-open and tweak it later.
"""
import pickle
import numpy as np
import matplotlib.pyplot as plt

def main():
    # 1) Build the figure
    x = np.linspace(0, 10, 100)
    y = np.sin(x)

    fig, ax = plt.subplots()
    ax.plot(x, y, label="sin(x)")
    ax.set_title("Original Title")
    ax.set_xlabel("X axis")
    ax.set_ylabel("Y axis")
    ax.legend()
    
    # Show it once
    plt.show()

    # 2) Pickle to file
    with open("my_plot.pkl", "wb") as f:
        pickle.dump(fig, f)
    print("Figure pickled to my_plot.pkl")

if __name__ == "__main__":
    main()