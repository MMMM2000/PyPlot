# Liquid Glass UI Experiments

Apple's iOS/macOS 26 aesthetic blends frosted translucency with vibrant accent
colors and depth. The notes below capture ideas for translating that “liquid
glass” style into future iterations of the microwire tools.

## Demo implementation

* `experiments/liquid_glass_gui.py` now wraps the PyVISA current annealing logger
  in a frosted card so behaviour matches the production window while the chrome
  shifts to liquid glass.
* Buttons in the demo open the classic PyVISA and serial loggers for side-by-side
  comparison. Use them to judge whether the translucent treatment improves
  clarity before introducing a theme toggle to the main tools.

## Palette and Materials

* **Layered translucency** – wrap primary panels in semi-opaque glass with
  backdrop blurs. Use light and dark variants that sample the system accent so
  dialogs inherit their tint from the OS.
* **Sheen gradients** – introduce gentle vertical gradients on headers and
  buttons to suggest light passing through curved glass.
* **Highlight inks** – promote contextual actions with saturated accent pills
  floating above the glass surface. Outline destructive actions with thin
  vibrant borders instead of flat fills.

## Layout Concepts

* **Floating islands** – separate file pickers, option grids, and console panes
  into individual glass cards hovering above a softly textured backdrop. Keep
  spacing generous to accentuate the layering.
* **Depth through motion** – animate cards with subtle parallax when dialogs
  open or when users toggle between configuration groups. Keep motion slow and
  easing gentle to reinforce the fluid feel.
* **Rounded hierarchy** – adopt large corner radii (18–24 px) for top-level
  cards and slightly tighter radii (12–16 px) for nested elements. Maintain
  consistent corner logic so the stack reads as poured glass.

## Typography and Iconography

* **Dynamic weights** – pair SF Pro or Segoe UI Variable with weight/optical size
  adjustments to retain legibility against translucent backgrounds.
* **Glyph silhouettes** – switch to simple duotone glyphs with inner shadows to
  mimic backlit icons etched into glass.
* **Animated status dots** – replace plain status text with glowing indicator
  dots that pulse softly when background tasks (logging, exporting) run.

## Interaction Touches

* **Contextual morphing** – when expanding advanced settings, morph the glass
  card instead of popping a new dialog. Corners should stretch as if the card is
  made of viscous liquid.
* **Haptic-inspired sound cues** – pair key actions (start/stop logging, origin
  export) with short, soft chimes reminiscent of iOS system sounds.
* **Focus halos** – display thin light rays around focused widgets to replicate
  the light bloom Apple uses when glass catches a highlight.

These concepts can be prototyped in a sandbox Qt window using QML or
Qt Quick Controls to explore blur and vibrancy effects without impacting the
main application. Once the palette and card hierarchy feel polished, portions of
this aesthetic can trickle into the production dialogs behind a "Liquid Glass"
appearance toggle.
