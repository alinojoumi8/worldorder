---
name: POLIS
description: A living systems atlas for understanding a city of AI agents.
colors:
  drafting-white: "#f5f8fb"
  surface: "#ffffff"
  surface-blue: "#edf3f8"
  surface-blue-strong: "#dfe9f2"
  graphite: "#122033"
  graphite-secondary: "#26374c"
  muted: "#58697d"
  line: "#c9d4df"
  line-strong: "#9cacbd"
  signal-cobalt: "#145ae8"
  cobalt-deep: "#0c43b7"
  cobalt-soft: "#e5eeff"
  proof-vermilion: "#ce3429"
  vermilion-soft: "#fdeae8"
  transit-amber: "#a95c00"
  amber-soft: "#fff2dd"
  civic-green: "#13764a"
  green-soft: "#e6f4ec"
  civic-teal: "#0e7782"
  cognition-violet: "#7055ba"
typography:
  display:
    fontFamily: "Bahnschrift, Arial Narrow, Segoe UI, sans-serif"
    fontSize: "clamp(1.65rem, 2.2vw, 2.45rem)"
    fontWeight: 650
    lineHeight: 1
    letterSpacing: "-0.025em"
  body:
    fontFamily: "Segoe UI Variable, Segoe UI, system-ui, sans-serif"
    fontSize: "0.82rem"
    fontWeight: 400
    lineHeight: 1.48
  label:
    fontFamily: "Bahnschrift, Arial Narrow, Segoe UI, sans-serif"
    fontSize: "0.64rem"
    fontWeight: 700
    lineHeight: 1.2
    letterSpacing: "0.06em"
  address:
    fontFamily: "Cascadia Mono, SFMono-Regular, Consolas, monospace"
    fontSize: "0.62rem"
    fontWeight: 500
    lineHeight: 1.35
rounded:
  indexed: "2px"
  control: "3px"
  status: "999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "24px"
  canvas: "28px"
components:
  button-primary:
    backgroundColor: "{colors.signal-cobalt}"
    textColor: "{colors.surface}"
    typography: "{typography.label}"
    rounded: "{rounded.indexed}"
    padding: "0 13px"
    height: "36px"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.graphite}"
    typography: "{typography.label}"
    rounded: "{rounded.indexed}"
    padding: "0 13px"
    height: "36px"
  navigation-active:
    backgroundColor: "{colors.cobalt-soft}"
    textColor: "{colors.cobalt-deep}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
---

# Design System: POLIS

## Overview

**Creative North Star: "The Living Systems Atlas"**

POLIS feels like a civic-scale scientific instrument: an explorable city rendered through the visual language of urban systems atlases, transit-control maps, and translucent analytical overlays. The city remains continuously present while work, housing, markets, social life, and cognition become inspectable layers. Macro patterns and individual decisions share one coordinate system.

The interface is precise, luminous, and spatial without becoming game-like. Its reusable signature is the **evidence trace**: a cobalt route that can begin on a district, chart anomaly, or event and continue through causal steps into a specific agent's perception, memory, decision, and outcome.

**Key Characteristics:**

- City-first, with analytical layers instead of decorative scenery.
- Cool, bright working surfaces suited to long research sessions.
- Dense but strongly indexed, with persistent tick and sequence references.
- Linear movement interpolation and deliberate overlay transitions only.
- Warnings and uncertainty are structural elements, never transient decoration.

## Colors

The strategy is restrained: cool civic neutrals carry most of the interface, cobalt owns navigation and evidence traces, and semantic colors appear only when the underlying data requires them.

- **Drafting White:** the primary working field and map substrate.
- **Graphite:** primary text, geometry, and institutional structure.
- **Signal Cobalt:** active routes, selected agents, causal links, and focus.
- **Vermilion Proof Mark:** blocking drift, invalid reconstruction, and invariant failure.
- **Transit Amber:** lag, sampling limits, and unresolved warnings.
- **Civic Green:** verified integrity, healthy systems, and positive status.
- **District Tints:** low-chroma thematic colors for economic, social, political, legal, and demographic layers.

**The Evidence Color Rule.** Saturated color must encode a current selection, data relationship, or scientific state. It is never scattered as decoration.

## Typography

**Display Font:** Bahnschrift SemiCondensed, with Arial Narrow and sans-serif fallbacks  
**Body Font:** Segoe UI Variable, with system-ui and sans-serif fallbacks  
**Label/Mono Font:** Cascadia Mono, with ui-monospace and monospace fallbacks

