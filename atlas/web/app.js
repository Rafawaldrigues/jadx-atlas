'use strict';

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const make = (tag, className, text) => {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (text !== undefined) el.textContent = text;
  return el;
};
const LIMIT = 600;
const state = { project: null, nodes: new Map(), incoming: new Map(), outgoing: new Map(), selected: null,
  kind: 'all', query: '', mode: 'all', history: [], listLimit: 200, revision: -1,
  source: '', sourceLine: 1, sourceRequest: 0, busy: false, collapsed: new Set(), fullHierarchy: false };
let cy;
let toastTimer;

function toast(message) {
  $('#toast').textContent = message;
  $('#toast').hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $('#toast').hidden = true; }, 4500);
}

async function api(path, body) {
  const response = await fetch(path, body === undefined ? undefined : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'Não foi possível concluir a operação.');
  return result;
}

function icon(node) {
  return make('span', `type-icon ${node.kind} ${node.external ? 'external' : ''}`,
    node.external ? '↗' : ({ class: 'C', interface: 'I', enum: 'E', record: 'R', annotation: '@' }[node.kind] || 'C'));
}

function kindLabel(node) {
  return node.external ? 'referência externa' : ({ class: 'classe', interface: 'interface', enum: 'enum', record: 'record', annotation: 'anotação' }[node.kind]);
}

async function loadProject() {
  const project = await api('/api/project');
  if (!project) return;
  state.project = project;
  state.nodes = new Map(project.nodes.map(n => [n.id, n]));
  state.incoming = new Map(); state.outgoing = new Map();
  for (const edge of project.edges) {
    if (!state.incoming.has(edge.target)) state.incoming.set(edge.target, []);
    if (!state.outgoing.has(edge.source)) state.outgoing.set(edge.source, []);
    state.incoming.get(edge.target).push(edge);
    state.outgoing.get(edge.source).push(edge);
  }
  state.history = []; state.collapsed.clear(); state.listLimit = 200;
  state.query = ''; state.kind = 'all'; $('#search').value = '';
  $$('[data-kind]').forEach(button => button.classList.toggle('active', button.dataset.kind === 'all'));
  state.selected = project.nodes.find(n => n.name === 'PedidoActivity' && !n.external)?.id
    || [...project.nodes].filter(n => !n.external).sort((a, b) => degree(b.id) - degree(a.id))[0]?.id;
  $('#project-name').textContent = project.name;
  $('#project-name').title = project.root;
  $('#demo-badge').hidden = !project.demo;
  $('#file-count').textContent = `${project.stats.files.toLocaleString('pt-BR')} arquivos`;
  for (const key of ['types', 'relations', 'packages']) $('#stat-' + key).textContent = project.stats[key].toLocaleString('pt-BR');
  $('#stat-warnings').textContent = project.warnings.length.toLocaleString('pt-BR');
  $('#show-warnings').classList.toggle('has-warnings', project.warnings.length > 0);
  $('#analysis-info').textContent = `Indexado em ${project.stats.seconds.toLocaleString('pt-BR')} s · ${project.stats.external} referências externas`;
  $('#project-path').value = project.demo ? '' : project.root;
  const filter = $('#package-filter');
  filter.replaceChildren(new Option('Todos os pacotes', ''));
  [...new Set(project.nodes.filter(n => !n.external).map(n => n.package))].sort().forEach(p => filter.add(new Option(p || '(sem pacote)', p || '__default')));
  resetFilters(false);
  renderList(); renderDetails(); renderGraph();
  $('#back').disabled = true;
}

function degree(id) { return (state.incoming.get(id)?.length || 0) + (state.outgoing.get(id)?.length || 0); }

function ancestryOf(start = state.selected) {
  const visited = new Set(start ? [start] : []), queue = start ? [{ id: start, depth: 0 }] : [];
  const entries = [];
  while (queue.length) {
    const current = queue.shift();
    for (const edge of state.outgoing.get(current.id) || []) {
      const depth = current.depth + 1;
      entries.push({ id: edge.target, depth, kind: edge.kind, direct: depth === 1, edge });
      if (!visited.has(edge.target)) {
        visited.add(edge.target);
        queue.push({ id: edge.target, depth });
      }
    }
  }
  return entries;
}

