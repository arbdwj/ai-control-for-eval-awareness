# Writeups

The report on the 2026-10-04 full run, in three formats built from the same content:

| File | Use |
| :-- | :-- |
| `report.md` | Markdown, renders on GitHub; figures in `figures/` |
| `report.docx` | Word, generated from the Markdown with the `docx` library |
| `report.html` | Standalone page with interactive charts (hover, table views); also published as a private Claude artifact |
| `figures/*.png` | matplotlib renders of the three charts, 200 dpi |

To regenerate the figures: `python -m probing_control.evaluate ...` gives the numbers; the plotting code lives in the session notes for now and the numbers are in `experiments/2026-10-04_full_glm53_rollout1/summary.json`.
