#!/usr/bin/env python3
import json
from pathlib import Path


BASE_DIR = Path('/Users/ben/src/braxton-visualizer')
DIAGRAM_DIR = BASE_DIR / 'data/ta-w/v1/diagrams'
GLOSS_INT = BASE_DIR / 'data/ta-w/v1/glossary_integration.json'
GLOSS = BASE_DIR / 'data/ta-w/v1/glossary.json'


def normalize(term: str) -> str:
    return term.strip().lower()


def main() -> None:
    code_to_exp = {
        e['code']: e['expansion']
        for e in json.loads(GLOSS_INT.read_text())['entries']
    }
    terms = {
        e['term'].lower(): e['definition']
        for e in json.loads(GLOSS.read_text())['entries']
    }

    for path in sorted(DIAGRAM_DIR.rglob('*.json')):
        if path.name.endswith('.missing.json'):
            continue
        data = json.loads(path.read_text())
        missing = []
        for node in data.get('nodes', []):
            if node.get('role') == 'junction':
                continue
            code = node.get('glossaryCode') or ''
            label = node.get('label')
            exp = code_to_exp.get(code) if code else None
            if not exp:
                missing.append({
                    'nodeId': node.get('id'),
                    'label': label,
                    'issue': 'missing_expansion'
                })
            else:
                if normalize(exp) not in terms:
                    missing.append({
                        'nodeId': node.get('id'),
                        'label': label,
                        'expansion': exp,
                        'issue': 'missing_definition'
                    })

        out = path.with_suffix('.missing.json')
        out.write_text(json.dumps({'diagram': data.get('id'), 'missing': missing}, indent=2))
        print(out)


if __name__ == '__main__':
    main()
