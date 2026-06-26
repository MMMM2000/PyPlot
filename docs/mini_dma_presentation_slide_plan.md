# TMA Presentation Slide Plan

Prepared as a planning artifact before editing `mini_dma_presentation.pptx`.

Target presentation date: Thursday, 2026-05-21.

Source deck:

- `C:\Users\Martin\OneDrive - Technicka univerzita v Kosiciach\1 Projects\PhD\mini_dma_presentation.pptx`

Supporting repo context:

- `docs/mini_dma_presentation_context.md`
- `docs/mini_dma_hardware.md`
- `docs/mini_dma_speed_control.md`
- `docs/mini_dma_measurement_plan.md`

Current generated deck:

- `docs/assets/presentations/mini_dma_colloquium_2026-05-21_revised.pptx`
- Branch used on 2026-05-21: `codex/mini-dma-presentation-story`
- Last known synced commit on the Mac: `d91b1ec Refine presentation slides and comparison visuals`
- Current Windows repair: restored the MacBook deck as the main PPTX, removed the old comparison slide 5, fixed distorted picture aspect ratios, replotted key graphs with PyPlot logic, and replaced the next-step hardware photo with a thermal-camera frame.

Source-folder context reviewed from:

- `Prezentacia Praha 1. mesiac/prezentacia.pptx`
- `Prezentacia Praha 1. mesiac/notes.pdf`
- `Prezentacia Praha 1. mesiac/outline.docx`
- `Prezentacia Praha 1. mesiac/Varga_SMM27.pptx`
- `SAIA - Praha 2025/OdbornyProgram_MartinElias2025.docx`
- `SAIA - Praha 2025/Martin Elias_Polrocna sprava.docx`
- `SAIA - Praha 2025/MotivacnyList_MartinElias2025.docx`

## Story Thesis

Commercial DMA is a precise and valuable way to identify transition behavior and strain response, but it is slow, resource-intensive, and not ideal when many shape-memory microwire samples need fast iteration. TMA is a focused, low-cost, feedback-controlled instrument built for the specific experiment we need: controlled tensile load, strain/stress calculation, electrical heating, synchronized logging, and enough audit data to understand every run.

The presentation should move from microwire motivation to measurement bottleneck, then from purpose-built solution to first evidence, and finally to the broader point: AI-assisted engineering made it realistic for one researcher to build an instrument that would previously have needed a larger team and more time.

## Current Revised Deck, 2026-05-21

The current authoritative PPTX is
`docs/assets/presentations/mini_dma_colloquium_2026-05-21_revised.pptx`.
It is the MacBook-built deck from commit `d91b1ec`, repaired on Windows into a
14-slide version: real photos and plots keep their original aspect ratio, the
manual stress-strain graph uses PyPlot cycle coloring, the first TMA result
uses the PyPlot power top axis, and the next-step slide uses the thermal-camera
frame.

The 11-slide outline in `Preferred 15-Minute Flow After Programme Update` is a
possible compression guide, not the active PPTX.

Global decisions from the final review:

- Use simple, normal slide titles. Avoid poetic phrasing such as `two speeds instead of one bottleneck`.
- Keep text large and sparse. The audience should not need to read dense paragraphs.
- Include page numbers.
- Do not describe the TMA as a replacement for commercial DMA. It is a cheap screening and protocol-development tool before final validation.
- Do not say `validation remains experimental` in the closing. The final slide should hype the practical Codex/AI-assisted instrument-development story.
- Do not use a made-up thermocamera plot. Only use the real thermal-camera frame if it is found; otherwise keep the thermal-camera slide as a next-step slide using real hardware.
- Slide 3 should not mention `EDA`, because that label is not important for the talk. It should visually communicate that many preparation, geometry, temperature, stress, and strain parameters are being compared.
- The cost comparison should make the approximate order-of-magnitude contrast obvious: `300â‚¬` versus `30000â‚¬`. The speaker can say "approximately" out loud.

### Slide 1 - TMA for Microwire Screening

Purpose:

- Open with the real setup photo and the actual theme: fast iso-stress measurements before commercial DMA validation.

Talk out loud:

- "Limpat has already introduced the microwires themselves, so I will focus on the measurement workflow."
- "The question is how to prepare good wires reproducibly and measure enough of them quickly enough to learn the preparation rules."

### Slide 2 - The Goal Is Better Preparation, Not One Perfect Plot

Visible logic:

- `Many samples`
- `Fast feedback`
- `Selected validation`

Talk out loud:

- "The goal is not one beautiful graph. The goal is learning which preparation choices produce useful wires."
- "Commercial DMA is the reference step, but not the first step for every sample."

### Slide 3 - Many Variables Can Be Compared At Once

Current visual:

- Dense parameter-to-parameter correlation heatmap from the microwire dataset.
- It is intentionally not meant to be fully readable in the room. Its job is to show the large number of parameters and relationships being compared.
- Avoid the term `EDA` on the slide and in the spoken explanation unless someone asks.

Talk out loud:

- "Our broader goal is to connect preparation data with experimental results."
- "We want to understand how to prepare wires with high strain that can also survive high stress."
- "This means comparing many variables with each other: preparation conditions, geometry, transformation indicators, stress, strain, and failure-related values."
- "I will not go into this graph in detail; it is here to show the scale of the correlation problem."

### Slide 4 - Manual Stress-Strain Measurements Were Useful But Slow

Current visual:

- Manual stress-strain measurement for `Ni50Fe27Ga23 5_4, 50 mA`.
- The 50 mA version is preferred because the 87 mA measurement was too high-current: the wire stayed in austenite and did not clearly transform back to martensite.

Talk out loud:

- "Before the TMA setup, I did some stress-strain measurements manually."
- "They were useful, but slow and annoying. One current value could take about half an hour of manual work."
- "Manual stress-strain is a snapshot at one current. For screening transformation behavior, iso-stress sweeps are a better first measurement."