**Character:** Civic signage meets precise instrumentation. Condensed headings let views carry long research labels without shouting; the body remains calm and familiar; ticks, sequence IDs, metrics, and hashes use tabular mono.

**The Addressable Type Rule.** Anything a researcher may quote, compare, or navigate to—tick, sequence, run, agent, metric, event kind—uses the mono voice and remains selectable text.

## Layout

The city or primary analytical object owns the largest contiguous region. Navigation, filters, and evidence details attach as rails, leaves, and overlays around it rather than enclosing every idea in a card. Desktop uses a persistent left atlas index, a central spatial canvas, and a contextual evidence rail. Dense views may widen the evidence rail or replace the canvas, but preserve the same coordinate and reference language.

On smaller screens the atlas index becomes a horizontal view strip, the primary object remains first, and the evidence rail becomes a bottom sheet with visible provenance. Mobile is a monitoring and inspection adaptation, not a compressed replica of every desktop comparison tool.

## Elevation & Depth

Depth comes from stacked drafting films: pale tonal separation, fine edge keys, small offset shadows, and occasional translucency over the city canvas. Panels feel physically layered but never glassy or glossy. The map may use restrained 2.5D height to reveal density and land use; controls and data remain flat, sharp, and readable.

**The One City Plane Rule.** Decorative perspective never competes with data. Extrusion communicates a real district or place value, otherwise geometry stays flat.

## Shapes

Forms are rectilinear with very small radii and occasional clipped corners that recall indexed map sheets. Evidence nodes use circles only when representing actual graph entities or agent positions. Pills are reserved for compact status values, never used as generic containers.

## Components

### Buttons

- **Shape:** indexed rectangular controls with two-pixel corners.
- **Primary:** signal cobalt with white label text and a deep-cobalt hover.
- **Secondary:** white drafting surface with a strong keyline; hover introduces the pale atlas field and cobalt text.
- **Focus:** a clearly separated cobalt focus ring; focus never depends on color change alone.

### Navigation

- **Atlas index:** a persistent vertical strip on desktop and an overflow-safe horizontal strip on smaller screens.
- **Active state:** pale cobalt field, cobalt icon and label, plus a short registration mark at the outer edge.
- **Behavior:** every main view is reachable with one action and uses `aria-current="page"`.

### Evidence Rail

The signature component joins indexed steps with a single cobalt route. Step color changes only for semantic states: vermilion for blocking evidence, violet for cognition, amber for uncertain or reconstructed material, and green for verified outcomes.

### Persistent Trace

Outside the map, a compact evidence breadcrumb preserves the active macro signal, selected agent, and recorded outcome. Each addressable step returns to its source view, survives browser history, and keeps the selected agent coherent across the Observatory.

### Mobile Run Status

On monitoring-sized screens, read-only mode, run health, freshness, and lag remain visible in a compact graphite strip below navigation. Scientific status is never removed merely to gain viewport space.

### Map Layers

Layer controls sit on an opaque drafting sheet above the map. Checked states use square cobalt registration marks. Map color is reserved for district fields, actual movement flows, agent sampling, and selection.

### Tables and Registries

Tables use pale atlas headers, thin horizontal keylines, selectable rows, and mono addresses. Blocking comparison differences color the entire affected row; scorecards keep every dimension separate and never synthesize a composite. Wide scientific tables use an explicit horizontal-scroll cue on mobile and pin their identifying first column.

### Warnings

Warnings remain in the document flow. Blocking drift uses vermilion, sampling and reconstruction use amber, and verified integrity uses civic green. No warning is dismissible when it changes scientific interpretation.

## Do's and Don'ts

### Do:

- **Do** keep the living city visible as the anchor when the task permits it.
- **Do** make the Map → Cause → Agent trail continuous and visibly addressable.
- **Do** distinguish “not recorded,” “did not happen,” and “failed to reconstruct.”
- **Do** keep scientific warnings attached to exported or captured visualizations.
- **Do** use synthetic demonstration data at full fidelity and label it clearly.

### Don't:

- **Don't** turn POLIS into a game HUD, fantasy city, or photorealistic world renderer.
- **Don't** use generic dashboard card grids as the primary composition.
- **Don't** hide lag, sampling gaps, drift, or uncertainty in tooltips or dismissible toasts.
- **Don't** use decorative neon, glassmorphism, or arbitrary gradients.
- **Don't** add run controls, shock controls, or other mutation affordances to the Observatory.
