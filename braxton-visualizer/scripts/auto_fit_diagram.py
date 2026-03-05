import json
from pathlib import Path
from typing import List, Tuple

import cv2
import numpy as np
from skimage.morphology import skeletonize
import pytesseract

ROOT = Path('/Users/ben/src/braxton-visualizer')
IMG_PATH = ROOT / 'assets' / 'ta-w' / 'v1' / 'Introduction' / 'TAW-V1-Introduction-01.jpg'
JSON_PATH = ROOT / 'data' / 'ta-w' / 'v1' / 'diagrams' / 'Introduction' / 'TAW-V1-Introduction-01.json'
OUT_JSON = ROOT / 'data' / 'ta-w' / 'v1' / 'diagrams' / 'Introduction' / 'TAW-V1-Introduction-01.autofit.json'
OVERLAY_DIR = ROOT / 'assets' / 'ta-w' / 'v1' / 'overlays' / 'Introduction'
OUT_OVERLAY = OVERLAY_DIR / 'TAW-V1-Introduction-01-overlay-autofit.png'
OUT_CLEAN = Path('/tmp/TAW-V1-Introduction-01-clean.png')
OUT_SKEL = Path('/tmp/TAW-V1-Introduction-01-skeleton.png')
OUT_TEXT_MASK = Path('/tmp/TAW-V1-Introduction-01-textmask.png')
OUT_LABEL_MASK = Path('/tmp/TAW-V1-Introduction-01-labelmask.png')
OUT_OCR = Path('/tmp/TAW-V1-Introduction-01-ocr.png')
OUT_OCR_RAW = Path('/tmp/TAW-V1-Introduction-01-ocr-raw.png')
OUT_OCR_DEBUG = Path('/tmp/TAW-V1-Introduction-01-ocr-debug.json')
APPLY_SNAP = True


def preprocess(image: np.ndarray, label_boxes: List[Tuple[int, int, int, int]]) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    # adaptive threshold to preserve thin strokes
    adaptive = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        21,
        8,
    )
    # remove paper texture / noise with morphological opening
    kernel = np.ones((2, 2), np.uint8)
    denoised = cv2.morphologyEx(adaptive, cv2.MORPH_OPEN, kernel, iterations=1)
    # close small gaps in strokes
    closed = cv2.morphologyEx(denoised, cv2.MORPH_CLOSE, kernel, iterations=1)

    # OCR-based text mask (remove labels) at multiple scales
    text_mask = np.zeros_like(closed)
    for scale in (1.0, 2.0, 3.0):
        if scale != 1.0:
            scaled = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        else:
            scaled = gray
        data = pytesseract.image_to_data(
            scaled,
            output_type=pytesseract.Output.DICT,
            config='--oem 1 --psm 6'
        )
        n = len(data.get('text', []))
        for i in range(n):
            text = (data['text'][i] or '').strip()
            conf_val = data['conf'][i]
            try:
                conf = int(conf_val)
            except Exception:
                conf = -1
            if not text or conf < 10:
                continue
            x, y, w, h = data['left'][i], data['top'][i], data['width'][i], data['height'][i]
            # map back to original scale
            if scale != 1.0:
                x = int(x / scale)
                y = int(y / scale)
                w = int(w / scale)
                h = int(h / scale)
            # expand box a bit to cover dots/periods
            pad = 6
            x0, y0 = max(0, x - pad), max(0, y - pad)
            x1, y1 = min(text_mask.shape[1], x + w + pad), min(text_mask.shape[0], y + h + pad)
            text_mask[y0:y1, x0:x1] = 255

    # label mask based on known node positions
    label_mask = np.zeros_like(closed)
    for x0, y0, x1, y1 in label_boxes:
        label_mask[y0:y1, x0:x1] = 255

    # dilate text mask to ensure punctuation is removed
    text_mask = cv2.dilate(text_mask, np.ones((3, 3), np.uint8), iterations=1)
    combined_mask = cv2.bitwise_or(text_mask, label_mask)
    cv2.imwrite(str(OUT_TEXT_MASK), text_mask)
    cv2.imwrite(str(OUT_LABEL_MASK), label_mask)
    cleaned = cv2.bitwise_and(closed, cv2.bitwise_not(combined_mask))

    # remove remaining small components (likely label remnants)
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(cleaned, connectivity=8)
    filtered = np.zeros_like(cleaned)
    for i in range(1, num_labels):
        area = stats[i, cv2.CC_STAT_AREA]
        if area >= 80:
            filtered[labels == i] = 255
    return filtered