### Slide 5 - Iso-Stress Sweeps Match The Question Better

Purpose:

- Explain why the new setup is not just motorized stress-strain measurement.

Talk out loud:

- "Manual stress-strain is not wrong; it answers a different question."
- "For screening, I want to hold the stress target and see how the wire contracts as current changes."
- "That gives a more direct view of transformation behavior under controlled stress."

### Slide 6 - Commercial DMA Is The Precise Reference Measurement

Current visual:

- Commercial DMA graph.

Talk out loud:

- "Commercial DMA is still the precise reference tool."
- "It is good for selected samples and final validation."
- "The bottleneck is using it as the first filter for every preparation variant."

### Slide 7 - Use TMA Before Commercial DMA

Visible logic:

- TMA screening -> promising wires -> DMA validation.

Talk out loud:

- "The TMA is a decision tool."
- "It helps decide which wires deserve slow, careful commercial DMA time."

### Slide 8 - TMA Hardware

Current visual:

- Stepper motor / linear motion.
- Scale.
- Control software.

Important hardware points:

- Smallest motor step: about `10 Âµm`.
- Scale precision: `0.05 g` for this presentation framing.
- Scale latency: about `200 ms`.
- Power supply provides current heating.
- Load is measured using a `20 g` weight on the scale: lifting part of the weight corresponds to load applied to the wire.

Talk out loud:

- "The motor provides displacement, the scale measures load, and the power supply heats the wire by current."
- "The hardware is simple; the important part is making the pieces work together as one controlled experiment."

### Slide 9 - How The Iso-Stress Measurement Works

Current visual:

- Software screenshot / photo.
- Step sequence for target stress, current heating, wire contraction, and motor correction.

Talk out loud:

- "I set a target stress, similar to an iso-stress DMA measurement."
- "As current increases, the wire heats and contracts during transformation."
- "That increases the stress, so the motor moves down to bring the stress back to the target."
- "Because we know how much the motor moved, we can calculate strain."
- "During cooling, the wire elongates again and the motor moves in the opposite direction to hold the same stress."

### Slide 10 - The Run Starts By Defining The Real Zero Length

Current visual:

- Baseline and initial-length plot.

Talk out loud:

- "The setup measures displacement, not absolute wire length."
- "At the start, it first moves to a known stress target, for example 20 MPa, and asks me to enter the measured wire length."
- "Then it moves back toward zero load."
- "The wire can still pull slightly even when it is not fully straight, so the software estimates the zero-load baseline from where load stops following the linear displacement trend."
- "Since we know the displacement from the measured target state to this baseline, we get the effective initial length."

### Slide 11 - The Motor Approaches The Target In Two Modes

Current visual:

- Target approach plot.

Talk out loud:

- "To be precise but not painfully slow, the speed changes dynamically."
- "Far from the target, it takes larger steps and can use a continuous cruise mode."
- "Because the scale value is delayed by about 200 ms, the controller checks retrospectively whether it is approaching correctly."
- "Near the target, it switches to gated corrections: move, wait for a fresh scale value, then move again."
- "Near the final target it uses single 10 micrometer steps."

### Slide 12 - First TMA Current Sweeps

Current visual:

- Real TMA current sweep with only the lower stress curves, `50-200 MPa`, to avoid clutter.

Talk out loud:

- "This is a real measurement from the setup."
- "The high-stress curves were removed on this slide only to keep it readable."
- "Because current is measured, we can also estimate the power needed to reach a chosen contraction, for example 10 percent, which matters for practical actuator applications."

### Slide 13 - TMA Vs Commercial DMA

Current visual:

- TMA graph and commercial DMA graph side by side.
- Large price contrast underneath: `300â‚¬` and `30000â‚¬`.

Talk out loud:

- "These prices are approximate, but the order of magnitude is the point."
- "Commercial DMA is still the better validation instrument."
- "A few-hundred-euro screening setup changes which experiments are practical to try."

### Slide 14 - Next Step: Add Temperature To The Same Run

Current visual:

- Real thermal-camera hardware photo, not a synthetic thermal trace.

Important missing asset:

- The user remembers an actual thermocamera frame from an older presentation, showing a heated wire. It was not found locally in the repo during the 2026-05-20 search.
- If that image is found, it should replace or supplement the current hardware-only thermal slide.

Talk out loud:

- "Right now we measure current and can calculate electrical input."
- "The next step is to add actual wire temperature during the same run."
- "That would let us connect current, power, strain, and temperature, and later also look at the elastocaloric effect during transformation."

### Slide 15 - Codex Made This A One-Person Prototype

Purpose:

- End with a positive AI-assisted instrument-development message.

Talk out loud:

- "This experiment was built largely with AI help."
- "ChatGPT helped choose the components and explained the wiring very concretely, even down to which cable goes to which pin."
- "After connecting the hardware to the PC, Codex wrote the software, installed the correct drivers, and helped get the setup running."
- "The whole prototype came together in about two weeks."
- "A few years ago I would probably need someone for electronics and someone else for software. Now, if someone has a small experimental idea, they can get surprisingly far by themselves."

## Immediate Continuation Notes For Windows Laptop

To continue from another machine:

```text
git fetch --all --prune
git switch codex/mini-dma-presentation-story
git pull --ff-only
```

Then open:

```text
docs/assets/presentations/mini_dma_colloquium_2026-05-21_revised.pptx
```

Final checks to do in PowerPoint:

- Verify no text is cropped or overlapping.
- Check slide 3 visually: it should communicate "many variables and correlations", not require reading.
- Check slide 13: the graph-plus-price comparison should remain clear at presentation scale.
- If the real thermocamera frame is found, replace the slide 14 hardware-only visual.

## Source-Folder Takeaways