function rootClassOf(start = state.selected) {
  let current = start;
  const visited = new Set();
  while (current && !visited.has(current)) {
    visited.add(current);
    const parent = (state.outgoing.get(current) || []).find(edge => {
      const target = state.nodes.get(edge.target);
      return edge.kind === 'extends' && target && !['interface', 'annotation'].includes(target.kind);
    });
    if (!parent) return current;
    current = parent.target;
  }
  return current || start;
}

function renderList() {
  const root = $('#class-list');
  const oldScroll = root.scrollTop;
  root.replaceChildren();
  if (!state.project) return;
  const matches = state.project.nodes.filter(n => !n.external &&
    (state.kind === 'all' || (state.kind === 'interface' ? ['interface', 'annotation'].includes(n.kind) : !['interface', 'annotation'].includes(n.kind))) &&
    `${n.id} ${n.path}`.toLowerCase().includes(state.query));
  matches.sort((a, b) => a.id.localeCompare(b.id));
  $('#search-count').textContent = matches.length.toLocaleString('pt-BR');
  if (!matches.length) {
    root.append(make('div', 'no-results', 'Nenhuma declaração encontrada. Tente outro nome ou pacote.'));
    return;
  }
  const groups = new Map();
  for (const node of matches.slice(0, state.listLimit)) {
    if (!groups.has(node.package)) groups.set(node.package, []);
    groups.get(node.package).push(node);
  }
  for (const [pkg, nodes] of groups) {
    const group = make('details', 'package-group');
    group.open = Boolean(state.query) || !state.collapsed.has(pkg);
    const summary = make('summary');
    summary.title = pkg || '(sem pacote)';
    summary.append(make('span', '', '▱'), make('span', 'package-label', pkg || '(sem pacote)'), make('small', '', nodes.length));
    group.append(summary);
    group.addEventListener('toggle', () => { if (group.open) state.collapsed.delete(pkg); else state.collapsed.add(pkg); });
    for (const node of nodes) {
      const button = make('button', `class-row${node.id === state.selected ? ' selected' : ''}`);
      button.dataset.id = node.id;
      button.title = node.id;
      button.setAttribute('aria-pressed', String(node.id === state.selected));
      const local = node.package ? node.id.slice(node.package.length + 1) : node.id;
      button.append(icon(node), make('span', 'class-name', local));
      button.addEventListener('click', () => select(node.id));
      group.append(button);
    }
    root.append(group);
  }
  if (matches.length > state.listLimit) {
    const more = make('button', 'more-classes', `Mostrar mais (${matches.length - state.listLimit} restantes)`);
    more.addEventListener('click', () => { state.listLimit += 200; renderList(); });
    root.append(more);
  }
  root.scrollTop = oldScroll;
}

function select(id, remember = true) {
  if (!state.nodes.has(id)) return;
  if (state.selected !== id && remember && state.selected) state.history.push(state.selected);
  state.selected = id;
  $('#back').disabled = !state.history.length;
  $$('.class-row').forEach(row => {
    const active = row.dataset.id === id;
    row.classList.toggle('selected', active); row.setAttribute('aria-pressed', String(active));
  });
  renderDetails();
  if (state.nodes.get(id).external) $('#show-external').checked = true;
  if (state.mode === 'all' && !cy.getElementById(id).length) setMode('focus');
  else if (state.mode === 'focus') renderGraph();
  else {
    cy.nodes().unselect();
    const node = cy.getElementById(id); node.select();
    cy.animate({ center: { eles: node }, duration: 200 });
  }
}

