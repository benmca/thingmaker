from pathlib import Path
import json
from PIL import Image, ImageDraw

root = Path('/Users/ben/src/braxton-visualizer')
img_path = root / 'assets' / 'ta-w' / 'v1' / 'Introduction' / 'TAW-V1-Introduction-01.jpg'
json_path = root / 'data' / 'ta-w' / 'v1' / 'diagrams' / 'Introduction' / 'TAW-V1-Introduction-01.json'

img = Image.open(img_path).convert('RGBA')
data = json.loads(json_path.read_text())

nodes = {n['id']: n for n in data['nodes']}

# overlay drawing
overlay = Image.new('RGBA', img.size, (0, 0, 0, 0))
draw = ImageDraw.Draw(overlay)

# draw edges
for edge in data['edges']:
    n_from = nodes[edge['from']]
    n_to = nodes[edge['to']]
    x1, y1 = n_from['x'], n_from['y']
    x2, y2 = n_to['x'], n_to['y']
    draw.line((x1, y1, x2, y2), fill=(0, 90, 255, 160), width=2)

# draw junctions and nodes
for node in data['nodes']:
    x, y = node['x'], node['y']
    if node.get('role') == 'junction':
        r = 3
        draw.ellipse((x - r, y - r, x + r, y + r), fill=(255, 0, 0, 200))
    else:
        r = 6
        draw.ellipse((x - r, y - r, x + r, y + r), outline=(0, 90, 255, 200), width=2)

out = Image.alpha_composite(img, overlay)

out_path = Path('/tmp/TAW-V1-Introduction-01-overlay.png')
out.save(out_path)
print(out_path)
