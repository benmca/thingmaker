/* global p5 */
let diagram = null;
let rawGlossaryIntegration = null;
let rawGlossary = null;
let glossaryIntegration = {};
let glossary = {};

let hoveredNode = null;
let selectedNode = null;
let originalPositions = new Map();
// no overlay/edit tools in base prototype
let overlayImg = null;
let overlayAutofitImg = null;
let manifest = null;
let currentImagePath = 'assets/ta-w/v1/Introduction/TAW-V1-Introduction-01.jpg';
let currentDiagramPath = 'data/ta-w/v1/diagrams/Introduction/TAW-V1-Introduction-01.json';
let currentAutofitPath = 'assets/ta-w/v1/overlays/Introduction/TAW-V1-Introduction-01-overlay-autofit.png';
let draggingNode = null;
let dragOffset = { x: 0, y: 0 };

const config = {
  nodeRadius: 28,
  padding: 40
};

function preload() {
  diagram = loadJSON(currentDiagramPath);
  rawGlossaryIntegration = loadJSON('data/ta-w/v1/glossary_integration.json');
  rawGlossary = loadJSON('data/ta-w/v1/glossary.json');
  overlayImg = loadImage(currentImagePath);
  overlayAutofitImg = loadImage(currentAutofitPath);
}

function setup() {
  const wrap = document.querySelector('.canvas-wrap');
  const canvas = createCanvas(Math.max(520, wrap.clientWidth), 680);
  canvas.parent(wrap);
  document.getElementById('diagram-title').textContent = diagram.title;

  glossaryIntegration = indexGlossaryIntegration(rawGlossaryIntegration);
  glossary = indexGlossary(rawGlossary);
  loadManifest();

  const toggleLabels = document.getElementById('toggle-labels');
  const toggleExpand = document.getElementById('toggle-expand');
  const toggleLayout = document.getElementById('toggle-layout');
  const toggleOverlay = document.getElementById('toggle-overlay');
  const toggleAutofit = document.getElementById('toggle-autofit');
  const toggleWide = document.getElementById('toggle-wide');
  const toggleGhost = document.getElementById('toggle-ghost');
  const toggleEdit = document.getElementById('toggle-edit');
  const exportBtn = document.getElementById('export-json');
  toggleLabels.addEventListener('change', () => redraw());
  toggleExpand.addEventListener('change', () => redraw());
  toggleLayout.addEventListener('change', () => {
    applyLayout();
    redraw();
  });
  toggleOverlay.addEventListener('change', () => redraw());
  toggleAutofit.addEventListener('change', () => redraw());
  toggleWide.addEventListener('change', () => redraw());
  toggleGhost.addEventListener('change', () => redraw());
  toggleEdit.addEventListener('change', () => {
    draggingNode = null;
    redraw();
  });
  exportBtn.addEventListener('click', exportDiagramJson);
  cacheOriginalPositions();
  loop();
}

function loadManifest() {
  fetch('data/ta-w/v1/manifest.json')
    .then((res) => res.json())
    .then((data) => {
      manifest = data;
      buildNavTree();
    })
    .catch(() => {
      const nav = document.getElementById('nav-tree');
      if (nav) nav.textContent = 'Failed to load manifest.';
    });
}

function buildNavTree() {
  const nav = document.getElementById('nav-tree');
  if (!nav || !manifest) return;
  nav.innerHTML = '';
  manifest.chapters.forEach((chapter) => {
    const details = document.createElement('details');
    const summary = document.createElement('summary');
    summary.textContent = chapter.title;
    details.appendChild(summary);

    chapter.items.forEach((item) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.textContent = item.label;
      btn.addEventListener('click', () => {
        loadDiagram(chapter, item);
      });
      details.appendChild(btn);
    });

    nav.appendChild(details);
  });
}

function loadDiagram(chapter, item) {
  currentImagePath = item.path;
  currentDiagramPath = `data/ta-w/v1/diagrams/${chapter.slug}/${item.base}.json`;
  currentAutofitPath = `assets/ta-w/v1/overlays/${chapter.slug}/${item.base}-overlay-autofit.png`;

  overlayImg = loadImage(currentImagePath, () => redraw());
  overlayAutofitImg = loadImage(currentAutofitPath, () => redraw());

  fetch(currentDiagramPath)
    .then((res) => {
      if (!res.ok) throw new Error('missing');
      return res.json();
    })
    .then((data) => {
      diagram = data;
      document.getElementById('diagram-title').textContent = diagram.title || item.base;
      cacheOriginalPositions();
      redraw();
    })
    .catch(() => {
      diagram = { id: item.base, title: item.base, nodes: [], edges: [] };
      document.getElementById('diagram-title').textContent = item.base;
      redraw();
    });
}

function windowResized() {
  const wrap = document.querySelector('.canvas-wrap');
  const width = Math.max(520, wrap.clientWidth);
  resizeCanvas(width, 680);
  applyLayout();
  redraw();
}

function draw() {
  background('#faf8f4');
  drawOverlay();
  drawAutofitOverlay();
  drawNodes();
  updateHover();
}

