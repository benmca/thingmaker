# Repository Guidelines

## Project Structure & Module Organization
This repository is currently empty (no source, tests, or configuration files detected). When you add code, keep a clear layout and document it here. A practical default for this project is:
- `src/` for application or library code
- `tests/` for automated tests and fixtures
- `assets/` for static files (images, sample data)
- `scripts/` for local tooling (build, migration, data prep)

## Build, Test, and Development Commands
No build system or local dev workflow is configured yet. Add a `README.md` and/or `Makefile` once you introduce tooling. Example conventions you can adopt:
- `npm run dev` for a local server
- `npm test` or `pytest` for unit tests
- `npm run lint` or `ruff check` for linting

## Coding Style & Naming Conventions
No formatting or linting tools are present. Until tooling is defined, pick consistent, language-appropriate defaults:
- Indentation: 2 spaces for JS/TS, 4 spaces for Python (choose per language).
- Filenames: `kebab-case` for files, `PascalCase` for React components/classes.
- Avoid abbreviations in public APIs and keep directory names short and descriptive.

## Testing Guidelines
No testing framework is configured. When tests are added, document:
- Framework (e.g., `vitest`, `jest`, `pytest`).
- Naming pattern (e.g., `*.test.ts`, `test_*.py`).
- How to run tests locally and in CI.

## Commit & Pull Request Guidelines
There is no Git history in this repository yet, so commit conventions are unknown. Suggested baseline:
- Use short, imperative commit messages (e.g., `Add data loader`).
- PRs should include a concise summary, testing notes, and screenshots for UI changes.

## Security & Configuration
No secret management or runtime configuration is set up. If you introduce secrets:
- Store them in `.env` files.
- Add `.env` to `.gitignore`.
- Document required variables in `README.md`.

## Braxton Ebooks (Local Context)
Ebook files are stored outside the repo in Dropbox:
- `/Users/ben/Library/CloudStorage/Dropbox/Books/frog peak/`
- Volume 1 example: `/Users/ben/Library/CloudStorage/Dropbox/Books/frog peak/gamelan_braxton-tri-axium-writings-1-epub_2025-11-04_1754 (1)/Braxton Tri-Axium Writings 1.epub`

How to read:
- EPUB is a ZIP. Use Python `zipfile` to read `OEBPS/xhtml/*.xhtml` for text and `OEBPS/images/*.jpg` for diagrams.
- The Introduction for V1 is in `OEBPS/xhtml/06_Intro.xhtml`.
- TOC/navigation is in `OEBPS/xhtml/00_Nav.xhtml` and `OEBPS/toc.ncx`.
- The schematic referenced in the Introduction is `OEBPS/images/page020.jpg` (saved in repo as `assets/ta-w/v1/TAW-V1-Introduction-01.jpg`).

## Diagram Naming & Storage
- Naming convention: `TAW-V{N}-{SectionName}-{NN}.jpg` (e.g., `TAW-V1-Introduction-01.jpg`).
- `SectionName` should be human-readable and match the chapter/section folder name.
- Images live under `assets/ta-w/v1/<chapter-folder>/`.
- Diagram JSONs live under `data/ta-w/v1/diagrams/<chapter-folder>/` and mirror the assets folder structure.
- One folder per EPUB nav item (chapter/section). Use a simple slug: replace spaces with hyphens, remove punctuation (including `&`), keep Roman numerals and numbers (e.g., `I-Underlying-Philosophical-Bases`, `1-World-Music`, `C-Level-Three-Questions-Answers`).

## Diagram Placement Workflow
Use this end-to-end checklist for each schematic image (Codex performs all steps; “by eye” means Codex visually inspects the image in-session):
1) Open the image in `assets/ta-w/v1/<chapter>/`.
2) Transcribe every printed label exactly as shown (keep punctuation and dots).
3) Create a diagram JSON at `data/ta-w/v1/diagrams/<chapter>/<base>.json`.
4) Add one node per label.
5) Place each node centered over its label by eye (do not obscure text; OK to overlap lines).
6) Do not add edges/junctions in this pass; label placement only.
7) Verify alignment in the browser overlay and nudge coordinates until labels match.
8) Map `glossaryCode` and `fullLabel` using:
   - `data/ta-w/v1/glossary_integration.json` for expansions
   - `data/ta-w/v1/glossary.json` for definitions
   Flag any unmatched items for follow-up.
9) Optional OCR refinement (label-only centering):
   - Run `python scripts/auto_fit_diagram.py` to center labels using OCR.
   - The script only moves nodes when OCR matches the label and leaves unmatched labels unchanged.
   - Use `--no-snap` to disable line snapping, or `--snap` to enable snapping for OCR-matched labels only.
10) Generate and save the OCR overlay image for reference:
    - Run `python scripts/auto_fit_diagram.py --image <image> --diagram <json> --out <json> --overlay-out assets/ta-w/v1/overlays/<chapter>/<base>-overlay-autofit.png`
      to write the overlay directly into the overlays folder (no manual copy needed).

Minimal diagram JSON template:
```json
{
  "id": "TAW-V1-<Chapter>-<NN>",
  "title": "<Chapter Title> – Diagram <NN>",
  "subject": {
    "nodeId": "<NODE_ID>",
    "arrowFrom": { "x": 0, "y": 0 }
  },
  "nodes": [
    {
      "id": "<NODE_ID>",
      "label": "<label.as.printed>",
      "fullLabel": "<expansion or empty>",
      "glossaryCode": "<CODE or empty>",
      "x": 0,
      "y": 0,
      "role": "detail"
    }
  ],
  "edges": []
}
```

## Scripted Pipeline
1) Extract images from the EPUB into chapter folders:
```
python scripts/extract_v1_images.py
```

2) Build the navigation manifest used by the UI:
```
python scripts/build_manifest.py
```

3) Manually create diagram JSONs (label transcription + node placement) for each image.
   - Skip images larger than 70KB (these often contain multiple diagrams and need manual splitting first).
   - Record skipped images in `data/ta-w/v1/diagrams/skipped-manual-split.txt` (one line per image).
   - If needed, use `python scripts/auto_fit_diagram.py` to refine label placement with OCR.

4) Generate missing-glossary reports for all diagram JSONs:
```
python scripts/check_missing_glossary.py
```

These reports are written next to each diagram JSON as `*.missing.json` and list any nodes without a glossary expansion or full definition.

## Diagram Generation Workflow
When the user says “generate a diagram” and the images are already extracted:
1) Run Scripted Pipeline step 2: `python scripts/build_manifest.py`.
2) Run Scripted Pipeline step 3: create the diagram JSONs using the full Diagram Placement Workflow (including optional OCR refinement and overlay generation).
3) Run Scripted Pipeline step 4: `python scripts/check_missing_glossary.py`.
No other steps are required unless explicitly requested.
