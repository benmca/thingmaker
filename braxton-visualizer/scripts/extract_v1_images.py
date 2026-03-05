#!/usr/bin/env python3
import argparse
import re
import zipfile
from pathlib import Path
from html import unescape


DEFAULT_EPUB = Path('/Users/ben/Library/CloudStorage/Dropbox/Books/frog peak/gamelan_braxton-tri-axium-writings-1-epub_2025-11-04_1754 (1)/Braxton Tri-Axium Writings 1.epub')
DEFAULT_OUT = Path('/Users/ben/src/braxton-visualizer/assets/ta-w/v1')

DEFAULT_CHAPTERS = [
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
    s = title
    s = s.replace('&', 'and')
    s = re.sub(r'[\\(\\)\\.]', '', s)
    s = s.replace(':', '')
    s = s.replace('  ', ' ')
    s = s.replace(' ', '-')
    s = re.sub(r'-+', '-', s)
    return s


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Extract chapter images from TAW V1 epub.')
    parser.add_argument('--epub', type=Path, default=DEFAULT_EPUB, help='Path to the V1 epub.')
    parser.add_argument('--out', type=Path, default=DEFAULT_OUT, help='Output root folder for images.')
    parser.add_argument('--chapters', type=Path, help='Optional text file of chapter titles (one per line).')
    return parser.parse_args()


def load_chapters(path: Path | None) -> list[str]:
    if not path:
        return DEFAULT_CHAPTERS
    content = path.read_text(encoding='utf-8')
    lines = [line.strip() for line in content.splitlines()]
    return [line for line in lines if line and not line.startswith('#')]


def main() -> None:
    args = parse_args()
    epub = args.epub
    out_root = args.out
    chapters = load_chapters(args.chapters)

    folders = {title: slug(title) for title in chapters}
    for f in folders.values():
        (out_root / f).mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(epub) as z:
        nav = z.read('OEBPS/xhtml/00_Nav.xhtml').decode('utf-8', errors='ignore')

    pairs = re.findall(r'<a[^>]+href=\"([^\"]+)\"[^>]*>(.*?)</a>', nav, flags=re.I | re.S)
    title_to_href = {}
    for href, text in pairs:
        title = unescape(re.sub(r'<[^>]+>', ' ', text)).strip()
        if title in folders and title not in title_to_href:
            title_to_href[title] = href

    missing = [t for t in chapters if t not in title_to_href]
    if missing:
        print('Missing nav entries:', missing)

    with zipfile.ZipFile(epub) as z:
        for title in chapters:
            href = title_to_href.get(title)
            if not href:
                continue
            href_path = href.split('#')[0]
            if href_path.startswith('xhtml/'):
                href_path = 'OEBPS/' + href_path
            elif not href_path.startswith('OEBPS/'):
                href_path = 'OEBPS/xhtml/' + href_path.lstrip('./')

            try:
                html = z.read(href_path).decode('utf-8', errors='ignore')
            except KeyError:
                print('Missing file in epub:', href_path)
                continue

            imgs = re.findall(r'<img[^>]+src=\"([^\"]+)\"', html, flags=re.I)
            seen = set()
            ordered = []
            for src in imgs:
                src = src.split('#')[0]
                if src in seen:
                    continue
                seen.add(src)
                ordered.append(src)

            if not ordered:
                continue

            folder = out_root / folders[title]
            for i, src in enumerate(ordered, 1):
                img_path = src
                if img_path.startswith('../'):
                    img_path = img_path.replace('../', 'OEBPS/', 1)
                elif img_path.startswith('images/'):
                    img_path = 'OEBPS/' + img_path
                elif not img_path.startswith('OEBPS/'):
                    img_path = 'OEBPS/' + img_path.lstrip('./')

                try:
                    data = z.read(img_path)
                except KeyError:
                    print('Missing image in epub:', img_path)
                    continue

                out_name = f'TAW-V1-{folders[title]}-{i:02d}.jpg'
                (folder / out_name).write_bytes(data)

    print('Done')


if __name__ == '__main__':
    main()