def preprocess_for_ocr(image: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    # adaptive threshold (binary) preserves thin strokes/dots
    adaptive = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        21,
        8,
    )
    # remove paper texture / noise
    kernel = np.ones((2, 2), np.uint8)
    denoised = cv2.morphologyEx(adaptive, cv2.MORPH_OPEN, kernel, iterations=1)
    # close small gaps in strokes
    closed = cv2.morphologyEx(denoised, cv2.MORPH_CLOSE, kernel, iterations=1)
    return closed


def normalize_label(value: str) -> str:
    return ''.join(ch.lower() for ch in value if ch.isalnum())


def whitelist_from_label(label: str) -> str:
    chars = {ch for ch in label if ch.isalnum() or ch in {'.', '-'}}
    # include both cases to help OCR
    extra = set()
    for ch in chars:
        if ch.isalpha():
            extra.add(ch.lower())
            extra.add(ch.upper())
    chars |= extra
    # always allow dot/hyphen
    chars |= {'.', '-'}
    return ''.join(sorted(chars))


def center_text_nodes(image: np.ndarray, data: dict) -> tuple[dict, set[str]]:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    centers = {}
    matched = set()
    ocr_img = preprocess_for_ocr(image)
    cv2.imwrite(str(OUT_OCR), ocr_img)
    cv2.imwrite(str(OUT_OCR_RAW), gray)

    def ocr_items_for(img: np.ndarray, roi=None, whitelist: str | None = None):
        if roi:
            x0, y0, x1, y1 = roi
            crop = img[y0:y1, x0:x1]
            if crop.size == 0:
                return []
            origin_x, origin_y = x0, y0
            ocr_img = crop
        else:
            origin_x, origin_y = 0, 0
            ocr_img = img
        config = '--oem 1 --psm 7'
        if whitelist:
            config += f' -c tessedit_char_whitelist={whitelist}'
        ocr_data = pytesseract.image_to_data(
            ocr_img,
            output_type=pytesseract.Output.DICT,
            config=config
        )
        items = []
        n = len(ocr_data.get('text', []))
        for i in range(n):
            text = (ocr_data['text'][i] or '').strip()
            try:
                conf = int(ocr_data['conf'][i])
            except Exception:
                conf = -1
            if not text or conf < 0:
                continue
            x, y, w, h = (
                ocr_data['left'][i],
                ocr_data['top'][i],
                ocr_data['width'][i],
                ocr_data['height'][i],
            )
            norm = normalize_label(text)
            if not norm:
                continue
            items.append({
                'text': text,
                'norm': norm,
                'cx': origin_x + x + w / 2,
                'cy': origin_y + y + h / 2,
            })
        return items

    def pick_match(label_norm: str, x: float, y: float, items: list[dict], max_dist: float):
        candidates = [item for item in items if item['norm'] == label_norm]
        if not candidates:
            return None
        best = min(
            candidates,
            key=lambda item: ((item['cx'] - x) ** 2 + (item['cy'] - y) ** 2),
        )
        dist = ((best['cx'] - x) ** 2 + (best['cy'] - y) ** 2) ** 0.5
        if dist > max_dist:
            return None
        return best

    debug = {}
    for node in data.get('nodes', []):
        if node.get('role') == 'junction':
            continue
        label = (node.get('label') or '').strip()
        if not label:
            continue

        label_norm = normalize_label(label)
        if not label_norm:
            continue

        x, y = float(node['x']), float(node['y'])
        roi_w = max(80, min(220, 12 * len(label_norm)))
        roi_h = 55
        x0 = max(0, int(x - roi_w / 2))
        y0 = max(0, int(y - roi_h / 2))
        x1 = min(gray.shape[1], int(x + roi_w / 2))
        y1 = min(gray.shape[0], int(y + roi_h / 2))
        roi = (x0, y0, x1, y1)

        wl = whitelist_from_label(label)
        ocr_items_raw = ocr_items_for(gray, roi=roi, whitelist=wl)
        ocr_items_proc = ocr_items_for(ocr_img, roi=roi, whitelist=wl)

        # prefer raw OCR on original image; fallback to preprocessed OCR
        best = pick_match(label_norm, x, y, ocr_items_raw, max_dist=30.0)
        if not best:
            best = pick_match(label_norm, x, y, ocr_items_proc, max_dist=30.0)
        if not best:
            debug[node['id']] = {
                'label': label,
                'label_norm': label_norm,
                'roi': [x0, y0, x1, y1],
                'raw': ocr_items_raw,
                'proc': ocr_items_proc,
            }
            continue
        centers[node['id']] = (float(best['cx']), float(best['cy']))
        matched.add(node['id'])

    if debug:
        OUT_OCR_DEBUG.write_text(json.dumps(debug, indent=2))

    # apply shifts
    for node in data.get('nodes', []):
        if node.get('id') in centers:
            node['x'], node['y'] = centers[node['id']]
    return data, matched