Reusable microwire-intro content:

- From `prezentacia.pptx` / `notes.pdf`: simple explanation of glass-coated metallic microwires, geometry advantages, Taylor-Ulitovsky fabrication, internal stresses, Ni-Fe-Ga Heusler alloy tuning, and the research goal of large reversible strain with tunable transformation temperature.
- From `outline.docx`: concise speaking notes for the same intro, especially the explanation that current annealing is a quick screening method and DMA was planned for precise strain measurement.
- From `Varga_SMM27.pptx`: stronger authority points for why the material matters: shape anisotropy, one-direction strain, fast thermal response, simple production, Heusler phase transition, Ni-Mn-Ga as ideal but hard to fabricate as microwires, Ni-Fe-Ga as a practical route, repeatability as a central problem, reported large strain/cycling/strength examples, and pre-stress as a fine-tuning lever.

What to reuse directly:

- A microscope image of the metallic core plus glass coating.
- One visual or schematic of Taylor-Ulitovsky production.
- One compact "why microwires are interesting" slide.
- One sentence about Ni-Fe-Ga Heusler microwires: structural transformation can produce strain, and transformation temperature can be tuned by composition and pre-stress.

What not to import into the main talk:

- Long Heusler alloy taxonomy.
- Detailed Co-addition and Curie-temperature story.
- Multiple composition case studies.
- Dense fabrication-history slides.
- Full magnetic measurement context unless it directly supports why TMA is needed.

For a 15-minute TMA talk, the microwire introduction should be about 3 slides and 3 minutes total. Its job is to make the audience understand why the instrument matters, not to retell the whole microwire research program.

## Programme-Aware Revision

Programme source:

- `C:\Users\Martin\Downloads\Ni-Fe-Ga_colloquium_programme_corrected_2026-05-21.pdf`

Important programme context:

- Limpat Nulandaya speaks before this talk, 13:45-14:00, on `Ni-Fe-Ga microwires: current status and characterisation`.
- That slot already covers microwire preparation status, microstructure, martensitic transformation, and DMA.
- Martin's slot is 14:30-14:45, titled `Instrumentation developed for Ni-Fe-Ga research`.

Implication:

Do not spend 3-4 minutes introducing microwires unless Limpat's talk is cancelled or unexpectedly skips the basics. Use one short bridge sentence instead:

> "After Limpat's overview of the microwires themselves, I will focus on the measurement problem: how do we prepare good wires reproducibly, and how do we measure enough of them quickly enough to learn the preparation rules?"

The revised story should be: research goal -> measurement throughput problem -> DMA bottleneck -> purpose-built TMA setup -> current status -> next experiments.

## Prague / SAIA Project Context

The Prague stay was framed around experimental characterization of Ni-Fe-Ga-based glass-coated Heusler microwires and linking their transformation, magnetic, and mechanical properties to preparation parameters.

Useful framing points for this talk:

- There is a large set of microwires produced under different conditions, so the core problem is not one measurement, but building an efficient selection workflow.
- Target behavior: clear and stable martensitic transformation in an application-relevant range, strong recoverable strain, and reproducible preparation.
- The original project plan emphasized SEM/EDS, VSM, and DMA; the half-year report adds a practical screening hierarchy: Joule-heating resistance scans, fast manual/mechanical strain checks, magnetic/FMR characterization, and finally slow high-precision DMA.
- Quick screening is not a side task; it is part of the research strategy. Time-consuming detailed measurements should be focused on promising wires.
- This makes TMA fit naturally as an instrumentation answer: a bridge between very fast rough screening and slow commercial DMA validation.
- SAIA report results that can be mentioned if useful: manual screening found several wires with more than 10% strain under suitable current/stress, and commercial DMA measured about 16% recoverable strain for sample `11/1` under 40 MPa.

Power-axis implementation note, checked 2026-05-20:

- PR #262 has been merged into `main`, and this presentation branch has been fast-forwarded to include it.
- TMA logs electrical channels such as current, voltage, resistance, and power in the logger context.
- TMA PyPlot graphs now support optional top power axes for strain-current and resistance-current plots.
- The graph implementation calculates electrical power as `P = I^2R` in mW from plotted current and resistance values, then labels the top X axis with power while the bottom X axis remains measured current.
- Before editing the final PPTX, replot the TMA graphs with `Show power top axis` enabled so the result slide can show current on the bottom axis and calculated power on the top axis.

## Preferred 15-Minute Flow After Programme Update

Target: 11 slides, about 14.5 minutes spoken. This is now the preferred structure because the audience should already have microwire background from Limpat's talk.

Delivery style:

- Speak like you are explaining your own problem-solving path, not reading a paper abstract.
- Use Limpat's talk as a handoff: "you have already seen what these wires are; now I want to show why the measurement workflow became a problem for me."
- Keep the repeated thread simple: `to prepare good wires, we need many measurements; commercial DMA is excellent but slow; TMA is the practical bridge`.
- Do not over-defend the machine. Say clearly that it is a screening and development setup, and that commercial DMA remains the validation tool.
- The AI-assisted build story should sound like a closing reflection, not a sales pitch.

### Slide 1 - Handoff / Title

Time: 0:30

Visible slide text:

- `Instrumentation for Ni-Fe-Ga microwire research`
- `From preparation questions to faster measurement workflows`

Image / visual:

- TMA setup photo, ideally with the wire/load path visible.

Talk out loud:

- "Limpat has already introduced the microwires themselves, so I will not repeat that part."
- "I want to focus on the next problem, which is more practical: if we want to prepare good wires reproducibly, we need a way to measure enough of them."
- "So this talk is about the measurement workflow, and about the small setup we built to make that workflow faster."

### Slide 2 - My Practical Goal

Time: 1:00

Visible slide text:

- `Goal: learn how to prepare good wires`
- `select functional samples`
- `connect preparation to response`
- `focus slow measurements on the best candidates`

Image / visual:

- Simple workflow: `preparation parameters -> wire response -> better preparation rules`.

Talk out loud:

- "My goal is not just to measure one nice wire."
- "The real question is: how do we prepare good wires on purpose?"
- "In Prague we have many wires prepared under different conditions, and we need to understand which ones are actually promising."
- "That means connecting preparation conditions, like composition, annealing, and pre-stress, with the final behavior of the wire."
- "So for me this becomes a selection and mapping problem: which preparation route gives a useful and reproducible response, and which samples deserve the slow detailed measurements?"

### Slide 3 - Why This Requires Many Measurements

Time: 1:15

Visible slide text:

- `Many samples`
- `many preparation conditions`
- `many possible outcomes`
- `fast screening -> precise validation`

Image / visual:

- Matrix or funnel: compositions / annealing settings / stress levels leading to selected good candidates.

Talk out loud:

- "The difficulty is that every sample can be a little different."
- "So even if I make one very precise measurement, it does not tell me the whole story."
- "I need to compare many wires, many treatments, and ideally see trends."
- "This is why the workflow has to include screening methods. For example, resistance during Joule heating can quickly show whether something interesting is happening."
- "Precision is still important, but throughput starts to matter too."

### Slide 4 - What We Need From The Measurement

Time: 1:15

Visible slide text:

- `transition temperature`
- `strain response`
- `load / stress dependence`
- `current, resistance, power`
- `repeatability`

Image / visual:

- One response curve with labels: transition point, strain magnitude, hysteresis/temperature region if available.

Talk out loud:

- "For each wire, the key things I want are quite concrete."
- "At what temperature, or under what heating condition, does the transformation happen?"
- "How much strain do we get?"
- "And how does that depend on the applied load or stress?"
- "Because the heating is electrical, current alone is not always the most physical way to look at the sweep. From current and resistance we can also calculate power very quickly."
- "These are exactly the kinds of values where DMA is very useful, especially when we want the final precise numbers."

### Slide 5 - DMA Is Excellent, But It Becomes The Bottleneck

Time: 1:30

Visible slide text:

- `Commercial DMA`
- `precise strain and temperature values`
- `slow runs`
- `liquid nitrogen`
- `limited access`

Image / visual:

- Bottleneck diagram: many samples entering one commercial DMA queue.

Talk out loud:

- "The commercial DMA is the best tool for this when we want precise strain and temperature values."
- "But in practice it is not a quick screening tool."
- "Each run takes time, it needs liquid nitrogen, and access to the instrument is limited."
- "So if we have many samples, the measurement itself becomes the bottleneck."
- "That led to a very practical question: can we build something simpler that helps us decide which samples deserve the full DMA measurement?"

### Slide 6 - Measurement Strategy

Time: 1:00

Visible slide text:

- `TMA for screening and protocol development`
- `commercial DMA for final validation`

Image / visual:

- Pipeline: `many wires -> TMA -> selected candidates -> commercial DMA`.

Talk out loud:

- "So the idea is not to replace the commercial DMA."
- "I see this more as a first-pass instrument."
- "We can use it to screen samples, try different protocols, and understand which wires are worth sending to the proper DMA measurement."
- "In other words, TMA should make the commercial DMA time more focused and more useful."

### Slide 7 - TMA Concept

Time: 1:30

Visible slide text:

- `motor controls displacement`
- `balance measures load`
- `power supply heats the wire`
- `current, resistance, power are logged`
- `software controls the experiment`

Image / visual:

- Full setup photo with direct component labels.

Talk out loud:

- "The setup is quite simple in terms of hardware."
- "The motor changes the wire length."
- "The balance tells us the load."
- "The power supply heats the wire by current."
- "At the same time, we record the electrical quantities, so we can later look not only at current, but also at resistance and calculated power."
- "The interesting part is that the software makes these parts work together as one experiment."
- "The experiment we care about is: heat the wire, and at the same time try to control load, stress, or strain."

### Slide 8 - Why The Control Problem Is Nontrivial

Time: 1:30

Visible slide text:

- `For a 13 um wire: 1 g ~= 74 MPa`
- `0.005 g digit ~= 0.37 MPa`
- `scale feedback ~= 5 Hz`

Image / visual:

- Reuse the precise-but-slow scale slide, simplified.

Talk out loud:

- "One thing that makes this slightly tricky is the scale of the forces."
- "In grams, the loads look very small."
- "But for a 13 micrometer wire, one gram is already around 74 MPa."
- "So the balance resolution is actually meaningful."
- "The downside is speed: the balance gives us fresh useful data only about five times per second."
- "That means the software has to be careful. It cannot behave as if it had a fast load cell."

### Slide 9 - Setup And Closed-Loop Control

Time: 1:45

Visible slide text:

- `define l0 before the sweep`
- `large corrections far away`
- `small gated corrections near target`
- `fresh feedback gates final decisions`

Image / visual:

- Combine the current `l0` setup slide and `How it approaches 100 MPa` control slide, or use one slide with two compact panels.

Talk out loud:

- "Before the actual sweep, the setup has to define a sensible zero for strain."
- "The wire may not be perfectly straight or perfectly preloaded when it is mounted, so the starting position alone is not enough."
- "After that, the controller tries to approach the target stress."
- "When it is far away, it can make larger corrections."
- "When it gets close, it becomes much more conservative: move a little, wait for a fresh balance reading, and then decide again."
- "That is basically how we make a slow but precise balance usable for this kind of control."

### Slide 10 - Current Status / First Results

Time: 1:45

Visible slide text:

- `first controlled sweeps`
- `interpretable response curves`
- `validation still in progress`

Image / visual:

