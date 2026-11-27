"""Write the sample English paper used for the website's PDF translation screenshots.

The text is written for Lectavo (no real paper is reproduced): two columns, a
figure, an equation, chemical formulas with subscripts and citation marks, like
the papers students read in seminars.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

from reportlab.lib.colors import Color, black
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas

WIDTH, HEIGHT = A4
MARGIN = 52
GUTTER = 22
COLUMN = (WIDTH - 2 * MARGIN - GUTTER) / 2


def paragraph(canvas, x, y, lines, size=9.4, leading=12.2):
    for line in lines:
        draw_rich(canvas, x, y, line, size)
        y -= leading
    return y


def draw_rich(canvas, x, y, line, size):
    """Draw a line where _{..} is a subscript and ^{..} a superscript."""
    import re

    for part in re.split(r"(_\{[^}]*\}|\^\{[^}]*\})", line):
        if not part:
            continue
        if part.startswith("_{"):
            text, rise, scale = part[2:-1], -size * 0.22, 0.7
        elif part.startswith("^{"):
            text, rise, scale = part[2:-1], size * 0.38, 0.7
        else:
            text, rise, scale = part, 0, 1.0
        canvas.setFont("Times-Roman", size * scale)
        canvas.drawString(x, y + rise, text)
        x += canvas.stringWidth(text, "Times-Roman", size * scale)


def figure(canvas, x, y, width, height):
    canvas.setStrokeColor(black)
    canvas.setLineWidth(0.8)
    canvas.rect(x, y, width, height)
    canvas.setFont("Helvetica", 7)
    for i in range(6):
        tx = x + width * i / 5
        canvas.line(tx, y, tx, y + 3)
        canvas.drawCentredString(tx, y - 9, str(i * 5))
    canvas.drawCentredString(x + width / 2, y - 19, "Eu content (%)")
    canvas.saveState()
    canvas.translate(x - 16, y + height / 2)
    canvas.rotate(90)
    canvas.drawCentredString(0, 0, "PL intensity (a.u.)")
    canvas.restoreState()
    canvas.setStrokeColor(Color(0.1, 0.25, 0.6))
    canvas.setFillColor(Color(0.1, 0.25, 0.6))
    canvas.setLineWidth(1.2)
    points = []
    for i in range(26):
        value = math.exp(-((i - 12) ** 2) / 60)
        points.append((x + width * i / 25, y + 8 + (height - 20) * value))
    path = canvas.beginPath()
    path.moveTo(*points[0])
    for point in points[1:]:
        path.lineTo(*point)
    canvas.drawPath(path)
    for px, py in points[::2]:
        canvas.circle(px, py, 1.8, stroke=0, fill=1)
    canvas.setFillColor(black)


def main(out: Path) -> None:
    canvas = Canvas(str(out), pagesize=A4)
    canvas.setTitle("Sample paper for Lectavo")
    left, right = MARGIN, MARGIN + COLUMN + GUTTER

    canvas.setFont("Helvetica", 8)
    canvas.drawString(MARGIN, HEIGHT - 34, "Sample J. Lect. Mater. 3, 101 (2026)")
    canvas.drawRightString(WIDTH - MARGIN, HEIGHT - 34, "Lectavo sample paper")
    canvas.line(MARGIN, HEIGHT - 38, WIDTH - MARGIN, HEIGHT - 38)
    canvas.setFont("Helvetica-Bold", 15)
    canvas.drawCentredString(WIDTH / 2, HEIGHT - 74, "Finding the brightest composition of a phosphor film")
    canvas.drawCentredString(WIDTH / 2, HEIGHT - 93, "with fewer measurements")
    canvas.setFont("Helvetica", 9)
    canvas.drawCentredString(WIDTH / 2, HEIGHT - 112, "A. Sample and B. Example (sample text written for Lectavo)")

    y = HEIGHT - 146
    canvas.setFont("Helvetica-Bold", 9.5)
    canvas.drawString(left, y, "1. Introduction")
    y = paragraph(
        canvas,
        left,
        y - 15,
        [
            "Thin films whose composition changes across the substrate",
            "let us study many materials in a single sample.^{1,2)} Eu-doped",
            "Y_{2}O_{3} is a red phosphor, and its emission depends strongly",
            "on how much Eu^{3+} replaces Y^{3+} in the crystal.^{3)}",
            "Measuring every point of such a film takes hours, so we",
            "asked whether a model could choose the next point to",
            "measure and reach the brightest composition sooner.",
        ],
    )
    y -= 10
    canvas.setFont("Helvetica-Bold", 9.5)
    canvas.drawString(left, y, "2. Method")
    y = paragraph(
        canvas,
        left,
        y - 15,
        [
            "An Eu_{x}Y_{2-x}O_{3} film with x from 0 to 0.25 was grown on",
            "a SrTiO_{3} substrate. After each measurement, the model",
            "predicts the mean and the uncertainty of the emission",
            "and picks the point with the highest upper bound:",
        ],
    )
    canvas.setFont("Times-Italic", 11)
    canvas.drawCentredString(left + COLUMN / 2, y - 8, "UCB(x) = μ(x) + κ σ(x)")
    canvas.setFont("Times-Roman", 10)
    canvas.drawRightString(left + COLUMN, y - 8, "(1)")
    y = paragraph(
        canvas,
        left,
        y - 30,
        [
            "Here μ(x) is the predicted mean and σ(x) the standard",
            "deviation. A small κ favours points that are already",
            "bright, and a large κ favours points we know little about.",
        ],
    )

    figure(canvas, right + 24, HEIGHT - 300, COLUMN - 34, 130)
    canvas.setFont("Helvetica", 8)
    canvas.drawString(right, HEIGHT - 334, "Fig. 1. Emission intensity of the film against the Eu content.")
    canvas.drawString(right, HEIGHT - 344, "The brightest point is near 12%.")
    y = HEIGHT - 372
    canvas.setFont("Helvetica-Bold", 9.5)
    canvas.drawString(right, y, "3. Results")
    paragraph(
        canvas,
        right,
        y - 15,
        [
            "The emission rose with the Eu content and reached its",
            "maximum near 12%, then fell because of concentration",
            "quenching (Fig. 1). With the model choosing the points,",
            "six measurements were enough to find this maximum,",
            "while a full map needed eighty. The predicted curve",
            "matched the full map within the uncertainty σ(x).",
            "",
            "These results show that a model can shorten the search",
            "for the best composition of a phosphor film.^{4)}",
        ],
    )
    canvas.setFont("Helvetica", 8)
    canvas.drawCentredString(WIDTH / 2, 30, "101-1")
    canvas.showPage()
    canvas.save()


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "sample_paper.pdf"))