def skeleton_graph(cleaned: np.ndarray):
    # skeletonize expects boolean
    skel = skeletonize(cleaned > 0)
    skel_uint8 = (skel * 255).astype(np.uint8)
    # find skeleton pixels
    ys, xs = np.where(skel)
    points = set(zip(xs, ys))

    def neighbors(x, y):
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                nx, ny = x + dx, y + dy
                if (nx, ny) in points:
                    yield nx, ny

    junctions = []
    endpoints = []
    for x, y in points:
        degree = sum(1 for _ in neighbors(x, y))
        if degree == 1:
            endpoints.append((x, y))
        elif degree >= 3:
            junctions.append((x, y))

    return skel_uint8, junctions, endpoints, points


def nearest_point(px, py, points, max_dist):
    best = None
    best_dist = max_dist
    for x, y in points:
        dist = ((px - x) ** 2 + (py - y) ** 2) ** 0.5
        if dist < best_dist:
            best = (x, y)
            best_dist = dist
    return best, best_dist


def nearest_point_to_origin(points, origin, max_dist):
    ox, oy = origin
    best = None
    best_dist = max_dist
    for x, y in points:
        dist = ((ox - x) ** 2 + (oy - y) ** 2) ** 0.5
        if dist < best_dist:
            best = (x, y)
            best_dist = dist
    return best, best_dist


def load_lines(image: np.ndarray, label_boxes: List[Tuple[int, int, int, int]]) -> List[Tuple[int, int, int, int]]:
    cleaned = preprocess(image, label_boxes)
    edges = cv2.Canny(cleaned, 40, 120, apertureSize=3)
    lines = cv2.HoughLinesP(
        edges,
        rho=1,
        theta=np.pi / 180,
        threshold=50,
        minLineLength=25,
        maxLineGap=8,
    )
    if lines is None:
        return []
    return [tuple(line[0]) for line in lines]


def closest_point_on_segment(px, py, x1, y1, x2, y2):
    vx, vy = x2 - x1, y2 - y1
    wx, wy = px - x1, py - y1
    c1 = vx * wx + vy * wy
    if c1 <= 0:
        return x1, y1
    c2 = vx * vx + vy * vy
    if c2 <= c1:
        return x2, y2
    t = c1 / c2
    return x1 + t * vx, y1 + t * vy


def snap_point(px, py, segments, max_dist):
    best = None
    best_dist = max_dist
    for x1, y1, x2, y2 in segments:
        cx, cy = closest_point_on_segment(px, py, x1, y1, x2, y2)
        dist = ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5
        if dist < best_dist:
            best = (cx, cy)
            best_dist = dist
    return best, best_dist


def segment_intersection(a, b, c, d):
    # line segments AB and CD intersection (if any), returns point or None
    ax, ay = a
    bx, by = b
    cx, cy = c
    dx, dy = d
    denom = (ax - bx) * (cy - dy) - (ay - by) * (cx - dx)
    if abs(denom) < 1e-6:
        return None
    px = ((ax * by - ay * bx) * (cx - dx) - (ax - bx) * (cx * dy - cy * dx)) / denom
    py = ((ax * by - ay * bx) * (cy - dy) - (ay - by) * (cx * dy - cy * dx)) / denom
    # check within segment bounds with tolerance
    if (min(ax, bx) - 2 <= px <= max(ax, bx) + 2 and
            min(ay, by) - 2 <= py <= max(ay, by) + 2 and
            min(cx, dx) - 2 <= px <= max(cx, dx) + 2 and
            min(cy, dy) - 2 <= py <= max(cy, dy) + 2):
        return px, py
    return None


def find_intersections(segments: List[Tuple[int, int, int, int]]) -> List[Tuple[float, float]]:
    points = []
    for i in range(len(segments)):
        x1, y1, x2, y2 = segments[i]
        for j in range(i + 1, len(segments)):
            x3, y3, x4, y4 = segments[j]
            inter = segment_intersection((x1, y1), (x2, y2), (x3, y3), (x4, y4))
            if inter:
                points.append(inter)
    return points