- One dominant result plot from the current deck, annotated with the one feature the audience should see.
- Replot the TMA graph after the PR #262 merge with `Show power top axis` enabled; use measured current on the bottom axis and calculated power on the top axis.

Talk out loud:

- "At this stage, I would describe the results as first proof of workflow."
- "The setup can run a controlled sweep and produce a curve that we can interpret."
- "One nice detail is that the plots do not have to be read only as current sweeps. Since the system records resistance, we can calculate power and show it as a top axis, which is often closer to the heating question."
- "I do not want to overstate this as final validation yet."
- "The next step is repeatability and comparison with the commercial DMA."
- "But it already shows that this kind of focused setup can help with the screening problem."

### Slide 11 - What This Enables / Closing

Time: 1:30

Visible slide text:

- `more samples`
- `faster iteration`
- `better candidates for commercial DMA`
- `AI-assisted instrument development`

Image / visual:

- Left: sample-screening pipeline.
- Right: short build timeline: `idea -> components -> wiring -> drivers -> software`.

Talk out loud:

- "For me, the main value of this setup is faster learning."
- "If we can test more wires before using the commercial DMA, then we can build a better preparation map and choose better candidates for precise validation."
- "And I think there is also an interesting broader point here."
- "This setup was developed very quickly by one person, with AI helping in a lot of the engineering steps: choosing components, reading manuals, wiring, installing drivers, and writing the software."
- "Of course, the physical validation still has to be done by us. The experiment still has to make sense."
- "But it changes what one researcher can realistically build when they have a clear measurement problem."

## Programme-Aware Timing Budget

| Section | Slides | Time |
| --- | ---: | ---: |
| Handoff and goal | 1-2 | 1:30 |
| Measurement need | 3-5 | 4:00 |
| Strategy and setup | 6-7 | 2:30 |
| Control and result | 8-10 | 5:00 |
| Closing | 11 | 1:30 |
| Total | 11 slides | 14:30 |

Emergency 10-slide version: combine slides 3 and 4 into one `What we need to learn from many wires` slide.

## Current Deck Diagnosis

The current deck is hardware-first:

1. Title
2. Motor
3. Scale precision and speed
4. Hardware
5. Machine photo
6. Setup / `l0`
7. Control approach to 100 MPa
8. First results
9. TMA vs commercial DMA
10. Next steps

This is a good technical inventory, but the audience meets the hardware before they understand the problem. The revised flow should start with why DMA matters, why the commercial instrument is a bottleneck, and why a dedicated TMA is a reasonable scientific tool rather than just a gadget.

## Backup 15-Minute Flow With Microwire Intro

Keep this version as a fallback only if the programme changes or Limpat does not cover enough microwire background.

Target: 12 slides, about 14.5 minutes spoken, leaving a little room for transitions. If the session is strict, cut slide 11 and close with slide 12.

### Slide 1 - Title: TMA

Time: 0:30

Visible slide text:

- `TMA`
- `A purpose-built instrument for shape-memory microwire measurements`
- Your name, group, date

Image / visual:

- Strong full-machine photo or close-up of the TMA setup.

Talk out loud:

- "This talk is about why we built a small DMA-like instrument for our microwires."
- "The short version is: the material needs many mechanical transformation measurements, commercial DMA is a bottleneck, and a focused machine can help us iterate faster."

### Slide 2 - Microwires In One Minute

Time: 1:00

Visible slide text:

- `Shape-memory microwires`
- `metallic core + glass coating`
- `micrometer diameter`
- `large axial response in a tiny sample`

Image / visual:

- Reuse the Keyence / microscope image from the older Prague presentation showing the core and glass coating.
- Add two direct labels: `metallic core`, `glass coating`.

Talk out loud:

- "These are metallic wires with micrometer-scale diameter, often covered by glass after fabrication."
- "Their geometry is important: the functional direction is along the wire axis, and the surface-to-volume ratio is high."
- "That makes them attractive for small actuators and fast thermal response."

### Slide 3 - Why These Wires Are Interesting

Time: 1:30

Visible slide text:

- `Structural transformation -> strain`
- `Composition and pre-stress tune the transformation`
- `Goal: large, reproducible, useful response`

Image / visual:

- One simplified transformation sketch or a representative old slide image showing shape-memory behavior.
- Optional tiny phrase: `Ni-Fe-Ga Heusler microwires`.

Talk out loud:

- "The wires we care about are Ni-Fe-Ga-based Heusler microwires."
- "The useful behavior comes from a structural transformation, which can produce reversible strain."
- "The challenge is reproducibility: composition, fabrication, glass-induced stress, annealing, and pre-stress all influence whether the transformation appears where we need it."
- "So we need many measurements, not just one perfect measurement."

### Slide 4 - What We Need To Measure

Time: 1:15

Visible slide text:

- `We need transition temperature and strain response`
- `under controlled load / stress`
- `for many samples`

Image / visual:

- A simple curve with transition region highlighted, or the strongest existing current/strain response plot.

Talk out loud:

- "For this project, knowing that a wire transforms is not enough."
- "We need to know when it transforms, how much strain it produces, and how this changes under stress or pre-stress."
- "That is where DMA is valuable."

### Slide 5 - The Bottleneck

Time: 1:15

Visible slide text:

- `Commercial DMA is precise, but slow`
- `long runs`
- `liquid nitrogen`
- `limited instrument time`
- `many samples`

Image / visual:

- Bottleneck diagram: many samples entering one commercial DMA path.
- Add real numbers later if available.

Talk out loud:

- "The problem is not that commercial DMA is bad. It is the opposite: it is very good."
- "But when we have many microwires to screen, each precise measurement costs time, access, and liquid nitrogen."
- "So we needed a faster way to learn which samples are worth deeper validation."

### Slide 6 - The Design Decision

Time: 0:45

Visible slide text:

- `Not a universal DMA`
- `A focused microwire experiment`
- `motion + load feedback + current heating`