function drawSubjectArrow() {
  if (!diagram.subject || !diagram.subject.arrowFrom) return;
  const target = findNode(diagram.subject.nodeId);
  if (!target) return;

  stroke('#1e3a2b');
  strokeWeight(2);
  fill('#1e3a2b');

  const from = diagram.subject.arrowFrom;
  const to = { x: target.x - config.nodeRadius - 6, y: target.y };

  line(from.x, from.y, to.x, to.y);

  const angle = Math.atan2(to.y - from.y, to.x - from.x);
  const arrowSize = 8;
  const arrowX1 = to.x - arrowSize * Math.cos(angle - Math.PI / 6);
  const arrowY1 = to.y - arrowSize * Math.sin(angle - Math.PI / 6);
  const arrowX2 = to.x - arrowSize * Math.cos(angle + Math.PI / 6);
  const arrowY2 = to.y - arrowSize * Math.sin(angle + Math.PI / 6);

  triangle(to.x, to.y, arrowX1, arrowY1, arrowX2, arrowY2);
  noStroke();
}

function drawEdges() {
  const showLabels = document.getElementById('toggle-labels').checked;
  stroke('#7b756c');
  strokeWeight(2);
  fill('#3c3a36');
  textSize(12);
  textAlign(CENTER, CENTER);

  for (const edge of diagram.edges) {
    const from = findNode(edge.from);
    const to = findNode(edge.to);
    if (!from || !to) continue;

    line(from.x, from.y, to.x, to.y);

    if (showLabels && edge.relation) {
      const midX = (from.x + to.x) / 2;
      const midY = (from.y + to.y) / 2 - 14;
      noStroke();
      fill('#55514b');
      text(edge.relation, midX, midY);
      stroke('#7b756c');
    }
  }
}

function drawNodes() {
  const expand = document.getElementById('toggle-expand').checked;
  const wide = document.getElementById('toggle-wide').checked;
  const ghost = document.getElementById('toggle-ghost').checked;
  textAlign(CENTER, CENTER);
  textSize(12);

  for (const node of diagram.nodes) {
    if (node.role === 'junction') continue;
    const isHovered = hoveredNode && hoveredNode.id === node.id;
    const isSelected = selectedNode && selectedNode.id === node.id;
    const color = node.role === 'subject' ? '#1e3a2b' : '#2c2a27';
    const match = getMatchStatus(node);

    stroke(match.status === 'ok' ? color : '#b7412e');
    strokeWeight(isHovered || isSelected ? 3 : 2);
    if (ghost) {
      noFill();
    } else {
      fill(isHovered || isSelected ? '#efe7da' : '#fffaf2');
    }
    const diameter = config.nodeRadius * 2;
    const ellipseWidth = wide ? diameter * 2 : diameter;
    ellipse(node.x, node.y, ellipseWidth, diameter);

    noStroke();
    fill(color);
    if (!ghost) {
      const label = expand && node.fullLabel ? node.fullLabel : node.label;
      const tags = node.tags && node.tags.length ? `(${node.tags.join(',')}) ` : '';
      text(tags + label, node.x, node.y);
    }
  }
}

function drawOverlay() {
  const showOverlay = document.getElementById('toggle-overlay').checked;
  if (!showOverlay || !overlayImg) return;
  push();
  tint(255, 120);
  image(overlayImg, 0, 0);
  pop();
}

function drawAutofitOverlay() {
  const showOverlay = document.getElementById('toggle-autofit').checked;
  if (!showOverlay || !overlayAutofitImg) return;
  push();
  tint(255, 160);
  image(overlayAutofitImg, 0, 0);
  pop();
}


function updateHover() {
  const tooltip = document.getElementById('tooltip');
  hoveredNode = null;

  for (const node of diagram.nodes) {
    if (node.role === 'junction') continue;
    if (dist(mouseX, mouseY, node.x, node.y) <= config.nodeRadius) {
      hoveredNode = node;
      break;
    }
  }

  if (!hoveredNode) {
    tooltip.hidden = true;
    return;
  }

  const match = getMatchStatus(hoveredNode);
  const expansion = formatExpansion(match.expansion);
  const definition = match.definition;

  tooltip.innerHTML = `
    <div><strong>${hoveredNode.label}</strong>${expansion ? ` — ${expansion}` : ''}</div>
    ${match.status === 'missing_expansion' ? '<div style="margin-top:6px;color:#b7412e;">Missing glossary expansion</div>' : ''}
    ${match.status === 'missing_definition' ? '<div style="margin-top:6px;color:#b7412e;">Missing glossary definition</div>' : ''}
    ${definition ? '<div style="margin-top:6px;color:#5d5a55;">Click for full definition</div>' : ''}
  `;
  tooltip.style.left = `${mouseX + 14}px`;
  tooltip.style.top = `${mouseY + 14}px`;
  tooltip.hidden = false;
}