def render_overlay(image: np.ndarray, data: dict, out_path: Path):
    overlay = image.copy()
    # draw edges
    for edge in data['edges']:
        n_from = next(n for n in data['nodes'] if n['id'] == edge['from'])
        n_to = next(n for n in data['nodes'] if n['id'] == edge['to'])
        cv2.line(
            overlay,
            (int(n_from['x']), int(n_from['y'])),
            (int(n_to['x']), int(n_to['y'])),
            (255, 120, 0),
            2,
        )
    # draw nodes
    for node in data['nodes']:
        x, y = int(node['x']), int(node['y'])
        if node.get('role') == 'junction':
            cv2.circle(overlay, (x, y), 3, (0, 0, 255), -1)
        else:
            cv2.circle(overlay, (x, y), 6, (255, 120, 0), 2)
    cv2.imwrite(str(out_path), overlay)


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Auto-fit diagram nodes to lines/labels.')
    parser.add_argument('--image', type=Path, default=IMG_PATH, help='Path to the diagram image.')
    parser.add_argument('--diagram', type=Path, default=JSON_PATH, help='Path to the diagram JSON.')
    parser.add_argument('--out', type=Path, default=OUT_JSON, help='Output JSON path.')
    parser.add_argument('--overlay-out', type=Path, default=OUT_OVERLAY, help='Overlay output image path.')
    parser.add_argument('--snap', dest='snap', action='store_true', help='Enable line snapping for OCR-matched labels.')
    parser.add_argument('--no-snap', dest='snap', action='store_false', help='Disable line snapping.')
    parser.set_defaults(snap=APPLY_SNAP)
    args = parser.parse_args()
    image = cv2.imread(str(args.image))
    if image is None:
        raise SystemExit(f'Failed to read image: {args.image}')

    data = json.loads(args.diagram.read_text())
    data, ocr_matched = center_text_nodes(image, data)
    # approximate label boxes from node positions
    label_boxes = []
    for node in data['nodes']:
        if node.get('role') == 'junction':
            continue
        x, y = int(node['x']), int(node['y'])
        # box width based on label length, with extra pad
        label = node.get('label', '')
        width = max(40, min(180, 8 * len(label)))
        height = 24
        x0 = max(0, x - width // 2)
        y0 = max(0, y - height // 2)
        x1 = min(image.shape[1], x + width // 2)
        y1 = min(image.shape[0], y + height // 2)
        label_boxes.append((x0, y0, x1, y1))

    cleaned = preprocess(image, label_boxes)
    cv2.imwrite(str(OUT_CLEAN), cleaned)
    segments = load_lines(image, label_boxes)
    if not segments:
        raise SystemExit('No line segments detected; check Canny/Hough params.')

    skel, junctions, endpoints, skel_points = skeleton_graph(cleaned)
    cv2.imwrite(str(OUT_SKEL), skel)
    intersections = find_intersections(segments)
    max_dist = 40.0
    total = 0
    moved = 0
    distances = []
    print(f'junctions: {len(junctions)}, endpoints: {len(endpoints)}, skel_points: {len(skel_points)}')

    # Build quick index for nodes
    node_map = {n['id']: n for n in data['nodes']}
    junction_ids = {n['id'] for n in data['nodes'] if n.get('role') == 'junction'}

    if args.snap:
        # Only snap OCR-matched labels (skip junctions entirely).
        total = 0

    # Then snap non-junctions to endpoints near their connected junction
    # Build adjacency from edges
    adjacency = {}
    for edge in data['edges']:
        adjacency.setdefault(edge['from'], set()).add(edge['to'])
        adjacency.setdefault(edge['to'], set()).add(edge['from'])

    if APPLY_SNAP:
        for node in data['nodes']:
            if node.get('role') == 'junction':
                continue
            if node['id'] not in ocr_matched:
                # Skip snapping if OCR didn't anchor this label.
                continue
            px, py = node['x'], node['y']
            # find connected junction (if any)
            neighbors = adjacency.get(node['id'], set())
            junction_neighbor = None
            for nid in neighbors:
                if nid in junction_ids:
                    junction_neighbor = node_map[nid]
                    break
            if junction_neighbor:
                origin = (junction_neighbor['x'], junction_neighbor['y'])
                snap, dist = nearest_point_to_origin(endpoints, origin, max_dist)
            else:
                snap, dist = nearest_point(px, py, endpoints or skel_points, max_dist)
            if snap:
                node['x'], node['y'] = float(snap[0]), float(snap[1])
                moved += 1
                distances.append(dist)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.overlay_out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, indent=2))
    render_overlay(image, data, args.overlay_out)

    avg = sum(distances) / len(distances) if distances else 0.0
    print(f'moved {moved}/{total}, avg snap dist: {avg:.2f}px')
    print(f'json: {args.out}')
    print(f'overlay: {args.overlay_out}')


if __name__ == '__main__':
    main()