Image / visual:

- Narrowing diagram: `commercial DMA` to `our required workflow`.

Talk out loud:

- "We decided not to build a full commercial DMA clone."
- "We only needed the part that matters for this experiment: tensile control while the wire is heated electrically."
- "That narrower goal made the instrument realistic."

### Slide 7 - TMA At A Glance

Time: 1:15

Visible slide text:

- `Motor changes displacement`
- `Balance measures load`
- `Power supply heats the wire`
- `Software closes the loop`

Image / visual:

- Full setup photo with labels on the actual components.

Talk out loud:

- "The machine is mechanically simple."
- "The motor changes the wire length."
- "The balance provides the force signal."
- "The power supply heats the wire."
- "The software synchronizes all of it into one measurement."

### Slide 8 - Why Control Is Delicate

Time: 1:30

Visible slide text:

- `For a 13 um wire:`
- `1 g ~= 74 MPa`
- `0.005 g digit ~= 0.37 MPa`
- `balance reply ~= 203 ms`

Image / visual:

- Reuse scale slide, but simplify it around the key tension: precise force signal, slow feedback.

Talk out loud:

- "The forces look tiny in grams, but in a micrometer wire they become large stresses."
- "That makes the balance resolution useful."
- "But the balance is slow: roughly one useful reply every 200 ms."
- "So the software cannot pretend this is a fast load cell."

### Slide 9 - Setup And Mechanical Zero

Time: 1:00

Visible slide text:

- `measure length -> optional preload -> return toward zero load -> compute l0`
- `strain zero is measured, not guessed`

Image / visual:

- Reuse the existing `l0 and baseline before the sweep` slide, simplified.

Talk out loud:

- "For such thin wires, the initial mounted state may include slack or misalignment."
- "Before the main measurement, we enter the mounted wire length once, optionally apply preload if the wire is below it, and compute the unloaded gauge length."
- "This makes strain meaningful."

### Slide 10 - Closed-Loop Stress Control

Time: 1:30

Visible slide text:

- `Large corrections far from target`
- `small gated corrections near target`
- `fresh scale feedback before final decisions`

Image / visual:

- Reuse the existing `How it approaches 100 MPa` slide.

Talk out loud:

- "The software reads the load, calculates the stress error, predicts a displacement correction, and commands the motor."
- "Far from the target, larger moves are allowed."
- "Near the target, the controller becomes conservative: one move, wait for fresh balance feedback, then decide again."
- "This is the main software idea that makes the slow balance usable."

### Slide 11 - First Result / Proof Of Workflow

Time: 1:30

Visible slide text:

- `First controlled sweep`
- `TMA can produce interpretable response curves`
- `preliminary validation`

Image / visual:

- One dominant result plot from the current deck, annotated to show what the audience should notice.

Talk out loud:

- "This is the first proof that the workflow can run."
- "The result is not the final validation claim yet."
- "It shows that the machine can control the experiment and produce a curve we can interpret."
- "The next job is repeatability and comparison with commercial DMA."

### Slide 12 - What This Changes

Time: 1:30

Visible slide text:

- `More samples, faster iteration`
- `commercial DMA for validation`
- `TMA for screening and custom workflows`
- `AI-assisted instrument building`

Image / visual:

- Pipeline: `many samples -> TMA screening -> selected samples -> commercial DMA validation`.
- Small side timeline: `idea -> components -> wiring -> drivers -> software`.

Talk out loud:

- "The purpose is not to replace commercial DMA."
- "The purpose is to move faster: screen more wires, test recipes, and reserve commercial DMA for the strongest candidates."
- "There is also a second lesson: this was built by one researcher with AI assistance."
- "AI helped with component selection, manuals, wiring, drivers, and software. The human part was defining the scientific need, assembling the physical system, and validating whether the behavior made sense."
- "That is the broader message: a clear experimental idea can become working lab infrastructure much faster now."

## Timing Budget

| Section | Slides | Time |
| --- | ---: | ---: |
| Opening | 1 | 0:30 |
| Microwire motivation | 2-4 | 3:45 |
| Measurement bottleneck | 5-6 | 2:00 |
| TMA design/control | 7-10 | 5:15 |
| Result and close | 11-12 | 3:00 |
| Total | 12 slides | 14:30 |

To keep it safely under 15 minutes, combine slides 11 and 12 if the first result is simple or if the introduction takes longer than expected.

## Expanded Reference Flow

This older 16-slide outline is useful as a content reservoir, but it is probably too long once the microwire intro is added.

Aim for 12 main slides plus 2 optional backup slides. If time is short, combine slides 6-7 and skip slide 13.

### Slide 1 - Title

Visible slide text:

- `TMA`
- `A purpose-built instrument for faster shape-memory microwire measurements`
- Your name, group, date

Image / visual:

- Strong full-machine photo or clean close-up of the TMA setup.
- If the current title slide already has the best image, keep it but make the title more specific than only `TMA`.

Talk out loud:

- "This is a small instrument we built because our measurement problem became a throughput problem."
- "The goal is not to replace every function of a commercial DMA, but to build the specific measurement workflow we need for our microwires."
- "I will show the motivation, the hardware, the control idea, the first results, and what building it this way changes."

### Slide 2 - Why DMA Is Valuable

Visible slide text:

- `Why DMA?`
- `Transition temperatures and strain response are the quantities we need to trust.`
- Optional small labels: `transition temperature`, `strain`, `stress/load`, `heating/cooling`

Image / visual:

- A simple conceptual curve or an existing representative result showing a transition region.
- Avoid too much theory. The slide should say: DMA gives the right kind of information.

Talk out loud:

- "For shape-memory materials, the important information is not only whether the sample changes, but at what temperature or heating condition it changes and how much strain is produced."
- "DMA is a very good method because it connects mechanical response with temperature or heating in a controlled way."
- "That precision is why we care about it."

