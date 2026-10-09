import { mkdir, copyFile } from 'node:fs/promises';
await mkdir('atlas/web/vendor', { recursive: true });
for (const [source, target] of [
  ['cytoscape/dist/cytoscape.min.js', 'cytoscape.min.js'],
  // cytoscape-dagre 4 bundles @dagrejs/dagre (MIT, same licence text as dagre-LICENSE).
  ['cytoscape-dagre/dist/cytoscape-dagre.min.js', 'cytoscape-dagre.js'],
  ['cytoscape/LICENSE', 'cytoscape-LICENSE'],
  ['cytoscape-dagre/LICENSE', 'cytoscape-dagre-LICENSE'],
]) await copyFile(`node_modules/${source}`, `atlas/web/vendor/${target}`);
