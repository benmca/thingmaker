# Braxton Visualizer Prototype

## Run The Server
Use the built-in Python server from the repo root:

```sh
python -m http.server 8000
```

Then open `http://localhost:8000` in a browser.

## Notes
- The prototype is static (HTML + p5.js). No build step is required.
- Python dependencies are declared in `pyproject.toml`. To install them:

```sh
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

## Diagram Workflow (Repeatable)
For each schematic image:
- Identify all text labels/abbreviations as they appear in the image.
- Create a diagram JSON with one node per label.
- Place nodes close to the printed labels (do not obscure text; OK to overlap lines).
- Do not add edges/junctions during this pass; focus on label placement first.
- Verify alignment in the browser with the overlay; tweak coordinates until labels match.
- Map each abbreviation to a glossary expansion (from `data/ta-w/v1/glossary_integration.json`) and, if possible, a full definition (from `data/ta-w/v1/glossary.json`). Flag any unmatched items for follow-up.