### Slide 3 - The Bottleneck

Visible slide text:

- `The problem: the good measurement is slow`
- `Many samples`
- `Long measurement time`
- `Liquid nitrogen consumption`
- `Limited instrument access`

Image / visual:

- A sparse bottleneck diagram: many sample icons or labels entering one commercial DMA path.
- If available, include a photo of the commercial DMA or a simple timeline showing one sample consuming a long slot.

Talk out loud:

- "The issue is not that commercial DMA is bad. It is excellent."
- "The problem is that excellent measurements can be expensive in time and liquid nitrogen."
- "When we have many samples to screen, the commercial DMA becomes the bottleneck."
- "So the question became: can we build a focused system that gives us the key information faster?"

Facts to fill if available before final PPTX edit:

- Typical measurement duration per sample.
- Approximate liquid nitrogen use per measurement or per day.
- Approximate number of samples waiting / typical batch size.
- How difficult it is to reserve the commercial DMA.

### Slide 4 - The Design Decision

Visible slide text:

- `We did not need a universal DMA.`
- `We needed one controlled microwire experiment.`
- `Hold load/stress/strain while heating electrically.`

Image / visual:

- A narrowing diagram: `Commercial DMA capabilities` on one side, `our required workflow` narrowed to load/stress/strain + current sweep + logging.
- Could also use three large open labels: `tension`, `heating`, `logging`.

Talk out loud:

- "A commercial DMA is a general instrument. Our first requirement was narrower."
- "For microwires, the load values are small, but the stress is large because the diameter is tiny."
- "The key experiment is to heat the wire while controlling the mechanical condition."
- "That makes a dedicated instrument realistic."

### Slide 5 - TMA At A Glance

Visible slide text:

- `TMA = motion + force feedback + electrical heating + software control`
- Labels on the image: `linear actuator`, `balance`, `wire`, `power supply`, `PC/software`

Image / visual:

- Best full setup photo from the current deck.
- Add direct labels to the real components rather than using a generic block diagram.

Talk out loud:

- "The machine is mechanically simple."
- "The motor changes tensile displacement."
- "The balance gives force feedback."
- "The power supply heats the wire."
- "The software connects these into a synchronized measurement."

### Slide 6 - Why The Force Measurement Is Sensitive

Visible slide text:

- `For a 13 um wire, 1 g is about 74 MPa`
- `0.005 g balance digit is about 0.37 MPa`
- `0.1 g error is about 7.4 MPa`

Image / visual:

- One clean mini-table or scale graphic showing grams on one side and MPa on the other.
- This is an important "aha" slide. Keep it simple and large.

Talk out loud:

- "The forces look tiny in grams, but for a microwire they correspond to very large stresses."
- "That is why a laboratory balance can actually be useful here."
- "But it also means the controller has to be careful. A small load error can become a meaningful stress error."

### Slide 7 - Hardware Choice: Precise But Slow Balance

Visible slide text:

- `G&G E150Y-3 balance`
- `0.005 g readability`
- `about 203 ms per useful reply`
- `high resolution, low bandwidth`

Image / visual:

- Reuse the current scale slide image.
- Keep the existing `0.005 g` and `~203 ms` metrics, but frame the point explicitly: the scale is precise but slow.

Talk out loud:

- "This balance is the core feedback signal."
- "It is precise enough for the load range we care about."
- "But it is not a fast force sensor. In request/response mode we get roughly one useful load value every 200 ms."
- "So the control strategy has to respect the real feedback rate."

### Slide 8 - Hardware Choice: Linear Motion

Visible slide text:

- `Stepper linear actuator`
- `0.01 mm full-step travel`
- `about 800 Tic units/mm at 1/8 microstepping`
- `open-loop position, closed-loop load response`

Image / visual:

- Reuse the current motor slide or show the motor in the setup photo with a magnified crop.

Talk out loud:

- "The motor gives controlled displacement."
- "The motor itself is open loop; it knows the commanded position, not the true sample load."
- "Closed-loop behavior comes from combining motion commands with fresh balance feedback."
- "That distinction is important: the motor moves, but the controlled quantity can be stress or load."

### Slide 9 - Setup Before The Sweep

Visible slide text:

- `Before every run: define the mechanical zero`
- `measure length -> optional preload -> return toward zero load -> compute l0`

Image / visual:

- Reuse or redraw the current `l0 and baseline before the sweep` slide.
- Show the sequence as a left-to-right process, not as dense text.

Talk out loud:

- "For microwires, the initial mounted position is not a reliable strain zero."
- "The wire can be slack, bent, or slightly misaligned."
- "The setup step records the mounted length, optionally applies a small preload, and then estimates the unloaded gauge length."
- "This makes strain calculation more meaningful than simply using the starting motor position."

### Slide 10 - Control Logic

Visible slide text:

- `How it approaches 100 MPa`
- `Large corrections far away`
- `Small gated corrections near target`
- `Fresh scale feedback gates the final steps`

Image / visual:

- Reuse the current control slide, but make it a clear story of far mode vs near mode.
- Consider showing two zones: `far from target` and `near target`.

Talk out loud:

- "The software does not just move at a fixed speed and hope."
- "It reads the latest force value, calculates the error, estimates how much displacement should correct that error, and sends a motor move."
- "Far from the target, larger corrections are safe."
- "Near the target, the system becomes conservative: one correction, wait for a fresh balance reading, then decide again."
- "This is how the slow balance becomes usable instead of dangerous."

### Slide 11 - The DMA-Like Measurement

Visible slide text:

- `Current sweep while holding the mechanical condition`
- `iso-load`
- `iso-stress`
- `iso-strain`

Image / visual:

- Simple time diagram: current ramps upward while stress/load/strain is held near target.
- If there is an existing current sweep result, use it as the proof layer.

