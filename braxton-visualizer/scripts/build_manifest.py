#!/usr/bin/env python3
import json
import re
from pathlib import Path


DEFAULT_ROOT = Path('/Users/ben/src/braxton-visualizer/assets/ta-w/v1')
DEFAULT_OUT = Path('/Users/ben/src/braxton-visualizer/data/ta-w/v1/manifest.json')

CHAPTERS = [
    'Preface & Acknowledgements (second edition)',
    'Introduction',
    'Glossary Integration',
    'I. Underlying Philosophical Bases',
    'A. Level One',
    '1. World Music',
    '2. Western Art Music',
    '3. Trans-African Music',
    'B. Level Two',
    'C. Level Three: Questions & Answers',
    'II. Transition',
    '1. Western Art Continuance',
    '2. Creative Music From the Black Aesthetic',
    '3. The White Improvisor',
    '4. The Post-Cage Continuum',
    'III. Extended Functionalism',
    '1. The New York Movement',
    '2. The Midwest (and West Coast) Continuum',
    'Glossary',
]


def slug(title: str) -> str:
    s = title.replace('&', 'and')
    s = re.sub(r'[\\(\\)\\.]', '', s)
    s = s.replace(':', '')
    s = s.replace('  ', ' ')
    s = s.replace(' ', '-')
    s = re.sub(r'-+', '-', s)
    return s


def main() -> None:
    root = DEFAULT_ROOT
    manifest = {
        'volume': 'V1',
        'chapters': []
    }

    for title in CHAPTERS:
        folder = root / slug(title)
        items = []
        if folder.exists():
            for img in sorted(folder.glob('*.jpg')):
                items.append({
                    'file': img.name,
                    'path': f'assets/ta-w/v1/{folder.name}/{img.name}',
                    'base': img.stem,
                    'label': img.stem.split('-')[-1],
                })
        manifest['chapters'].append({
            'title': title,
            'slug': slug(title),
            'items': items
        })

    DEFAULT_OUT.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_OUT.write_text(json.dumps(manifest, indent=2))
    print(DEFAULT_OUT)


if __name__ == '__main__':
    main()
