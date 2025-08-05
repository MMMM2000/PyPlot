#!/usr/bin/env python3
"""
load_and_modify_pickle_fig.py

Load the pickled Figure, tweak title/labels/legend,
and display the updated plot.
"""
import pickle
import matplotlib.pyplot as plt

def main():
    # 1) Load the pickled Figure
    with open("my_plot.pkl", "rb") as f:
        fig = pickle.load(f)

    # 2) Modify the first Axes
    ax = fig.axes[0]
    ax.set_title("🎉 Updated Title")
    ax.set_xlabel("New X label")
    ax.set_ylabel("New Y label")
    # Change legend text
    handles, labels = ax.get_legend_handles_labels()
    ax.legend(handles, ["Updated Legend"])

    # 3) Show the modified figure
    plt.show()
    input("Press Enter to exit...")  # keep window open

if __name__ == "__main__":
    main()