Talk out loud:

- "This is the part that makes it DMA-like for our application."
- "The current ramp heats the wire."
- "At the same time, the mechanical servo keeps correcting displacement to hold load, stress, or strain."
- "If the stress error becomes too large, the current ramp can pause while mechanical control continues."
- "So the experiment is not only logging. It is actively controlling the condition during heating."

### Slide 12 - First Results

Visible slide text:

- `First results`
- `The system can run a controlled sweep and produce interpretable response curves.`
- Optional: `preliminary`, if the data still needs validation.

Image / visual:

- Use the strongest existing result plot from the current slide 8.
- Prefer one dominant graph over several small plots.
- Annotate the one feature the audience should see.

Talk out loud:

- "This is the first evidence that the machine is useful."
- "The important point is not that the system is already perfect."
- "The important point is that it can run the intended workflow and produce data that we can analyze."
- "The remaining work is calibration, repeatability, and comparison against the commercial DMA."

### Slide 13 - TMA vs Commercial DMA

Visible slide text:

- `Commercial DMA`
- `full instrument, validated standard, temperature-driven`
- `TMA`
- `focused instrument, fast iteration, current-driven`
- Existing cost comparison if defensible: `about 350 EUR vs about 30,000 EUR`

Image / visual:

- Use a calm comparison, not a "winner/loser" fight.
- Two columns are okay here because this is a real comparison slide.

Talk out loud:

- "I do not want to claim this replaces a commercial DMA."
- "The commercial instrument remains the standard for validated measurements."
- "TMA is a complementary screening and development tool."
- "It lets us run more experiments, test ideas faster, and reserve the commercial DMA for the most important validation measurements."

Facts to verify before final PPTX edit:

- The 350 EUR estimate and what it includes.
- The 30,000 EUR estimate and whether it is a real quote, approximate price, or illustrative number.

### Slide 14 - What This Enables

Visible slide text:

- `More samples, faster iteration`
- `custom recipes`
- `complete audit trail`
- `lower cost per experiment`

Image / visual:

- A clean pipeline: `sample idea -> TMA screening -> selected samples -> commercial DMA validation`.
- Or a screenshot/summary of the saved run artifacts: `measurement.csv`, `scale_raw.csv`, `control_trace.csv`, `metadata.json`.

Talk out loud:

- "The value is not only the hardware cost."
- "The value is that we can try more samples and more recipes."
- "Every run saves the raw scale data, the clean measurement table, metadata, and control trace."
- "That means if something strange happens, we can inspect why the controller moved, waited, accepted, or paused."

### Slide 15 - How It Was Built

Visible slide text:

- `One researcher + AI-assisted engineering`
- `component selection`
- `wiring`
- `drivers`
- `software`
- `documentation`
- `validation`

Image / visual:

- Timeline or chain: `idea -> ChatGPT component research -> ordered hardware -> wiring guidance -> PC setup -> Codex software automation -> working instrument`.
- Use this as a human story slide, not as a technical proof slide.

Talk out loud:

- "There is another part of the story that I think is important."
- "This was not built by a large engineering team."
- "AI tools helped select components, interpret manuals, map cables to pins, install the correct drivers, and write the software."
- "My role was still essential: define the scientific goal, connect and test the real hardware, check whether the behavior made physical sense, and validate the results."
- "The important change is that one person with a clear idea can now prototype laboratory tools much faster than before."

Tone note:

- Do not make this sound like "AI did everything."
- The stronger message is: AI compressed the engineering path, while scientific judgment and physical validation stayed with the researcher.

### Slide 16 - Next Steps

Visible slide text:

- `Next steps`
- `temperature measurements`
- `repeatability`
- `commercial DMA comparison`
- `control tuning`
- `more sample batches`

Image / visual:

- Reuse the existing next-steps slide if the image is strong.
- If possible, show the temperature measurement path as the next missing capability.

Talk out loud:

- "The next step is not to declare the instrument finished."
- "The next step is to validate it systematically."
- "We need temperature measurements, repeatability checks, comparison with commercial DMA, and better understanding of control limits."
- "But the main result is already clear: the measurement bottleneck can be attacked with a focused instrument."

## Optional Backup Slides

### Backup A - Safety And Limits

Visible slide text:

- `What keeps the rig safe?`
- `emergency stop`
- `load limits`
- `raw scale ceiling`
- `voltage-limit behavior`
- `wire-break detection`

Talk out loud:

- Use only if the audience asks whether the rig can damage the balance, motor, or wire.
- Explain the difference between raw scale display and applied wire load.

### Backup B - Run Files / Audit Trail

Visible slide text:

- `Every run is inspectable`
- `measurement.csv`
- `scale_raw.csv`
- `control_trace.csv`
- `metadata.json`

Talk out loud:

- Use if someone asks how results are verified or debugged.
- `measurement.csv` is the clean experiment table.
- `scale_raw.csv` proves the feedback cadence.
- `control_trace.csv` explains controller decisions.
- `metadata.json` stores settings and calibration context.

## Short Version If Time Is Tight

Use 10 slides:

1. Title
2. Why DMA matters
3. Bottleneck
4. TMA at a glance
5. Force sensitivity
6. Hardware: scale + motor combined
7. Setup and control
8. First results
9. Commercial DMA vs TMA
10. AI-assisted build + next steps

## Open Questions Before Editing The PPTX

- Do we have a clean number for typical commercial DMA measurement time per sample?
- Do we know approximate liquid nitrogen consumption per run or per measurement day?
- Should the presentation say "transition temperatures" specifically, or "transition behavior" if temperature is not directly measured yet?
- Is the cost comparison a real estimate we are comfortable showing?
- Which result graph is the strongest single proof slide?
- Do we want the AI-assisted build story as the final slide, or as the penultimate slide before scientific next steps?