function relationSection(label, edges, targetSide) {
  const section = make('section', 'relations-section');
  const title = make('div', 'relations-title');
  title.append(make('span', '', label), make('small', '', edges.length));
  section.append(title);
  if (!edges.length) section.append(make('p', 'relations-empty', 'Nenhuma relação direta.'));
  let shown = 0;
  const more = make('button', 'more-classes');
  function appendPage() {
    more.remove();
    for (const edge of edges.slice(shown, shown + 100)) {
      const node = state.nodes.get(edge[targetSide]);
      const button = make('button', 'relation-button');
      button.title = node.id;
      const text = make('span'); text.append(make('strong', '', node.name), make('small', '', node.package || '(sem pacote)'));
      button.append(icon(node), text, make('span', 'arrow', '↗'));
      button.addEventListener('click', () => select(node.id));
      section.append(button);
    }
    shown = Math.min(shown + 100, edges.length);
    if (shown < edges.length) {
      more.textContent = `Mostrar mais (${edges.length - shown} restantes)`;
      section.append(more);
    }
  }
  more.addEventListener('click', appendPage);
  appendPage();
  return section;
}

function ancestrySection(label, entries) {
  const section = make('section', 'relations-section ancestry-section');
  const unique = [];
  const seen = new Set();
  for (const entry of entries) if (!seen.has(entry.id)) { seen.add(entry.id); unique.push(entry); }
  const title = make('div', 'relations-title');
  title.append(make('span', '', label), make('small', '', unique.length));
  section.append(title);
  if (!unique.length) section.append(make('p', 'relations-empty', 'Nenhum tipo encontrado na hierarquia.'));
  for (const entry of unique) {
    const node = state.nodes.get(entry.id);
    const button = make('button', 'relation-button ancestry-row');
    button.title = node.id;
    const text = make('span');
    text.append(make('strong', '', node.name), make('small', '', `${entry.direct ? 'direto' : 'herdado'} · nível ${entry.depth} · ${node.package || '(sem pacote)'}`));
    button.append(icon(node), text, make('span', 'arrow', '↗'));
    button.addEventListener('click', () => select(node.id));
    section.append(button);
  }
  return section;
}

function showFullHierarchy() {
  if (!state.selected) return;
  state.fullHierarchy = true;
  $('#direction').value = 'parents';
  $('#show-extends').checked = $('#show-implements').checked = $('#show-external').checked = true;
  setMode('focus');
  toast('Mostrando todos os ancestrais e interfaces conhecidos desta classe.');
}

function goToRootClass() {
  const root = rootClassOf();
  if (!root || root === state.selected) {
    toast('Esta já é a classe raiz conhecida nessa cadeia.');
    return;
  }
  select(root);
  setMode('focus');
}