function mousePressed() {
  if (document.getElementById('toggle-edit').checked) {
    const hit = findHitNode(mouseX, mouseY);
    if (hit) {
      draggingNode = hit;
      dragOffset.x = mouseX - hit.x;
      dragOffset.y = mouseY - hit.y;
      return;
    }
  }
  const hit = findHitNode(mouseX, mouseY);
  if (!hit) return;
  selectedNode = hit;

  const match = getMatchStatus(selectedNode);
  const expansion = match.expansion;
  const definition = match.definition;
  const more = document.getElementById('more');
  const moreBody = document.getElementById('more-body');

  if (definition) {
    moreBody.textContent = definition;
    more.hidden = false;
  } else {
    const message = match.status === 'missing_expansion'
      ? 'No glossary expansion found for this abbreviation yet.'
      : 'No glossary definition found for this term yet.';
    moreBody.textContent = message;
    more.hidden = false;
  }

  redraw();
}

function mouseMoved() {
  // Ensure hover state updates immediately on pointer movement.
  redraw();
}

function mouseDragged() {
  if (!draggingNode) return;
  draggingNode.x = mouseX - dragOffset.x;
  draggingNode.y = mouseY - dragOffset.y;
  redraw();
}

function mouseReleased() {
  draggingNode = null;
}

function findNode(id) {
  return diagram.nodes.find((node) => node.id === id);
}

function findHitNode(x, y) {
  for (const node of diagram.nodes) {
    if (node.role === 'junction') continue;
    if (dist(x, y, node.x, node.y) <= config.nodeRadius) {
      return node;
    }
  }
  return null;
}

function indexGlossaryIntegration(data) {
  const out = {};
  if (!data || !data.entries) return out;
  for (const item of data.entries) {
    if (!item.code) continue;
    out[item.code.trim().toUpperCase()] = item.expansion;
  }
  return out;
}

function indexGlossary(data) {
  const out = {};
  if (!data || !data.entries) return out;
  for (const item of data.entries) {
    if (!item.term) continue;
    out[item.term.trim().toLowerCase()] = item.definition;
  }
  return out;
}

function normalizeTerm(value) {
  if (!value) return '';
  return value
    .trim()
    .replace(/\s+\(\d+\)\s*$/, '')
    .toLowerCase();
}

function getExpansion(node) {
  if (!node) return '';
  const code = node.glossaryCode || node.label || '';
  return glossaryIntegration[code.trim().toUpperCase()] || '';
}

function getDefinition(node, expansion) {
  const candidates = [
    node.glossaryTerm,
    expansion,
    node.fullLabel,
    node.label
  ];
  for (const candidate of candidates) {
    const key = normalizeTerm(candidate);
    if (key && glossary[key]) return glossary[key];
  }
  return '';
}

function getMatchStatus(node) {
  if (!node || node.role === 'junction') {
    return { status: 'ok', expansion: '', definition: '' };
  }
  const expansion = getExpansion(node);
  if (!expansion) {
    return { status: 'missing_expansion', expansion: '', definition: '' };
  }
  const definition = getDefinition(node, expansion);
  if (!definition) {
    return { status: 'missing_definition', expansion, definition: '' };
  }
  return { status: 'ok', expansion, definition };
}

function formatExpansion(expansion) {
  if (!expansion) return '';
  return expansion.charAt(0).toUpperCase() + expansion.slice(1);
}

function cacheOriginalPositions() {
  originalPositions.clear();
  for (const node of diagram.nodes) {
    originalPositions.set(node.id, { x: node.x, y: node.y });
  }
}

function applyLayout() {
  const auto = document.getElementById('toggle-layout').checked;
  if (!auto) {
    for (const node of diagram.nodes) {
      const original = originalPositions.get(node.id);
      if (original) {
        node.x = original.x;
        node.y = original.y;
      }
    }
    return;
  }

  const depth = new Map();
  const subjectId = diagram.subject?.nodeId;
  if (!subjectId) return;

  depth.set(subjectId, 0);
  let updated = true;
  while (updated) {
    updated = false;
    for (const edge of diagram.edges) {
      const fromDepth = depth.get(edge.from);
      if (fromDepth !== undefined) {
        const nextDepth = fromDepth + 1;
        if (depth.get(edge.to) === undefined || depth.get(edge.to) < nextDepth) {
          depth.set(edge.to, nextDepth);
          updated = true;
        }
      }
    }
  }

  const maxDepth = Math.max(...Array.from(depth.values()));
  const availableWidth = width - config.padding * 2;
  const columnWidth = maxDepth > 0 ? availableWidth / maxDepth : availableWidth;

  const columns = new Map();
  for (const node of diagram.nodes) {
    const d = depth.get(node.id) ?? 0;
    if (!columns.has(d)) columns.set(d, []);
    columns.get(d).push(node);
  }

  for (const [d, nodes] of columns.entries()) {
    const columnX = config.padding + d * columnWidth;
    nodes.sort((a, b) => (a.y ?? 0) - (b.y ?? 0));
    const rowSpacing = (height - config.padding * 2) / (nodes.length + 1);
    nodes.forEach((node, index) => {
      node.x = columnX;
      node.y = config.padding + rowSpacing * (index + 1);
    });
  }
}

function exportDiagramJson() {
  const content = JSON.stringify(diagram, null, 2);
  const blob = new Blob([content], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `${diagram.id}.json`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
