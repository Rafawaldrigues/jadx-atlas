import { mkdir, copyFile } from 'node:fs/promises';
await mkdir('web/vendor', { recursive: true });
for (const [source, target] of [
  ['cytoscape/dist/cytoscape.min.js', 'cytoscape.min.js'],
  ['dagre/dist/dagre.min.js', 'dagre.min.js'],
  ['cytoscape-dagre/cytoscape-dagre.js', 'cytoscape-dagre.js'],
  ['cytoscape/LICENSE', 'cytoscape-LICENSE'],
  ['dagre/LICENSE', 'dagre-LICENSE'],
  ['cytoscape-dagre/LICENSE', 'cytoscape-dagre-LICENSE'],
]) await copyFile(`node_modules/${source}`, `web/vendor/${target}`);