function renderDetails() {
  const root = $('#details'); root.replaceChildren();
  const node = state.nodes.get(state.selected);
  if (!node) return;
  const top = make('div', 'detail-main');
  const badges = make('div', 'detail-badges');
  badges.append(make('span', node.kind, kindLabel(node)));
  if (node.abstract) badges.append(make('span', 'neutral', 'abstract'));
  top.append(badges, make('h2', '', node.name), make('p', 'qualified-name', node.id.startsWith('?') ? node.declaration : node.id));
  if (node.path) {
    const source = make('button', 'source-link');
    source.append(make('span', '', '⌘'), make('span', '', node.path.split('/').pop()), make('small', '', `L${node.line} ↗`));
    source.title = 'Abrir código na declaração';
    source.addEventListener('click', () => openSource(node));
    top.append(source);
  } else {
    const unresolved = ['unresolved', 'ambiguous'].includes(node.resolution);
    let text = unresolved ? 'Referência não resolvida. A ligação conserva o nome encontrado no código; o tipo de destino não foi confirmado.' : 'O tipo é referenciado pelo projeto, mas seu código não está na pasta importada.';
    if (node.candidates?.length) text += ` Candidatos: ${node.candidates.join(', ')}.`;
    top.append(make('div', `external-note ${unresolved ? 'uncertain-note' : ''}`, text));
  }
  top.append(make('pre', 'declaration', node.declaration));
  const focus = make('button', 'focus-class', '◎ Focar nesta classe');
  focus.addEventListener('click', () => setMode('focus'));
  const hierarchyActions = make('div', 'hierarchy-actions');
  const complete = make('button', 'focus-class', '↑ Mostrar herança completa');
  complete.title = 'Exibir todos os pais e interfaces, sem limite de profundidade';
  complete.addEventListener('click', showFullHierarchy);
  const rootButton = make('button', 'focus-class', '⇈ Ir à classe raiz');
  const rootId = rootClassOf(node.id);
  rootButton.disabled = !rootId || rootId === node.id;
  rootButton.title = rootButton.disabled ? 'Esta é a raiz conhecida' : `Ir para ${state.nodes.get(rootId)?.name || rootId}`;
  rootButton.addEventListener('click', goToRootClass);
  hierarchyActions.append(focus, complete, rootButton);
  top.append(hierarchyActions); root.append(top);
  const outgoing = state.outgoing.get(node.id) || [];
  const incoming = state.incoming.get(node.id) || [];
  const ancestry = ancestryOf(node.id);
  const classAncestors = ancestry.filter(entry => !['interface', 'annotation'].includes(state.nodes.get(entry.id)?.kind));
  const interfaces = ancestry.filter(entry => ['interface', 'annotation'].includes(state.nodes.get(entry.id)?.kind));
  root.append(
    relationSection('HERDA DE', outgoing.filter(e => e.kind === 'extends'), 'target'),
    relationSection('IMPLEMENTA', outgoing.filter(e => e.kind === 'implements'), 'target'),
    ancestrySection('CADEIA COMPLETA DE CLASSES', classAncestors),
    ancestrySection('INTERFACES DIRETAS E HERDADAS', interfaces),
    relationSection('ESTENDIDA POR', incoming.filter(e => e.kind === 'extends'), 'source'),
  );
  if (node.kind === 'interface' || incoming.some(e => e.kind === 'implements')) root.append(relationSection('IMPLEMENTADA POR', incoming.filter(e => e.kind === 'implements'), 'source'));
}

function visibleGraph() {
  const edges = state.project.edges.filter(e => $(`#show-${e.kind}`).checked &&
    ($('#show-external').checked || (!state.nodes.get(e.target).external && !state.nodes.get(e.source).external)));
  let ids;
  if (state.mode === 'focus') {
    ids = new Set(state.selected ? [state.selected] : []);
    const depth = Number($('#depth').value), direction = $('#direction').value;
    const adjacent = new Map();
    const connect = (from, to) => { if (!adjacent.has(from)) adjacent.set(from, []); adjacent.get(from).push(to); };
    for (const e of edges) {
      if (direction !== 'children') connect(e.source, e.target);
      if (direction !== 'parents') connect(e.target, e.source);
    }
    let frontier = [...ids];
    const levels = state.fullHierarchy ? Number.POSITIVE_INFINITY : depth;
    for (let i = 0; i < levels && frontier.length; i++) {
      const next = [];
      for (const id of frontier) for (const target of adjacent.get(id) || []) if (!ids.has(target)) { ids.add(target); next.push(target); }
      frontier = next;
    }
  } else {
    const pkg = $('#package-filter').value;
    ids = new Set(state.project.nodes.filter(n => !n.external && (!pkg || n.package === (pkg === '__default' ? '' : pkg))).map(n => n.id));
    const seeds = new Set(ids);
    for (const e of edges) if (seeds.has(e.source) && (state.nodes.get(e.target).external || pkg)) ids.add(e.target);
    if (state.selected && !pkg && ($('#show-external').checked || !state.nodes.get(state.selected).external)) ids.add(state.selected);
  }
  if (!$('#show-external').checked) for (const id of ids) if (state.nodes.get(id).external) ids.delete(id);
  const total = ids.size;
  // A bounded canvas keeps large projects navigable; the full index stays searchable.
  if (ids.size > LIMIT) {
    const ordered = [...ids];
    ordered.sort((a, b) => Number(b === state.selected) - Number(a === state.selected) || degree(b) - degree(a));
    ids = new Set(ordered.slice(0, LIMIT));
  }
  return { nodes: [...ids].map(id => state.nodes.get(id)), edges: edges.filter(e => ids.has(e.source) && ids.has(e.target)), total };
}

