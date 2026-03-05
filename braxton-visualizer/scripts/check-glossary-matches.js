const fs = require('fs');
const path = require('path');

const diagramPath = path.join(__dirname, '..', 'data', 'ta-w', 'v1', 'diagrams', 'Introduction', 'TAW-V1-Introduction-01.json');
const giPath = path.join(__dirname, '..', 'data', 'ta-w', 'v1', 'glossary_integration.json');
const gPath = path.join(__dirname, '..', 'data', 'ta-w', 'v1', 'glossary.json');
const outPath = path.join(__dirname, '..', 'data', 'ta-w', 'v1', 'diagrams', 'Introduction', 'TAW-V1-Introduction-01.missing.json');

function loadJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, 'utf8'));
}

function normalizeTerm(value) {
  if (!value) return '';
  return value
    .trim()
    .replace(/\s+\(\d+\)\s*$/, '')
    .toLowerCase();
}

const diagram = loadJson(diagramPath);
const gi = loadJson(giPath);
const g = loadJson(gPath);

const expansions = {};
for (const item of gi.entries || []) {
  if (!item.code) continue;
  expansions[item.code.trim().toUpperCase()] = item.expansion;
}

const definitions = {};
for (const item of g.entries || []) {
  if (!item.term) continue;
  definitions[normalizeTerm(item.term)] = item.definition;
}

const missing = [];

for (const node of diagram.nodes) {
  const code = (node.glossaryCode || node.label || '').trim().toUpperCase();
  const expansion = expansions[code];
  if (!expansion) {
    missing.push({
      nodeId: node.id,
      label: node.label,
      glossaryCode: node.glossaryCode,
      issue: 'missing_expansion'
    });
  }

  const term = node.glossaryTerm || expansion;
  const key = normalizeTerm(term);
  if (!definitions[key]) {
    missing.push({
      nodeId: node.id,
      label: node.label,
      glossaryCode: node.glossaryCode,
      glossaryTerm: node.glossaryTerm,
      expansion,
      issue: 'missing_definition'
    });
  }
}

fs.writeFileSync(outPath, JSON.stringify({ diagram: diagram.id, missing }, null, 2));

console.log(`Missing items: ${missing.length}`);
console.log(`Report: ${outPath}`);
