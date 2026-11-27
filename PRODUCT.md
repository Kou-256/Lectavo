# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

University and graduate students in Japan. They have lecture recordings (screen-recorded slides with narration, sometimes split across several videos or audio-only files) and English research papers to read for seminars. The job: turn a lecture into something they can read and revise from, and read an English paper in Japanese without losing its figures and equations.

## Product Purpose

Lectavo is a free Windows desktop app (Windows 10/11 x64). From video it produces a slide PDF with duplicate slides removed, a timestamped transcript (faster-whisper, NVIDIA CUDA when available) and Markdown lecture notes; audio files (MP3, M4A, WAV) give a transcript. Its PDF translation translates a PDF into 17 languages while keeping the layout, figures, equations, subscripts and superscripts, writes a bilingual side-by-side PDF, OCRs scanned pages, and runs on a built-in local LLM by default (offline, free) with Claude, OpenAI, Gemini, DeepL and Ollama/LM Studio as alternatives. Translated PDFs can be compared full screen, text selected and copied, and edited (text, figures, images). Success on the website: a student downloads and installs it.

## Positioning

Everything runs on the student's own PC: no account, no upload, no subscription, no Python setup. A lecture video becomes a deduplicated slide PDF plus transcript and notes in one run, and an English paper becomes a Japanese PDF that still looks like the paper.

## Operating Context

Students download `Lectavo-setup-<version>-x64.exe` from the latest GitHub Release (linked from https://lectavo.vercel.app), install, then drop videos, audio or PDFs into the app. Typical inputs: recorded lectures and journal papers (e.g. Jpn. J. Appl. Phys. papers with two columns, formulas and chemical notation).

## Capabilities and Constraints

- Windows x64 only; free; MIT License; self-signed binary (publisher "Lectavo / Kou Hashizume"), so Windows may warn about an unknown publisher.
- Website: Japanese, static Vite site in `website/`, deployed with `vercel deploy --prod`. English lives in `README.en.md`.
- GitHub README (`README.md`, `README.en.md`) is the second front door.

## Brand Commitments

- Keep the app icon (`website/public/app-icon.png`, `assets/app_icon.png`) and the "Lectavo" wordmark.
- Site copy stays Japanese-first.
- Show the app's real screens (`website/public/screenshots/`).
- The website takes the category-standard form (headline, real app screens, features, download), executed at the craft level of obsidian.md (chosen 2026-11-27).

## Evidence on Hand

- Real app screenshots: `website/public/screenshots/` (home, ready, running, complete, review, region, audio, preflight, compare).
- Real sample outputs: `website/public/samples/` (slide PNGs, transcript, lecture notes).
- No testimonials, user counts, press or benchmarks exist; none may be invented.

## Product Principles

1. Download first: the free Windows installer is always one click away.
2. Show the real output (slides, transcript, translated paper), not claims about it.
3. Local and free is the core promise; never imply a cloud service or account.
4. Students' language: plain Japanese, no hype.