function graphElements(graph) {
  return [
    ...graph.nodes.map(n => ({ data: { id: n.id, label: `${n.name.length > 27 ? n.name.slice(0, 25) + '…' : n.name}\n${n.external ? '↗  ' + (n.resolution === 'external' ? 'externa' : 'não resolvida') : n.kind + (n.abstract ? ' · abstract' : '')}` }, classes: `${n.kind} ${n.external ? 'external' : ''} ${['ambiguous', 'unresolved'].includes(n.resolution) ? 'uncertain' : ''}` })),
    ...graph.edges.map(e => ({ data: { id: e.id, source: e.source, target: e.target, label: e.kind }, classes: e.kind })),
  ];
}

function layoutGraph() {
  if (!cy.nodes().length) return;
  const large = cy.nodes().length > 120 || cy.edges().length > 350;
  cy.layout(large
    ? { name: 'grid', cols: Math.ceil(Math.sqrt(cy.nodes().length * 1.6)), spacingFactor: 1.3, padding: 70, animate: false, fit: false }
    : { name: 'dagre', rankDir: 'BT', nodeSep: 35, rankSep: 75, edgeSep: 15, padding: 70, animate: false, fit: false }).run();
  cy.fit(undefined, 65);
  if (cy.zoom() > 1.15) { cy.zoom(1.15); cy.center(); }
}

function renderGraph() {
  if (!state.project) return;
  const graph = visibleGraph();
  cy.batch(() => {
    cy.elements().remove(); cy.add(graphElements(graph));
    if (state.selected) cy.getElementById(state.selected).select();
  });
  layoutGraph();
  updateZoomControls();
  $('#graph-empty').hidden = graph.nodes.length > 0;
  $('#visible-count').textContent = `${graph.nodes.length.toLocaleString('pt-BR')} tipos · ${graph.edges.length.toLocaleString('pt-BR')} relações visíveis`;
  const limited = graph.total > LIMIT;
  $('#graph-status').textContent = limited ? `Exibindo ${LIMIT} de ${graph.total.toLocaleString('pt-BR')}. Use foco ou filtre o pacote.` :
    state.fullHierarchy ? 'Hierarquia completa · setas apontam para o tipo pai' : 'Setas apontam para o tipo pai';
  $('#graph-status').style.color = limited ? '#775500' : '';
}

function setMode(mode) {
  if (mode !== 'focus') state.fullHierarchy = false;
  state.mode = mode;
  $('#mode-all').classList.toggle('active', mode === 'all');
  $('#mode-focus').classList.toggle('active', mode === 'focus');
  $('#focus-controls').hidden = mode !== 'focus';
  $('#package-filter').disabled = mode === 'focus';
  cy.resize(); renderGraph();
}

function resetFilters(render = true) {
  $('#package-filter').value = '';
  $('#show-extends').checked = $('#show-implements').checked = $('#show-external').checked = true;
  $('#depth').value = '1'; $('#direction').value = 'both';
  state.mode = 'all'; state.fullHierarchy = false;
  $('#mode-all').classList.add('active'); $('#mode-focus').classList.remove('active');
  $('#focus-controls').hidden = true; $('#package-filter').disabled = false;
  if (render) { cy.resize(); renderGraph(); }
}

