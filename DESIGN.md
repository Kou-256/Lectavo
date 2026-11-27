---
name: Lectavo website
description: Dark graphite product page for a free Windows app that turns lecture videos and English papers into readable material.
colors:
  bg: "#0b1012"
  bg-raise: "#111a1d"
  bg-raise-2: "#172226"
  line: "rgba(222, 240, 238, 0.09)"
  line-strong: "rgba(222, 240, 238, 0.18)"
  text: "#edf3f2"
  text-2: "#a9b8b7"
  text-3: "#83938f"
  accent: "#19b4a9"
  accent-strong: "#3fd6cb"
  accent-ink: "#032220"
  amber: "#f4b94e"
  paper: "#fbfaf6"
  paper-ink: "#1d2422"
typography:
  display:
    fontFamily: "Zen Kaku Gothic New, Yu Gothic UI, Hiragino Sans, Meiryo, sans-serif"
    fontSize: "clamp(2.2rem, 1.3rem + 4vw, 4.4rem)"
    fontWeight: 900
    lineHeight: 1.22
    letterSpacing: "-0.01em"
  section-heading:
    fontFamily: "Zen Kaku Gothic New, Yu Gothic UI, Hiragino Sans, Meiryo, sans-serif"
    fontSize: "clamp(1.75rem, 1.2rem + 2.2vw, 2.9rem)"
    fontWeight: 900
    lineHeight: 1.3
  feature-heading:
    fontFamily: "Zen Kaku Gothic New, Yu Gothic UI, Hiragino Sans, Meiryo, sans-serif"
    fontSize: "clamp(1.25rem, 1.1rem + 0.6vw, 1.6rem)"
    fontWeight: 700
    lineHeight: 1.45
  body:
    fontFamily: "Zen Kaku Gothic New, Yu Gothic UI, Hiragino Sans, Meiryo, sans-serif"
    fontSize: "16px"
    fontWeight: 400
    lineHeight: 1.8
  meta:
    fontFamily: "Zen Kaku Gothic New, Yu Gothic UI, Hiragino Sans, Meiryo, sans-serif"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.6
rounded:
  control: "10px"
  frame: "14px"
  stage: "20px"
  closing: "28px"
  pill: "999px"
spacing:
  section: "148px"
  section-mobile: "104px"
  feature: "44px"
  gutter: "20px"
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.accent-ink}"
    rounded: "{rounded.control}"
    padding: "0.95em 1.6em"
  button-primary-hover:
    backgroundColor: "{colors.accent-strong}"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.text}"
    rounded: "{rounded.control}"
  app-shot:
    backgroundColor: "#0f1619"
    rounded: "{rounded.frame}"
  explainer-stage:
    backgroundColor: "{colors.bg-raise}"
    rounded: "{rounded.stage}"
  paper:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.paper-ink}"
    rounded: "4px"
---

## Overview

The Lectavo site (`website/`) is the category-standard software page, played straight at obsidian.md's craft: one promise, the real app as the hero, every capability proven by a real screen or a real output. It is a dark graphite page with a single teal accent taken from the app icon. Copy is Japanese first; the English story lives in `README.en.md`.

The one orchestrated motion is the PDF translation explainer: a mock journal page where text blocks are outlined, protected terms are marked, English is replaced by Japanese paragraph by paragraph, and the page is written back. `assets/readme/pdf-translate.svg` mirrors it for GitHub.

## Colors

- **Graphite ground** (`bg`, `bg-raise`, `bg-raise-2`) carries every section; raised panels step up one shade and take a hairline `line` border instead of a shadow.
- **Teal** (`accent`, `accent-strong`) is the only action and live-state color: buttons, step numbers, the active explainer step, screenshot callouts, progress.
- **Amber** belongs to the translation demo alone: it marks protected terms and translated lines. Do not use it elsewhere.
- **Paper** (`paper`, `paper-ink`) appears only inside the explainer's mock page.
- Text steps: `text` for headings, `text-2` for body, `text-3` for meta and captions (all above 4.5:1 on `bg`).

## Typography

Zen Kaku Gothic New (Google Fonts, weights 400/500/700/900) for everything; 900 for the hero and section headings, 700 for feature headings and FAQ questions. Headings use `text-wrap: balance` and `word-break: auto-phrase` so Japanese breaks at phrase boundaries. The mock paper alone uses Times/Arial to look like a journal page. Transcript previews use Cascadia Mono / Consolas because they show a real file.

## Layout

Content width 1160px with a 20px gutter. Sections are separated by 148px (104px on phones) and open with a heading block (max 760px). Features alternate copy and media in a 5:7 grid separated by hairlines; the how-to steps use a 4:8 grid with the text top-aligned to the screenshot. The hero is centered. Below 980px every grid stacks and the nav collapses to the download button; below 680px the promises list, feature pairs and facts become one column.

## Elevation & Depth

Depth comes from shade steps and hairline borders. App screenshots take a long soft shadow (`0 30px 70px -36px rgba(0,0,0,.85)`); the hero screenshot a deeper one. One radial teal glow sits behind the hero and the closing panel; nothing else glows.

## Shapes

Rounded rectangles throughout: 10px controls, 14px screenshot frames and file previews, 20px explainer stage, 28px closing panel, pills for tags and badges. App screenshots sit in a window frame with a 30px chrome bar and three dots.

## Components

- **Buttons**: primary teal with dark ink and a download icon; ghost with a strong hairline. Hover lifts 1px with a neutral dark shadow.
- **App shot**: window chrome plus a real screenshot; numbered teal callouts point at controls.
- **Feature row**: heading, body, then media with a quiet caption.
- **Explainer**: a tab list of four steps (each with a progress line) beside the stage; autoplays when in view, a click or arrow key selects a step and pauses, a play/pause button resumes; reduced motion shows the final step.
- **Facts**: a two-column definition list separated by hairlines, never icon cards.
- **FAQ**: `details` rows with a chevron.

## Do's and Don'ts

- Do prove each capability with a real screenshot or real output; label sample content as sample.
- Do keep amber for translation marking only, and teal for actions.
- Don't add same-size icon cards, eyebrow labels above headings, or gradient text.
- Don't invent user counts, testimonials or benchmarks.
- Don't add a second autoplaying animation; the explainer is the page's one sequence.
