"""Simple command-line menu demo.

Run with:
    python ui_examples/cli_menu.py
"""

def main() -> None:
    options = ["Plot stress dependence", "Plot temperature sensitivity", "Exit"]
    print("CLI Demo: Select an option")
    for idx, opt in enumerate(options, 1):
        print(f"{idx}. {opt}")
    choice = input("Enter number: ")
    try:
        choice_idx = int(choice) - 1
    except ValueError:
        print("Invalid input")
        return
    if 0 <= choice_idx < len(options):
        print(f"You selected: {options[choice_idx]}")
    else:
        print("Selection out of range")


if __name__ == "__main__":
    main()