async function openSource(node) {
  const request = ++state.sourceRequest;
  state.source = '';
  $('#source-title').textContent = node.name;
  $('#source-path').textContent = node.path;
  $('#source-code').replaceChildren(make('p', 'no-results', 'Lendo arquivo…'));
  $('#source-notice').textContent = 'A linha da declaração está destacada. Visualização somente para leitura.';
  $('#copy-source').disabled = $('#jump-declaration').disabled = true;
  $('#source-dialog').showModal();
  try {
    const source = await api(`/api/source?id=${encodeURIComponent(node.id)}`);
    if (request !== state.sourceRequest) return;
    state.source = source.code; state.sourceLine = source.line;
    const lines = source.code.split('\n');
    const root = $('#source-code'); root.replaceChildren();
    // Render in chunks so very large decompiled files do not block the interface.
    for (let offset = 0; offset < lines.length; offset += 400) {
      if (request !== state.sourceRequest) return;
      const fragment = document.createDocumentFragment();
      for (let index = offset; index < Math.min(offset + 400, lines.length); index++) {
        const row = make('div', `code-line${index + 1 === source.line ? ' declaration-line' : ''}`);
        row.dataset.line = index + 1;
        row.append(make('span', 'line-number', index + 1), make('span', 'line-text', lines[index]));
        fragment.append(row);
      }
      root.append(fragment);
      if (offset === 0 || (source.line > offset && source.line <= offset + 400)) jumpDeclaration();
      await new Promise(resolve => setTimeout(resolve, 0));
    }
    $('#source-notice').textContent = `${lines.length.toLocaleString('pt-BR')} linhas · Declaração na linha ${source.line} · Somente leitura`;
    $('#copy-source').disabled = $('#jump-declaration').disabled = false;
    jumpDeclaration();
  } catch (error) {
    $('#source-code').replaceChildren(make('div', 'error-box', error.message));
  }
}

function jumpDeclaration() {
  const line = $('#source-code .declaration-line');
  if (line) $('#source-code').scrollTop = line.offsetTop - $('#source-code').offsetTop - 85;
}

async function importProject(demo = false) {
  if (state.busy) return;
  $('#import-error').hidden = true;
  try {
    await api(demo ? '/api/demo' : '/api/import', demo ? {} : { path: $('#project-path').value });
    state.busy = true;
    $('#import-progress').hidden = false;
    $('#submit-import').disabled = $('#load-demo').disabled = $('#project-path').disabled = true;
    await pollImport();
  } catch (error) { importError(error.message); }
}

function finishImport() {
  state.busy = false;
  $('#import-progress').hidden = true;
  $('#submit-import').disabled = $('#load-demo').disabled = $('#project-path').disabled = false;
}

function importError(message) {
  finishImport();
  $('#import-error').textContent = message;
  $('#import-error').hidden = false;
  if (!$('#import-dialog').open) toast(message);
}

async function pollImport() {
  try {
    const result = await api('/api/status');
    $('#progress-text').textContent = result.message;
    $('#progress-count').textContent = result.total ? `${result.done} / ${result.total}` : '';
    $('#progress-bar').value = result.total ? 100 * result.done / result.total : 0;
    if (result.busy) { setTimeout(pollImport, 350); return; }
    if (result.error) { importError(result.error); return; }
    await loadProject();
    finishImport(); $('#import-dialog').close();
    toast(`${state.project.stats.types.toLocaleString('pt-BR')} declarações indexadas. Projeto pronto para explorar.`);
  } catch (error) { importError(error.message); }
}

function showWarnings() {
  const root = $('#warnings-list'); root.replaceChildren();
  const warnings = state.project?.warnings || [];
  if (!warnings.length) root.append(make('div', 'clean-state', 'Nenhum aviso nesta importação. As relações explícitas encontradas foram resolvidas.'));
  for (const warning of warnings.slice(0, 300)) {
    const row = make('div', 'warning-item');
    row.append(make('code', '', warning.path), make('p', '', warning.message)); root.append(row);
  }
  if (warnings.length > 300) root.append(make('p', '', `${warnings.length - 300} outros avisos disponíveis na exportação JSON.`));
  $('#warnings-dialog').showModal();
}

function updateZoomControls() {
  if (!cy) return;
  const percent = Math.round(cy.zoom() * 100);
  $('#zoom-range').value = Math.max(4, Math.min(250, percent));
  $('#zoom-value').value = `${percent}%`;
}

function zoomTo(level) {
  const bounded = Math.max(cy.minZoom(), Math.min(cy.maxZoom(), level));
  cy.animate({ zoom: { level: bounded, position: { x: cy.width() / 2, y: cy.height() / 2 } }, duration: 140 });
}

function panGraph(x, y) {
  cy.animate({ panBy: { x, y }, duration: 120 });
}

function centerSelected() {
  const selected = cy.getElementById(state.selected);
  if (!selected.length) {
    toast('A classe selecionada não está no recorte atual do mapa. Use “Foco na classe”.');
    return;
  }
  cy.animate({ center: { eles: selected }, duration: 220 });
}

function setup() {
  cy = cytoscape({ container: $('#graph'), minZoom: 0.04, maxZoom: 2.5,
    boxSelectionEnabled: false, autoungrabify: true, panningEnabled: true, userPanningEnabled: true,
    zoomingEnabled: true, userZoomingEnabled: true,
    style: [
      { selector: 'node', style: { 'shape': 'rectangle', 'width': 194, 'height': 62, 'background-color': '#ffffff', 'border-color': '#204a87', 'border-width': 1.3,
        'label': 'data(label)', 'text-wrap': 'wrap', 'text-valign': 'center', 'text-halign': 'center', 'color': '#202020', 'font-family': 'DejaVu Sans Mono, Consolas, monospace', 'font-size': 11, 'line-height': 1.8, 'text-max-width': 180, 'overlay-opacity': 0 } },
      { selector: 'node.interface, node.annotation', style: { 'background-color': '#f4edfa', 'border-color': '#660099', 'color': '#660099' } },
      { selector: 'node.external', style: { 'background-color': '#e8e8e8', 'border-color': '#a0a0a0', 'border-style': 'dashed', 'color': '#595959' } },
      { selector: 'node.uncertain', style: { 'background-color': '#ffffdf', 'border-color': '#775500', 'color': '#775500' } },
      { selector: 'node:selected', style: { 'background-color': '#316ac5', 'border-color': '#204a87', 'border-width': 2, 'color': '#ffffff', 'font-weight': 'bold' } },
      { selector: 'edge', style: { 'curve-style': 'bezier', 'width': 1.4, 'line-color': '#204a87', 'target-arrow-color': '#204a87', 'target-arrow-shape': 'triangle', 'arrow-scale': 0.8,
        'label': 'data(label)', 'font-size': 9, 'font-family': 'Consolas, monospace', 'color': '#204a87', 'text-background-color': '#ffffff', 'text-background-opacity': 1, 'text-background-padding': 4, 'text-rotation': 'autorotate', 'text-margin-y': -1, 'overlay-opacity': 0 } },
      { selector: 'edge.implements', style: { 'line-color': '#660099', 'target-arrow-color': '#660099', 'line-style': 'dashed', 'color': '#660099' } },
    ] });
  cy.on('tap', 'node', event => select(event.target.id()));
  cy.on('dbltap', 'node', event => { select(event.target.id()); setMode('focus'); });
  cy.on('zoom', updateZoomControls);
  $('#graph').addEventListener('pointerdown', () => $('#graph').classList.add('is-panning'));
  window.addEventListener('pointerup', () => $('#graph').classList.remove('is-panning'));
  new ResizeObserver(() => cy.resize()).observe($('#graph'));
  let searchTimer;
  $('#search').addEventListener('input', event => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { state.query = event.target.value.trim().toLowerCase(); state.listLimit = 200; renderList(); }, 120); });
  $$('[data-kind]').forEach(button => button.addEventListener('click', () => { state.kind = button.dataset.kind; $$('[data-kind]').forEach(b => b.classList.toggle('active', b === button)); renderList(); }));
  $('#mode-all').addEventListener('click', () => setMode('all'));
  $('#mode-focus').addEventListener('click', () => setMode('focus'));
  ['package-filter', 'show-extends', 'show-implements', 'show-external'].forEach(id => $('#' + id).addEventListener('change', renderGraph));
  ['direction', 'depth'].forEach(id => $('#' + id).addEventListener('change', () => { state.fullHierarchy = false; renderGraph(); }));
  $('#expand-depth').addEventListener('click', () => { state.fullHierarchy = false; const depth = Number($('#depth').value); if (depth === 5) toast('Profundidade máxima: 5 níveis. Use “Herança completa” para remover esse limite.'); else { $('#depth').value = depth + 1; renderGraph(); } });
  $('#full-hierarchy').addEventListener('click', showFullHierarchy);
  $('#reset-filters').addEventListener('click', () => resetFilters()); $('#empty-reset').addEventListener('click', () => resetFilters());
  $('#zoom-in').addEventListener('click', () => zoomTo(cy.zoom() * 1.25));
  $('#zoom-out').addEventListener('click', () => zoomTo(cy.zoom() / 1.25));
  $('#zoom-range').addEventListener('input', event => cy.zoom({ level: Number(event.target.value) / 100, renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 } }));
  $('#pan-up').addEventListener('click', () => panGraph(0, -90));
  $('#pan-down').addEventListener('click', () => panGraph(0, 90));
  $('#pan-left').addEventListener('click', () => panGraph(-90, 0));
  $('#pan-right').addEventListener('click', () => panGraph(90, 0));
  $('#center-selected').addEventListener('click', centerSelected);
  $('#fit').addEventListener('click', () => { cy.animate({ fit: { eles: cy.elements(), padding: 65 }, duration: 180 }); });
  $('#layout').addEventListener('click', layoutGraph);
  $('#back').addEventListener('click', () => { const id = state.history.pop(); if (id) select(id, false); });
  $('#open-project').addEventListener('click', () => { $('#import-dialog').showModal(); $('#project-path').focus(); });
  $$('.close-dialog').forEach(button => button.addEventListener('click', () => button.closest('dialog').close()));
  $('#source-dialog').addEventListener('close', () => { state.sourceRequest++; });
  $('#import-form').addEventListener('submit', event => { event.preventDefault(); importProject(); });
  $('#load-demo').addEventListener('click', () => importProject(true));
  $('#cancel-import').addEventListener('click', async () => { try { await api('/api/cancel', {}); } catch (error) { toast(error.message); } });
  $('#show-warnings').addEventListener('click', showWarnings);
  $('#jump-declaration').addEventListener('click', jumpDeclaration);
  $('#copy-source').addEventListener('click', async () => { try { await navigator.clipboard.writeText(state.source); toast('Código copiado.'); } catch { toast('O navegador não permitiu copiar. Selecione o texto no painel.'); } });
  $('#export-graph').addEventListener('click', () => {
    if (!state.project) return;
    const blob = new Blob([JSON.stringify(state.project, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob), link = make('a'); link.href = url; link.download = 'jadx-atlas.json'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  document.addEventListener('keydown', event => {
    if (['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName) || $('dialog[open]')) return;
    const key = event.key.toLowerCase();
    if (key === '/') { event.preventDefault(); $('#search').focus(); return; }
    const actions = {
      '+': () => zoomTo(cy.zoom() * 1.25), '=': () => zoomTo(cy.zoom() * 1.25), '-': () => zoomTo(cy.zoom() / 1.25),
      '0': () => cy.animate({ fit: { eles: cy.elements(), padding: 65 }, duration: 180 }),
      'arrowup': () => panGraph(0, -90), 'arrowdown': () => panGraph(0, 90),
      'arrowleft': () => panGraph(-90, 0), 'arrowright': () => panGraph(90, 0),
      'c': centerSelected, 'h': showFullHierarchy, 'r': goToRootClass,
    };
    if (actions[key]) { event.preventDefault(); actions[key](); }
  });
  loadProject().catch(error => { $('#visible-count').textContent = 'Falha ao carregar projeto'; toast(error.message); });
}

try { setup(); } catch (error) { toast(`Falha ao iniciar: ${error.message}`); }
