import * as logic from './logic.js';

const { isPotential, isExposed, exposureLabel, surfaceClass, looksObfuscated, LIMIT } = logic;

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const make = (tag, className, text) => {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (text !== undefined) el.textContent = text;
  return el;
};
const state = { project: null, nodes: new Map(), incoming: new Map(), outgoing: new Map(), selected: null,
  kind: 'all', query: '', mode: 'all', history: [], listLimit: 200, revision: -1,
  source: '', sourceLine: 1, sourceRequest: 0, busy: false, collapsed: new Set(), fullHierarchy: false,
  surface: { exported: false, deeplink: false, noperm: false, type: '' }, role: '', panel: 'class', rules: new Map(), sourceFinding: null,
  layer: 'inheritance', intentOut: new Map(), intentIn: new Map(), annotations: {}, hideLibraries: false, extraPrefixes: [], grouped: false };
let cy;
let toastTimer;

// Accessibility: dialogs return focus to whatever opened them.
function openDialog(dialog) {
  dialog.returnFocusTo = document.activeElement;
  dialog.showModal();
}

function toast(message) {
  $('#toast').textContent = message;
  $('#toast').hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $('#toast').hidden = true; }, 4500);
}

// Session token from the launch URL fragment (#token=...), kept for this tab only and removed from the address bar.
const TOKEN = (() => {
  const match = location.hash.match(/token=([A-Za-z0-9_-]+)/);
  try {
    if (match) { sessionStorage.setItem('atlas.token', match[1]); history.replaceState(null, '', location.pathname); }
    return sessionStorage.getItem('atlas.token') || (match && match[1]) || '';
  } catch { return match ? match[1] : ''; }
})();

async function api(path, body) {
  const headers = { 'X-Atlas-Token': TOKEN };
  const response = await fetch(path, body === undefined ? { headers } : {
    method: 'POST', headers: { ...headers, 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'The operation could not be completed.');
  return result;
}

function icon(node) {
  return make('span', `type-icon ${node.kind} ${node.external ? 'external' : ''}`,
    node.external ? '↗' : ({ class: 'C', interface: 'I', enum: 'E', record: 'R', annotation: '@' }[node.kind] || 'C'));
}

function kindLabel(node) {
  return node.external ? 'external reference' : ({ class: 'class', interface: 'interface', enum: 'enum', record: 'record', annotation: 'annotation' }[node.kind]);
}

// Attack-surface helpers. "Potentially exported" = unknown/inconsistent with a guess of true; never shown as confirmed.



// Roles are inferred from the ancestor chain (atlas/roles.py); the first one is the most specific for display.
function primaryRole(node) { return node.roles?.[0]; }


function matchesRole(node) { return logic.matchesRole(node, state.role); }

// Phase 8: library hiding (never the app package) and analyst aliases.
function appPackage() { return state.project?.manifest?.package || ''; }

function isHiddenLibrary(node) { return logic.isHiddenLibrary(node, { hide: state.hideLibraries, prefixes: state.extraPrefixes, app: appPackage() }); }

function displayName(node) {
  const alias = state.annotations[node.id]?.alias;
  if (alias) return `${alias} (${node.name})`;
  if (node.originalName) return `${node.name} ⟵ ${node.originalName.split('.').pop()}`;
  return node.name;
}

function surfaceFilterActive() { return logic.surfaceFilterActive(state.surface); }

function matchesSurface(node) { return logic.matchesSurface(node, state.surface); }

async function loadProject() {
  const project = await api('/api/project');
  if (!project) return;
  if (project.tooLarge) {
    // Phase 9 guard: the browser would freeze on this payload; the on-demand routes and the CLI still work.
    $('#project-name').textContent = project.summary.name;
    $('#surface-banner').hidden = false;
    $('#surface-banner').textContent = `${project.message} (${Math.round(project.payloadBytes / 1048576)} MB; adjust the limit with ATLAS_MAX_PAYLOAD_MB).`;
    $('#visible-count').textContent = `${project.summary.stats.types.toLocaleString('en-US')} types indexed`;
    return;
  }
  state.project = project;
  state.nodes = new Map(project.nodes.map(n => [n.id, n]));
  state.incoming = new Map(); state.outgoing = new Map();
  for (const edge of project.edges) {
    if (!state.incoming.has(edge.target)) state.incoming.set(edge.target, []);
    if (!state.outgoing.has(edge.source)) state.outgoing.set(edge.source, []);
    state.incoming.get(edge.target).push(edge);
    state.outgoing.get(edge.source).push(edge);
  }
  state.intentOut = new Map(); state.intentIn = new Map();
  for (const edge of project.intentEdges || []) {
    if (!state.intentOut.has(edge.source)) state.intentOut.set(edge.source, []);
    if (!state.intentIn.has(edge.target)) state.intentIn.set(edge.target, []);
    state.intentOut.get(edge.source).push(edge);
    state.intentIn.get(edge.target).push(edge);
  }
  setLayer('inheritance', false);
  $$('[data-layer]').forEach(button => { button.disabled = !(project.intentEdges || []).length && button.dataset.layer !== 'inheritance'; });
  state.history = []; state.collapsed.clear(); state.listLimit = 200;
  state.query = ''; state.kind = 'all'; $('#search').value = '';
  $$('[data-kind]').forEach(button => button.classList.toggle('active', button.dataset.kind === 'all'));
  const firstExposed = project.manifest?.components.find(c => c.class && isExposed(c))?.class;
  state.selected = firstExposed || [...project.nodes].filter(n => !n.external).sort((a, b) => degree(b.id) - degree(a.id))[0]?.id;
  $('#project-name').textContent = project.name;
  $('#project-name').title = project.root;
  $('#demo-badge').hidden = !project.demo;
  $('#file-count').textContent = `${project.stats.files.toLocaleString('en-US')} files`;
  for (const key of ['types', 'relations', 'packages']) $('#stat-' + key).textContent = project.stats[key].toLocaleString('en-US');
  $('#stat-warnings').textContent = project.warnings.length.toLocaleString('en-US');
  $('#show-warnings').classList.toggle('has-warnings', project.warnings.length > 0);
  const obf = project.stats.obfuscation;
  $('#analysis-info').textContent = `Indexed in ${project.stats.seconds.toLocaleString('en-US')} s · ${project.stats.external} external references${obf ? ` · likely obfuscation: ${obf.level} (${Math.round(obf.fraction * 100)}% short names)` : ''}`;
  try { state.annotations = (await api('/api/annotations')).classes || {}; } catch { state.annotations = {}; }
  $('#project-path').value = project.demo ? '' : project.root;
  state.rules = new Map((project.rules || []).map(rule => [rule.id, rule]));
  renderSurfaceChrome();
  renderRoleFilter();
  renderFindingsChrome();
  renderPathsChrome();
  const filter = $('#package-filter');
  filter.replaceChildren(new Option('All packages', ''));
  [...new Set(project.nodes.filter(n => !n.external).map(n => n.package))].sort().forEach(p => filter.add(new Option(p || '(default package)', p || '__default')));
  resetFilters(false);
  renderList(); renderDetails(); renderGraph();
  $('#back').disabled = true;
}

function degree(id) { return (state.incoming.get(id)?.length || 0) + (state.outgoing.get(id)?.length || 0); }

function ancestryOf(start = state.selected) { return logic.ancestryOf(state.outgoing, start); }

function rootClassOf(start = state.selected) { return logic.rootClassOf(state.outgoing, state.nodes, start); }

function renderList() {
  const root = $('#class-list');
  const oldScroll = root.scrollTop;
  root.replaceChildren();
  if (!state.project) return;
  const matches = state.project.nodes.filter(n => !n.external &&
    (state.kind === 'all' || (state.kind === 'interface' ? ['interface', 'annotation'].includes(n.kind) : !['interface', 'annotation'].includes(n.kind))) &&
    `${n.id} ${n.path} ${n.originalName || ''} ${state.annotations[n.id]?.alias || ''}`.toLowerCase().includes(state.query) && matchesSurface(n) && matchesRole(n) && !isHiddenLibrary(n));
  matches.sort((a, b) => a.id.localeCompare(b.id));
  $('#search-count').textContent = matches.length.toLocaleString('en-US');
  if (!matches.length) {
    root.append(make('div', 'no-results', 'No declaration found. Try another name or package.'));
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
    summary.title = pkg || '(default package)';
    summary.append(make('span', '', '▱'), make('span', 'package-label', pkg || '(default package)'), make('small', '', nodes.length));
    group.append(summary);
    group.addEventListener('toggle', () => { if (group.open) state.collapsed.delete(pkg); else state.collapsed.add(pkg); });
    for (const node of nodes) {
      const button = make('button', `class-row${node.id === state.selected ? ' selected' : ''}`);
      button.dataset.id = node.id;
      button.title = node.id;
      button.setAttribute('aria-pressed', String(node.id === state.selected));
      const alias = state.annotations[node.id]?.alias;
      const local = (node.package ? node.id.slice(node.package.length + 1) : node.id) + (alias ? ` · ${alias}` : node.originalName ? ` ⟵ ${node.originalName.split('.').pop()}` : '');
      button.append(icon(node), make('span', 'class-name', local));
      if (node.findings) {
        const worst = ['high', 'medium', 'low', 'info'].find(s => node.findings[s]);
        const total = Object.values(node.findings).reduce((a, b) => a + b, 0);
        button.append(make('span', `finding-tag severity-${worst}`, `${total}${worst[0].toUpperCase()}`));
      }
      if (node.component && isExposed(node.component)) button.append(make('span', `surface-tag${isPotential(node.component) ? ' potential' : ''}`, isPotential(node.component) ? 'EXP?' : 'EXP'));
      button.addEventListener('click', () => select(node.id));
      group.append(button);
    }
    root.append(group);
  }
  if (matches.length > state.listLimit) {
    const more = make('button', 'more-classes', `Show more (${matches.length - state.listLimit} left)`);
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
  if (!edges.length) section.append(make('p', 'relations-empty', 'No direct relation.'));
  let shown = 0;
  const more = make('button', 'more-classes');
  function appendPage() {
    more.remove();
    for (const edge of edges.slice(shown, shown + 100)) {
      const node = state.nodes.get(edge[targetSide]);
      const button = make('button', 'relation-button');
      button.title = node.id;
      const text = make('span'); text.append(make('strong', '', node.name), make('small', '', node.package || '(default package)'));
      button.append(icon(node), text, make('span', 'arrow', '↗'));
      button.addEventListener('click', () => select(node.id));
      section.append(button);
    }
    shown = Math.min(shown + 100, edges.length);
    if (shown < edges.length) {
      more.textContent = `Show more (${edges.length - shown} left)`;
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
  if (!unique.length) section.append(make('p', 'relations-empty', 'No type found in the hierarchy.'));
  for (const entry of unique) {
    const node = state.nodes.get(entry.id);
    const button = make('button', 'relation-button ancestry-row');
    button.title = node.id;
    const text = make('span');
    text.append(make('strong', '', node.name), make('small', '', `${entry.direct ? 'direct' : 'inherited'} · level ${entry.depth} · ${node.package || '(default package)'}`));
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
  toast('Showing every known ancestor and interface of this class.');
}

function goToRootClass() {
  const root = rootClassOf();
  if (!root || root === state.selected) {
    toast('This is already the known root class of this chain.');
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
  if (node.readsIntent) badges.append(make('span', 'neutral', 'reads Intent'));
  if (node.deepLinkHandler) badges.append(make('span', 'neutral', 'deep link'));
  if (node.component) badges.append(make('span', `surface ${surfaceClass(node)}`, node.component.exported === true ? 'EXPORTED' : isPotential(node.component) ? 'EXPORTED?' : node.component.type));
  top.append(badges, make('h2', '', node.name), make('p', 'qualified-name', node.id.startsWith('?') ? node.declaration : node.id));
  if (node.path) {
    const source = make('button', 'source-link');
    source.append(make('span', '', '⌘'), make('span', '', node.path.split('/').pop()), make('small', '', `L${node.line} ↗`));
    source.title = 'Open the code at the declaration';
    source.addEventListener('click', () => openSource(node));
    top.append(source);
  } else {
    const unresolved = ['unresolved', 'ambiguous'].includes(node.resolution);
    let text = unresolved ? 'Unresolved reference. The link keeps the name found in the code; the target type was not confirmed.' : 'The type is referenced by the project, but its code is not in the imported folder.';
    if (node.candidates?.length) text += ` Candidates: ${node.candidates.join(', ')}.`;
    top.append(make('div', `external-note ${unresolved ? 'uncertain-note' : ''}`, text));
  }
  top.append(make('pre', 'declaration', node.declaration));
  const focus = make('button', 'focus-class', '◎ Focus on this class');
  focus.addEventListener('click', () => setMode('focus'));
  const hierarchyActions = make('div', 'hierarchy-actions');
  const complete = make('button', 'focus-class', '↑ Show full hierarchy');
  complete.title = 'Show every parent and interface, with no depth limit';
  complete.addEventListener('click', showFullHierarchy);
  const rootButton = make('button', 'focus-class', '⇈ Go to root class');
  const rootId = rootClassOf(node.id);
  rootButton.disabled = !rootId || rootId === node.id;
  rootButton.title = rootButton.disabled ? 'This is the known root' : `Go to ${state.nodes.get(rootId)?.name || rootId}`;
  rootButton.addEventListener('click', goToRootClass);
  hierarchyActions.append(focus, complete, rootButton);
  top.append(hierarchyActions); root.append(top);
  if (!node.external) root.append(annotationSection(node));
  if (node.findings) root.append(findingsSection(node));
  if (state.intentOut.has(node.id) || state.intentIn.has(node.id) || node.intents) root.append(intentsSection(node));
  if (!node.external && state.project.stats.usesEdges) root.append(usesSection(node));
  if (node.roles?.length) root.append(rolesSection(node));
  if (node.component) root.append(componentSection(node));
  else if (node.applicationClass) root.append(make('div', 'external-note', 'Application class declared in the AndroidManifest: it runs before any component.'));
  const outgoing = state.outgoing.get(node.id) || [];
  const incoming = state.incoming.get(node.id) || [];
  const ancestry = ancestryOf(node.id);
  const classAncestors = ancestry.filter(entry => !['interface', 'annotation'].includes(state.nodes.get(entry.id)?.kind));
  const interfaces = ancestry.filter(entry => ['interface', 'annotation'].includes(state.nodes.get(entry.id)?.kind));
  root.append(
    relationSection('EXTENDS', outgoing.filter(e => e.kind === 'extends'), 'target'),
    relationSection('IMPLEMENTS', outgoing.filter(e => e.kind === 'implements'), 'target'),
    ancestrySection('FULL CLASS CHAIN', classAncestors),
    ancestrySection('DIRECT AND INHERITED INTERFACES', interfaces),
    relationSection('EXTENDED BY', incoming.filter(e => e.kind === 'extends'), 'source'),
  );
  if (node.kind === 'interface' || incoming.some(e => e.kind === 'implements')) root.append(relationSection('IMPLEMENTED BY', incoming.filter(e => e.kind === 'implements'), 'source'));
}

function definition(list, term, value) {
  if (value === undefined || value === null || value === '') return;
  list.append(make('dt', '', term), make('dd', '', String(value)));
}

function rolesSection(node) {
  const section = make('section', 'relations-section roles-section');
  const title = make('div', 'relations-title');
  title.append(make('span', '', 'ROLES INFERRED FROM INHERITANCE'), make('small', '', node.roles.length));
  section.append(title);
  for (const role of node.roles) {
    const row = make('div', `role-row confidence-${role.confidence}`);
    row.append(make('strong', '', role.label), make('span', 'role-confidence', `confidence ${role.confidence}`));
    const chain = make('div', 'role-path');
    role.path.forEach((id, index) => {
      if (index) chain.append(make('span', 'role-arrow', ` → ${role.via[index - 1].startsWith('framework') ? '' : role.via[index - 1] + ' '}`));
      const target = state.nodes.get(id);
      const short = id.split('.').pop();
      if (target && id !== node.id) {
        const link = make('button', 'role-link', short);
        link.title = id; link.addEventListener('click', () => select(id));
        chain.append(link);
      } else chain.append(make('span', 'role-step', short));
    });
    row.append(chain);
    section.append(row);
  }
  if (node.undeclaredComponent) section.append(make('p', 'relations-empty', 'Has a component ancestor but is not declared in the AndroidManifest (may be a base class or unused code).'));
  return section;
}

function componentSection(node) {
  const component = node.component;
  const section = make('section', 'relations-section component-section');
  const title = make('div', 'relations-title');
  title.append(make('span', '', 'ANDROID COMPONENT (CANDIDATE)'), make('small', '', component.type));
  section.append(title);
  section.append(make('p', `exposure-line ${surfaceClass(node)}`, exposureLabel(component)));
  const list = make('dl', 'component-facts');
  definition(list, 'Declared as', (node.declaredAs || [component.name]).join(', '));
  definition(list, 'exported', `${component.exported} — ${component.exportedReason}`);
  if ('exportedGuess' in component) definition(list, 'Guess', `${component.exportedGuess === null ? 'no guess' : component.exportedGuess ? 'potentially exported' : 'potentially not exported'} — ${component.exportedGuessReason}`);
  definition(list, 'Confidence', component.confidence);
  const check = state.project.manifest?.components.find(c => c.class === node.id && c.type === component.type)?.roleCheck;
  if (check) definition(list, 'Inheritance matches', check === 'missing' ? 'NO — the chain does not reach the expected type (possible resolution error)' : `yes (confidence ${check})`);
  definition(list, 'Permission', component.permission ? `${component.permission} (${component.permissionSource === 'application' ? 'inherited from <application>' : 'from the component'})` : 'none');
  definition(list, 'protectionLevel', component.protectionLevel);
  definition(list, 'enabled', component.enabled);
  definition(list, 'intent-filters', component.intentFilters.length);
  section.append(list);
  if (component.deepLinks.length) {
    section.append(make('div', 'relations-title', 'DEEP LINKS'));
    for (const link of component.deepLinks) section.append(make('code', 'deep-link', `${link.uri}${link.autoVerify ? '  (autoVerify)' : ''}`));
  }
  for (const filter of component.intentFilters.slice(0, 20)) {
    const text = [...filter.actions, ...filter.categories.map(c => `cat: ${c}`)].join('\n') || '(no action)';
    section.append(make('pre', 'declaration filter-box', text));
  }
  return section;
}

function appAlerts(manifest) {
  const app = manifest.application;
  const alerts = [];
  const flag = (key, label, defaultText) => {
    const item = app[key];
    if (item.value === true) alerts.push(`${label}="true"`);
    else if (item.value === 'unknown') alerts.push(`${label}: ${item.reason}${item.guess !== null && item.guess !== undefined ? ` — palpite: ${item.guess} (${item.guessReason})` : ''}`);
    else if (item.value === null && defaultText) alerts.push(defaultText);
  };
  flag('debuggable', 'android:debuggable');
  flag('testOnly', 'android:testOnly');
  flag('allowBackup', 'android:allowBackup', 'android:allowBackup missing: the platform default is true');
  const cleartextDefault = manifest.targetSdk !== null && manifest.targetSdk < 28 && !app.networkSecurityConfig
    ? `android:usesCleartextTraffic missing with targetSdk ${manifest.targetSdk} < 28: cleartext HTTP is allowed by default` : null;
  flag('usesCleartextTraffic', 'android:usesCleartextTraffic', cleartextDefault);
  if (app.networkSecurityConfig) alerts.push(`networkSecurityConfig=${app.networkSecurityConfig} (review the XML; it overrides usesCleartextTraffic)`);
  return alerts;
}

function renderSurface() {
  const root = $('#surface'); root.replaceChildren();
  const manifest = state.project?.manifest;
  if (!manifest) return;
  const head = make('div', 'detail-main');
  head.append(make('h2', '', manifest.package || '(default package)'),
    make('p', 'qualified-name', `version ${manifest.versionName ?? '?'} (${manifest.versionCode ?? '?'}) · minSdk ${manifest.minSdk ?? '?'} · targetSdk ${manifest.targetSdk ?? 'missing'} · ${manifest.path}`));
  root.append(head);
  const alerts = appAlerts(manifest);
  const box = make('section', 'relations-section app-alerts');
  const title = make('div', 'relations-title');
  title.append(make('span', '', 'APP ALERTS (CANDIDATES)'), make('small', '', alerts.length));
  box.append(title);
  if (!alerts.length) box.append(make('p', 'relations-empty', 'No alert in the <application> attributes.'));
  for (const alert of alerts) box.append(make('p', 'alert-item', alert));
  root.append(box);
  const list = make('section', 'relations-section');
  const listTitle = make('div', 'relations-title');
  listTitle.append(make('span', '', 'COMPONENTS BY EXPOSURE'), make('small', '', manifest.components.length));
  list.append(listTitle);
  for (const component of manifest.components) {
    const button = make('button', `relation-button surface-row ${component.exported === true ? 'exported' : isPotential(component) ? 'potential' : ''}`);
    const text = make('span');
    const local = component.name.startsWith(manifest.package + '.') ? component.name.slice(manifest.package.length) : component.name;
    text.append(make('strong', '', local), make('small', '', `${component.type} · ${exposureLabel(component)}${component.deepLinks.length ? ` · ${component.deepLinks.length} deep link(s)` : ''}${component.enabled === false ? ' · disabled' : ''}`));
    button.append(make('span', 'surface-marker', component.exported === true ? '⇥' : isPotential(component) ? '⇥?' : '·'), text, make('span', 'arrow', component.class ? '↗' : '∅'));
    button.title = component.class ? `Go to ${component.class}` : 'The class is not in the exported sources';
    button.addEventListener('click', () => {
      if (!component.class) { toast(`${component.classId} is not in the sources (JADX failure, deobfuscation or library class).`); return; }
      setPanel('class'); select(component.class);
    });
    list.append(button);
  }
  root.append(list);
}

const PANELS = { class: 'details', surface: 'surface', findings: 'findings', paths: 'paths' };

function setPanel(panel) {
  state.panel = panel;
  for (const [name, id] of Object.entries(PANELS)) {
    $('#' + id).hidden = name !== panel;
    $(`#tab-${name}`).classList.toggle('active', name === panel);
    $(`#tab-${name}`).setAttribute('aria-selected', String(name === panel));
  }
  if (panel === 'surface') renderSurface();
  if (panel === 'findings') renderFindings();
}

// Paths (phase 5): computed on demand by the server; drawn as a temporary view on the map.
function renderPathsChrome() {
  const entries = state.project.pathEntries || [];
  $('#tab-paths').hidden = !entries.length;
  const entry = $('#path-entry');
  entry.replaceChildren();
  for (const item of entries) entry.add(new Option(`${item.id.split('.').pop()} — ${item.reasons.join(', ')}`, item.id));
  const target = $('#path-target');
  target.replaceChildren(new Option('classes with findings ≥ medium', ''), new Option('class selected on the map', '__selected'));
  for (const id of state.project.pathTargets || []) target.add(new Option(id.split('.').pop(), id));
  $('#paths-result').replaceChildren();
  state.pathView = null;
}

async function searchPaths(event) {
  event.preventDefault();
  const params = new URLSearchParams({ entry: $('#path-entry').value, maxDepth: $('#path-depth').value, inheritance: $('#path-inheritance').checked ? '1' : '0' });
  const target = $('#path-target').value === '__selected' ? state.selected : $('#path-target').value;
  if (target) params.set('target', target);
  const root = $('#paths-result');
  root.replaceChildren(make('p', 'relations-empty', 'Searching…'));
  try {
    const result = await api(`/api/paths?${params}`);
    root.replaceChildren();
    root.append(make('p', 'relations-empty', `${result.paths.length} path(s) · ${result.note}${result.truncated ? ` · search stopped by the ${result.truncated} limit` : ''}`));
    if (result.paths.length) {
      const copy = make('button', 'subtle', 'Copy all as text');
      copy.addEventListener('click', async () => { try { await navigator.clipboard.writeText(result.text); toast('Paths copied.'); } catch { toast('The browser did not allow copying.'); } });
      root.append(copy);
    }
    result.paths.forEach((path, index) => root.append(pathCard(path, index, result.entry)));
  } catch (error) {
    root.replaceChildren(make('div', 'error-box', error.message));
  }
}

function pathCard(path, index, entry) {
  const card = make('section', `path-card confidence-${path.confidence}`);
  const head = make('div', 'relations-title');
  head.append(make('span', '', `PATH ${index + 1} → ${path.target.split('.').pop()}`), make('small', '', `${path.length} step(s) · ${path.confidence}`));
  card.append(head);
  const show = make('button', 'focus-class', '◎ Highlight on the map');
  show.addEventListener('click', () => showPath(entry, path));
  card.append(show);
  for (const step of path.steps) {
    const row = make('button', 'relation-button path-step');
    const text = make('span');
    text.append(make('strong', '', `${step.from.split('.').pop()} → ${step.to.split('.').pop()}`),
      make('small', '', `${step.kind} · ${step.via} · ${step.file}${step.line ? `:${step.line}` : ''} · ${step.confidence}`));
    row.append(text, make('span', 'arrow', '⌘'));
    row.title = 'Open the evidence in the code';
    row.addEventListener('click', () => { const node = state.nodes.get(step.from); if (node) openSource(node, step.line); });
    card.append(row);
  }
  return card;
}

function showPath(entry, path) {
  const nodes = [entry, ...path.steps.map(s => s.to)];
  state.pathView = { nodes, edges: path.steps.map((s, i) => ({ id: `p${i}`, source: s.from, target: s.to, kind: s.kind, via: s.via, line: s.line, confidence: s.confidence, path: true })) };
  renderGraph();
  $('#graph-status').textContent = 'Possible path highlighted · use ↺ to go back to the map';
}

// Findings (phase 3): candidates for manual review, never verdicts.
const SEVERITY_RANK = { info: 0, low: 1, medium: 2, high: 3 };
const CONFIDENCE_RANK = { low: 1, medium: 2, high: 3 };

function ruleOf(id) { return state.rules.get(id) || { id, title: id, description: '', remediation: '', falsePositives: '', references: [] }; }

function findingButton(finding, showClass) {
  const rule = ruleOf(finding.ruleId);
  const button = make('button', `relation-button finding-row severity-${finding.severity}`);
  const text = make('span');
  const where = showClass ? `${finding.classId.split('.').pop()} · ` : '';
  text.append(make('strong', '', rule.title),
    make('small', '', `${finding.severity.toUpperCase()} · confidence ${finding.confidence} · ${where}L${finding.line}${finding.inAnonymous ? ' · anonymous/local class' : ''}${finding.partial ? ' · partial file' : ''}`),
    make('code', 'finding-snippet', finding.snippet));
  button.append(make('span', 'severity-marker', finding.severity[0].toUpperCase()), text, make('span', 'arrow', '↗'));
  button.title = 'Open the code at this line';
  button.addEventListener('click', () => {
    const node = state.nodes.get(finding.classId);
    if (!node) return;
    if (state.selected !== finding.classId) select(finding.classId);
    openSource(node, finding.line, finding);
  });
  return button;
}

function renderFindings() {
  const root = $('#findings-list'); root.replaceChildren();
  const severity = $('#filter-severity').value, category = $('#filter-category').value, confidence = $('#filter-confidence').value;
  const findings = (state.project?.findings || []).filter(f =>
    (!severity || SEVERITY_RANK[f.severity] >= SEVERITY_RANK[severity]) && (!category || f.category === category) &&
    (!confidence || CONFIDENCE_RANK[f.confidence] >= CONFIDENCE_RANK[confidence]));
  root.append(make('p', 'relations-empty', `${findings.length.toLocaleString('en-US')} candidates. Confidence high: receiver type confirmed by the imports; medium: method and import match; low: method name only.`));
  for (const finding of findings.slice(0, 500)) root.append(findingButton(finding, true));
  if (findings.length > 500) root.append(make('p', 'relations-empty', `Showing 500 of ${findings.length}. Use the filters or export the JSON.`));
}

function annotationSection(node) {
  const saved = state.annotations[node.id] || { alias: '', tags: [], note: '' };
  const section = make('details', 'relations-section annotation-section');
  section.open = Boolean(saved.alias || saved.tags.length || saved.note);
  section.append(make('summary', 'relations-title', 'ANALYST NOTES (STORED OUTSIDE THE ANALYSED FOLDER)'));
  const alias = make('input'); alias.value = saved.alias; alias.placeholder = 'alias (e.g. LoginActivity)'; alias.maxLength = 80;
  const tags = make('input'); tags.value = saved.tags.join(', '); tags.placeholder = 'comma-separated tags';
  const note = make('textarea'); note.value = saved.note; note.placeholder = 'note'; note.maxLength = 2000; note.rows = 3;
  const save = make('button', 'subtle', 'Save note');
  save.addEventListener('click', async () => {
    try {
      const result = await api('/api/annotations', { id: node.id, alias: alias.value, tags: tags.value.split(','), note: note.value });
      state.annotations = result.classes;
      toast('Note saved.'); renderList(); renderGraph();
    } catch (error) { toast(error.message); }
  });
  for (const [label, field] of [['Alias', alias], ['Tags', tags], ['Note', note]]) {
    const row = make('label', 'annotation-field'); row.append(make('span', '', label), field); section.append(row);
  }
  section.append(save);
  if (node.originalName || node.sourceFile) section.append(make('p', 'relations-empty', `JADX: ${node.originalName ? `renamed from ${node.originalName}` : ''}${node.originalName && node.sourceFile ? ' · ' : ''}${node.sourceFile ? `compiled from ${node.sourceFile}` : ''}`));
  return section;
}

async function globalSearch(query) {
  const root = $('#global-search');
  if (query.length < 3) { root.hidden = true; root.replaceChildren(); return; }
  try {
    const result = await api(`/api/search?q=${encodeURIComponent(query)}&limit=30`);
    if ($('#search').value.trim().toLowerCase() !== query) return;
    root.replaceChildren(make('div', 'relations-title', `GLOBAL SEARCH · ${result.total}`));
    for (const item of result.results) {
      const row = make('button', 'relation-button global-row');
      const text = make('span');
      text.append(make('strong', '', item.id.split('.').pop()), make('small', '', `${item.field}: ${item.text}`));
      row.append(text);
      row.title = item.id;
      row.addEventListener('click', () => select(item.id));
      root.append(row);
    }
    root.hidden = !result.results.length;
  } catch { root.hidden = true; }
}

function usesSection(node) {
  const section = make('section', 'relations-section uses-section');
  const button = make('button', 'focus-class', '⇄ Type references (uses)');
  button.title = 'Project classes this class creates, calls statically or declares as a variable, and vice versa';
  button.addEventListener('click', async () => {
    button.disabled = true;
    try {
      const uses = await api(`/api/uses?id=${encodeURIComponent(node.id)}`);
      const list = (items, arrow) => items.map(item => {
        const row = make('button', 'relation-button');
        const text = make('span');
        text.append(make('strong', '', `${arrow} ${item.id.split('.').pop()}`), make('small', '', `${item.id} · L${item.line}`));
        row.append(text, make('span', 'arrow', '↗'));
        row.addEventListener('click', () => select(item.id));
        return row;
      });
      button.replaceWith(make('div', 'relations-title', `USES (${uses.out.length}) · USED BY (${uses.in.length})${uses.truncated ? ' · list truncated' : ''}`), ...list(uses.out, '→'), ...list(uses.in, '←'));
    } catch (error) { toast(error.message); button.disabled = false; }
  });
  section.append(button);
  return section;
}

function intentsSection(node) {
  const section = make('section', 'relations-section intents-section');
  const title = make('div', 'relations-title');
  const out = state.intentOut.get(node.id) || [], incoming = state.intentIn.get(node.id) || [];
  title.append(make('span', '', 'INTENTS (FLOW WITHIN THE METHOD)'), make('small', '', out.length + incoming.length));
  section.append(title);
  const row = (edge, other, arrow) => {
    const target = state.nodes.get(other);
    const button = make('button', 'relation-button intent-row');
    const text = make('span');
    const detail = [edge.kind, edge.via, `L${edge.line}`, `confidence ${edge.confidence}`, edge.action && `action ${edge.action}`, edge.actions?.length && `actions ${edge.actions.join(', ')}`].filter(Boolean).join(' · ');
    text.append(make('strong', '', `${arrow} ${target?.name || other}`), make('small', '', detail));
    button.append(text, make('span', 'arrow', '↗'));
    button.title = other;
    button.addEventListener('click', () => select(other));
    return button;
  };
  for (const edge of out) section.append(row(edge, edge.target, '→'));
  for (const edge of incoming) section.append(row(edge, edge.source, '←'));
  const unresolved = node.intents?.unresolved || [];
  if (unresolved.length) {
    section.append(make('div', 'relations-title', `UNRESOLVED (${node.intents.unresolvedTotal})`));
    for (const item of unresolved.slice(0, 20)) section.append(make('p', 'relations-empty', `L${item.line} ${item.via}: ${item.reason}${item.action ? ` — ${item.action}` : ''}${item.text ? ` — ${item.text}` : ''}`));
  }
  return section;
}

function findingsSection(node) {
  const findings = state.project.findings.filter(f => f.classId === node.id);
  const section = make('section', 'relations-section findings-section');
  const title = make('div', 'relations-title');
  title.append(make('span', '', 'FINDING CANDIDATES IN THIS CLASS'), make('small', '', findings.length));
  section.append(title);
  for (const finding of findings.slice(0, 50)) section.append(findingButton(finding, false));
  return section;
}

function ruleDetails(finding) {
  const rule = ruleOf(finding.ruleId);
  const box = make('div', 'rule-details');
  box.append(make('strong', '', `${rule.title} — candidate (${finding.severity}, confidence ${finding.confidence})`), make('p', '', rule.description));
  if (finding.detail || finding.secret) box.append(make('p', '', [finding.detail, finding.secret && `masked value: ${finding.secret}`].filter(Boolean).join(' · ')));
  box.append(make('p', '', `Fix: ${rule.remediation}`), make('p', 'false-positives', `Known false positives: ${rule.falsePositives}`));
  for (const url of rule.references || []) {
    const link = make('a', '', url); link.href = url; link.target = '_blank'; link.rel = 'noopener noreferrer';
    box.append(link);
  }
  return box;
}

function renderFindingsChrome() {
  const findings = state.project.findings || [];
  $('#tab-findings').hidden = !state.project.rules?.length;
  $('#show-findings').hidden = !state.project.rules?.length;
  const relevant = findings.filter(f => f.severity !== 'info').length;
  $('#stat-findings').textContent = `${relevant.toLocaleString('en-US')}${findings.length > relevant ? ` +${findings.length - relevant} info` : ''}`;
  const categories = [...new Set(findings.map(f => f.category))].sort();
  const select = $('#filter-category');
  select.replaceChildren(new Option('todas', ''));
  for (const category of categories) select.add(new Option(category, category));
  if (state.panel === 'findings') renderFindings();
}

function renderRoleFilter() {
  const counts = state.project.stats.roles || {};
  const select = $('#filter-role');
  select.replaceChildren(new Option('All roles', ''));
  const labels = new Map();
  for (const node of state.project.nodes) for (const role of node.roles || []) labels.set(role.role, role.label);
  for (const [id, label] of labels) select.add(new Option(`${label} (${counts[id] || 0})`, id));
  state.role = '';
  $('#role-filter').hidden = labels.size === 0;
}

function renderSurfaceChrome() {
  const project = state.project;
  const status = project.stats.manifest;
  const hasManifest = Boolean(project.manifest);
  $('#surface-filters').hidden = !hasManifest;
  $('#tab-surface').hidden = !hasManifest;
  $('#legend-surface').hidden = !hasManifest;
  $('#show-surface').hidden = !hasManifest;
  const banner = $('#surface-banner');
  banner.hidden = hasManifest || status === undefined;
  banner.textContent = status === 'invalid'
    ? 'Attack-surface layer off: the AndroidManifest.xml found was refused (see the warnings).'
    : 'Attack-surface layer off: AndroidManifest.xml not found. Export with JADX without --no-res or provide the manifest path.';
  if (hasManifest) {
    const potential = project.stats.potentiallyExported ? ` +${project.stats.potentiallyExported}?` : '';
    $('#stat-exported').textContent = `${project.stats.exportedComponents.toLocaleString('en-US')}${potential}`;
  }
  state.surface = { exported: false, deeplink: false, noperm: false, type: '' };
  ['filter-exported', 'filter-deeplink', 'filter-noperm'].forEach(id => { $('#' + id).checked = false; });
  $('#filter-component-type').value = '';
  setPanel(state.panel === 'findings' ? 'findings' : hasManifest && state.panel === 'surface' ? 'surface' : 'class');
}

function setLayer(layer, render = true) {
  state.layer = layer;
  $$('[data-layer]').forEach(button => button.classList.toggle('active', button.dataset.layer === layer));
  $('#legend-intents').hidden = layer === 'inheritance';
  if (render) renderGraph();
}

function visibleGraph() {
  if (state.pathView) {
    return { nodes: state.pathView.nodes.map(id => state.nodes.get(id)).filter(Boolean), edges: state.pathView.edges, total: state.pathView.nodes.length };
  }
  if (state.grouped && state.mode === 'all' && !$('#package-filter').value) return groupedGraph();
  return logic.selectGraph(state.project, state.nodes, {
    mode: state.mode, selected: state.selected, layer: state.layer, fullHierarchy: state.fullHierarchy,
    depth: Number($('#depth').value), direction: $('#direction').value, pkg: $('#package-filter').value,
    showKind: kind => $(`#show-${kind}`)?.checked ?? true, showExternal: $('#show-external').checked,
    keep: n => !isHiddenLibrary(n) && matchesSurface(n) && matchesRole(n), limit: LIMIT, degree,
  });
}


function nodeLabel(n) {
  if (n.kind === 'package') return `${n.name.length > 27 ? '…' + n.name.slice(-25) : n.name}\n▣ ${n.count} classes${n.findings ? ` · ${n.findings} findings` : ''}`;
  const shown = displayName(n);
  let name = shown.length > 27 ? shown.slice(0, 25) + '…' : shown;
  const role = primaryRole(n);
  if (role && looksObfuscated(n.name)) name = `${name} (${role.label})`;
  if (n.external) return `${name}\n↗  ${n.resolution === 'external' ? 'external' : 'unresolved'}`;
  // Text marker as well as the double border, so exposure never depends on colour alone.
  let detail = n.kind + (n.abstract ? ' · abstract' : '');
  if (n.component) {
    const marker = n.component.exported === true ? '⇥ ' : isPotential(n.component) ? '⇥? ' : '';
    detail = `${marker}${n.component.type}${n.component.deepLinks.length ? ' · link' : ''}`;
  } else if (n.applicationClass) detail = 'Application';
  else if (role) detail += ` · ${role.label}${role.confidence === 'high' ? '' : ' ?'}`;
  if (n.findings) {
    const worst = ['high', 'medium', 'low', 'info'].find(s => n.findings[s]);
    if (worst !== 'info') detail += ` · ${n.findings[worst]}×${worst}`;
  }
  return `${name}\n${detail}`;
}

// Aggregated package view: one node per package, edges summed; clicking a package expands it.
function groupedGraph() {
  return logic.groupPackages(state.project, state.nodes, { layer: state.layer, keep: n => !isHiddenLibrary(n) && matchesSurface(n) && matchesRole(n) });
}

function graphElements(graph) {
  return [
    ...graph.nodes.map(n => ({ data: { id: n.id, label: nodeLabel(n) }, classes: `${n.kind} ${n.external ? 'external' : ''} ${['ambiguous', 'unresolved'].includes(n.resolution) ? 'uncertain' : ''} ${surfaceClass(n)}` })),
    ...graph.edges.map(e => ({ data: { id: e.id, source: e.source, target: e.target, label: e.via ? `${e.via} L${e.line}` : e.kind },
      classes: `${e.kind}${e.via && !e.path ? ` intent ${e.confidence}` : ''}${e.path ? ' path' : ''}` })),
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
  $('#visible-count').textContent = `${graph.nodes.length.toLocaleString('en-US')} types · ${graph.edges.length.toLocaleString('en-US')} visible relations`;
  const limited = graph.total > LIMIT;
  $('#graph-status').textContent = limited ? `Showing ${LIMIT} of ${graph.total.toLocaleString('en-US')}. Use focus or filter by package.` :
    state.fullHierarchy ? 'Full hierarchy · arrows point to the parent type' : 'Arrows point to the parent type';
  $('#graph-status').style.color = limited ? '#775500' : '';
}

function setMode(mode) {
  state.pathView = null;
  if (mode !== 'focus') state.fullHierarchy = false;
  state.mode = mode;
  $('#mode-all').classList.toggle('active', mode === 'all');
  $('#mode-focus').classList.toggle('active', mode === 'focus');
  $('#focus-controls').hidden = mode !== 'focus';
  $('#package-filter').disabled = mode === 'focus';
  cy.resize(); renderGraph();
}

function resetFilters(render = true) {
  state.pathView = null;
  $('#package-filter').value = '';
  $('#show-extends').checked = $('#show-implements').checked = $('#show-external').checked = true;
  $('#depth').value = '1'; $('#direction').value = 'both';
  state.mode = 'all'; state.fullHierarchy = false;
  $('#mode-all').classList.add('active'); $('#mode-focus').classList.remove('active');
  $('#focus-controls').hidden = true; $('#package-filter').disabled = false;
  if (render) { cy.resize(); renderGraph(); }
}

async function openSource(node, focusLine = null, finding = null) {
  const request = ++state.sourceRequest;
  state.source = '';
  state.focusLine = focusLine;
  const details = $('#source-finding'); details.replaceChildren(); details.hidden = !finding;
  if (finding) details.append(ruleDetails(finding));
  $('#source-title').textContent = node.name;
  $('#source-path').textContent = node.path;
  $('#source-code').replaceChildren(make('p', 'no-results', 'Reading file…'));
  $('#source-notice').textContent = 'The declaration line is highlighted. Read-only view.';
  $('#copy-source').disabled = $('#jump-declaration').disabled = true;
  openDialog($('#source-dialog'));
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
        const row = make('div', `code-line${index + 1 === source.line ? ' declaration-line' : ''}${index + 1 === focusLine ? ' finding-line' : ''}`);
        row.dataset.line = index + 1;
        row.append(make('span', 'line-number', index + 1), make('span', 'line-text', lines[index]));
        fragment.append(row);
      }
      root.append(fragment);
      if (offset === 0 || (source.line > offset && source.line <= offset + 400)) jumpDeclaration();
      await new Promise(resolve => setTimeout(resolve, 0));
    }
    $('#source-notice').textContent = `${lines.length.toLocaleString('en-US')} lines · Declaration at line ${source.line}${focusLine ? ` · Candidate at line ${focusLine}` : ''} · Read-only`;
    $('#copy-source').disabled = $('#jump-declaration').disabled = false;
    jumpDeclaration();
  } catch (error) {
    $('#source-code').replaceChildren(make('div', 'error-box', error.message));
  }
}

function jumpDeclaration() {
  const line = $('#source-code .finding-line') || $('#source-code .declaration-line');
  if (line) $('#source-code').scrollTop = line.offsetTop - $('#source-code').offsetTop - 85;
}

async function importProject(demo = false) {
  if (state.busy) return;
  $('#import-error').hidden = true;
  try {
    await api(demo ? '/api/demo' : '/api/import', demo ? {} : { path: $('#project-path').value, manifest: $('#manifest-path').value });
    state.busy = true;
    $('#import-progress').hidden = false;
    $('#submit-import').disabled = $('#load-demo').disabled = $('#project-path').disabled = $('#manifest-path').disabled = true;
    await pollImport();
  } catch (error) { importError(error.message); }
}

function finishImport() {
  state.busy = false;
  $('#import-progress').hidden = true;
  $('#submit-import').disabled = $('#load-demo').disabled = $('#project-path').disabled = $('#manifest-path').disabled = false;
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
    toast(`${state.project.stats.types.toLocaleString('en-US')} declarations indexed. Project ready to explore.`);
  } catch (error) { importError(error.message); }
}

// Version diff (phase 6). Renamed obfuscated classes are only "possible matches".
const DIFF_LABELS = {
  app: 'App', 'app-flag': '<application> attribute', 'permission-added': 'New permission', 'permission-removed': 'Removed permission',
  'component-added': 'New component', 'component-removed': 'Removed component', 'exported-changed': 'Export changed',
  'permission-changed': 'Component permission changed', 'deeplink-added': 'New deep link', 'deeplink-removed': 'Removed deep link',
  'filters-changed': 'Intent-filter actions changed', 'manifest-presence': 'Manifest presence',
};

function otherIs() { return document.querySelector('input[name="other-is"]:checked')?.value || 'old'; }

async function startCompare(event) {
  event.preventDefault();
  $('#compare-error').hidden = true;
  $('#compare-result').replaceChildren();
  try {
    await api('/api/compare', { path: $('#compare-path').value });
    $('#compare-submit').disabled = true;
    $('#compare-progress').hidden = false;
    await pollCompare();
  } catch (error) { compareError(error.message); }
}

function compareError(message) {
  $('#compare-submit').disabled = false;
  $('#compare-progress').hidden = true;
  $('#compare-error').textContent = message; $('#compare-error').hidden = false;
}

async function pollCompare() {
  try {
    const status = await api('/api/status');
    $('#compare-progress').textContent = `${status.message}${status.total ? ` · ${status.done} / ${status.total}` : ''}`;
    if (status.busy) { setTimeout(pollCompare, 350); return; }
    if (status.error) { compareError(status.error); return; }
    $('#compare-submit').disabled = false; $('#compare-progress').hidden = true;
    renderDiff(await api(`/api/diff?otherIs=${otherIs()}`));
    $('#compare-download').disabled = false;
  } catch (error) { compareError(error.message); }
}

function renderDiff(result) {
  const root = $('#compare-result'); root.replaceChildren();
  root.append(make('h3', '', `${result.old} → ${result.new}`));
  if (result.empty) { root.append(make('p', 'clean-state', 'No difference in the attack surface, finding candidates or component inheritance.')); return; }
  if (result.triggers.length) root.append(make('p', 'compare-triggers', `Categories (--fail-on): ${result.triggers.join(', ')}`));
  const section = (title, items, render) => {
    if (!items.length) return;
    root.append(make('div', 'relations-title', `${title} (${items.length})`));
    const list = make('ul', 'compare-list');
    for (const item of items) list.append(render(item));
    root.append(list);
  };
  section('MANIFEST', result.manifest, item => {
    const name = item.name || item.field || '';
    const change = item.kind === 'component-added' ? `${item.type}, exported=${item.exported}`
      : item.uri || ('old' in item ? `${JSON.stringify(item.old)} → ${JSON.stringify(item.new)}` : '');
    return make('li', item.highlight ? 'highlight' : '', `${DIFF_LABELS[item.kind] || item.kind}${item.highlight ? ' (false → true)' : ''}: ${name} ${change}`);
  });
  const finding = f => make('li', `severity-${f.severity}`, `${f.severity} (${f.confidence}) · ${ruleOf(f.ruleId).title} · ${f.classId}:${f.line} · ${f.snippet}`);
  section('NEW FINDING CANDIDATES', result.findingsAdded, finding);
  section('REMOVED FINDING CANDIDATES', result.findingsRemoved, finding);
  section('COMPONENT INHERITANCE', result.hierarchy, item => make('li', '', `${item.name}: ${item.old.map(i => i.split('.').pop()).join(' → ')} ⇒ ${item.new.map(i => i.split('.').pop()).join(' → ')}`));
  section('POSSIBLE MATCHES (NOT PROOF OF EQUIVALENCE)', result.renames, item => make('li', '', `${item.old} → ${item.new} · confidence ${item.confidence} · ${item.reason}`));
}

async function downloadDiff() {
  try {
    const { markdown } = await api(`/api/diff?otherIs=${otherIs()}&format=md`);
    const url = URL.createObjectURL(new Blob([markdown], { type: 'text/markdown' }));
    const link = make('a'); link.href = url; link.download = 'jadx-atlas-diff.md'; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) { toast(error.message); }
}

function showWarnings() {
  const root = $('#warnings-list'); root.replaceChildren();
  const warnings = state.project?.warnings || [];
  if (!warnings.length) root.append(make('div', 'clean-state', 'No warnings in this import. Every explicit relation found was resolved.'));
  for (const warning of warnings.slice(0, 300)) {
    const row = make('div', 'warning-item');
    row.append(make('code', '', warning.path), make('p', '', warning.message)); root.append(row);
  }
  if (warnings.length > 300) root.append(make('p', '', `${warnings.length - 300} more warnings available in the JSON export.`));
  openDialog($('#warnings-dialog'));
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
    toast('The selected class is not in the current map view. Use “Class focus”.');
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
      { selector: 'node.package', style: { 'shape': 'round-rectangle', 'background-color': '#eef3fb', 'border-width': 2, 'border-style': 'double', 'border-color': '#204a87' } },
      { selector: 'node.exported', style: { 'border-style': 'double', 'border-width': 5, 'border-color': '#a00000' } },
      { selector: 'node.potential', style: { 'border-style': 'double', 'border-width': 5, 'border-color': '#775500' } },
      { selector: 'node:selected', style: { 'background-color': '#316ac5', 'border-color': '#204a87', 'border-width': 2, 'color': '#ffffff', 'font-weight': 'bold' } },
      { selector: 'edge', style: { 'curve-style': 'bezier', 'width': 1.4, 'line-color': '#204a87', 'target-arrow-color': '#204a87', 'target-arrow-shape': 'triangle', 'arrow-scale': 0.8,
        'label': 'data(label)', 'font-size': 9, 'font-family': 'Consolas, monospace', 'color': '#204a87', 'text-background-color': '#ffffff', 'text-background-opacity': 1, 'text-background-padding': 4, 'text-rotation': 'autorotate', 'text-margin-y': -1, 'overlay-opacity': 0 } },
      { selector: 'edge.path', style: { 'line-color': '#a00000', 'target-arrow-color': '#a00000', 'target-arrow-shape': 'triangle-backcurve', 'width': 3, 'line-style': 'solid', 'color': '#a00000' } },
      { selector: 'edge.intent', style: { 'line-color': '#a05000', 'target-arrow-color': '#a05000', 'target-arrow-shape': 'vee', 'line-style': 'dashed', 'line-dash-pattern': [8, 4], 'color': '#a05000', 'width': 1.8 } },
      { selector: 'edge.sends_action', style: { 'line-style': 'dotted', 'line-dash-pattern': [2, 4] } },
      { selector: 'edge.registers_receiver', style: { 'source-arrow-shape': 'circle', 'source-arrow-color': '#a05000' } },
      { selector: 'edge.implements', style: { 'line-color': '#660099', 'target-arrow-color': '#660099', 'line-style': 'dashed', 'color': '#660099' } },
    ] });
  cy.on('tap', 'node', event => {
    const id = event.target.id();
    if (id.startsWith('pkg:')) { $('#package-filter').value = id.slice(4) === '(default package)' ? '__default' : id.slice(4); renderGraph(); return; }
    select(id);
  });
  cy.on('dbltap', 'node', event => { select(event.target.id()); setMode('focus'); });
  cy.on('zoom', updateZoomControls);
  $('#graph').addEventListener('pointerdown', () => $('#graph').classList.add('is-panning'));
  window.addEventListener('pointerup', () => $('#graph').classList.remove('is-panning'));
  new ResizeObserver(() => cy.resize()).observe($('#graph'));
  let searchTimer;
  $('#search').addEventListener('input', event => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { state.query = event.target.value.trim().toLowerCase(); state.listLimit = 200; renderList(); globalSearch(state.query); }, 250); });
  $$('[data-kind]').forEach(button => button.addEventListener('click', () => { state.kind = button.dataset.kind; $$('[data-kind]').forEach(b => b.classList.toggle('active', b === button)); renderList(); }));
  $('#mode-all').addEventListener('click', () => setMode('all'));
  $('#mode-focus').addEventListener('click', () => setMode('focus'));
  ['package-filter', 'show-extends', 'show-implements', 'show-external'].forEach(id => $('#' + id).addEventListener('change', renderGraph));
  ['direction', 'depth'].forEach(id => $('#' + id).addEventListener('change', () => { state.fullHierarchy = false; renderGraph(); }));
  $('#expand-depth').addEventListener('click', () => { state.fullHierarchy = false; const depth = Number($('#depth').value); if (depth === 5) toast('Maximum depth: 5 levels. Use “Full hierarchy” to remove the limit.'); else { $('#depth').value = depth + 1; renderGraph(); } });
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
  $('#open-project').addEventListener('click', () => { openDialog($('#import-dialog')); $('#project-path').focus(); });
  $$('.close-dialog').forEach(button => button.addEventListener('click', () => button.closest('dialog').close()));
  $$('dialog').forEach(dialog => dialog.addEventListener('close', () => { if (dialog.returnFocusTo?.isConnected) dialog.returnFocusTo.focus(); }));
  // WAI-ARIA tabs: arrow keys move between the visible inspector tabs.
  $('.inspector-tabs').addEventListener('keydown', event => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    const tabs = $$('.inspector-tabs [role="tab"]').filter(tab => !tab.hidden);
    const index = tabs.indexOf(document.activeElement);
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
    event.preventDefault();
    tabs[next].focus();
    tabs[next].click();
  });
  $('#source-dialog').addEventListener('close', () => { state.sourceRequest++; });
  $('#import-form').addEventListener('submit', event => { event.preventDefault(); importProject(); });
  $('#load-demo').addEventListener('click', () => importProject(true));
  $('#cancel-import').addEventListener('click', async () => { try { await api('/api/cancel', {}); } catch (error) { toast(error.message); } });
  $('#show-warnings').addEventListener('click', showWarnings);
  $('#open-compare').addEventListener('click', () => { openDialog($('#compare-dialog')); $('#compare-path').focus(); });
  $('#compare-form').addEventListener('submit', startCompare);
  $('#compare-download').addEventListener('click', downloadDiff);
  $$('input[name="other-is"]').forEach(input => input.addEventListener('change', async () => { if (!$('#compare-download').disabled) { try { renderDiff(await api(`/api/diff?otherIs=${otherIs()}`)); } catch (error) { toast(error.message); } } }));
  $('#show-surface').addEventListener('click', () => setPanel('surface'));
  try {
    state.hideLibraries = localStorage.getItem('atlas.hideLibraries') === '1';
    state.extraPrefixes = JSON.parse(localStorage.getItem('atlas.extraPrefixes') || '[]');
  } catch { /* storage unavailable: defaults */ }
  $('#hide-libraries').checked = state.hideLibraries;
  $('#extra-prefixes').value = state.extraPrefixes.join(', ');
  const librariesChanged = () => {
    state.hideLibraries = $('#hide-libraries').checked;
    state.extraPrefixes = $('#extra-prefixes').value.split(',').map(p => p.trim()).filter(Boolean).map(p => (p.endsWith('.') ? p : p + '.'));
    try { localStorage.setItem('atlas.hideLibraries', state.hideLibraries ? '1' : '0'); localStorage.setItem('atlas.extraPrefixes', JSON.stringify(state.extraPrefixes)); } catch { /* ignore */ }
    state.listLimit = 200; renderList(); renderGraph();
  };
  $('#hide-libraries').addEventListener('change', librariesChanged);
  $('#extra-prefixes').addEventListener('change', librariesChanged);
  $('#group-packages').addEventListener('change', event => { state.grouped = event.target.checked; renderGraph(); });
  $$('[data-layer]').forEach(button => button.addEventListener('click', () => setLayer(button.dataset.layer)));
  $('#show-findings').addEventListener('click', () => setPanel('findings'));
  $('#tab-findings').addEventListener('click', () => setPanel('findings'));
  $('#tab-paths').addEventListener('click', () => setPanel('paths'));
  $('#paths-form').addEventListener('submit', searchPaths);
  ['filter-severity', 'filter-category', 'filter-confidence'].forEach(id => $('#' + id).addEventListener('change', renderFindings));
  $('#tab-class').addEventListener('click', () => setPanel('class'));
  $('#tab-surface').addEventListener('click', () => setPanel('surface'));
  const surfaceChanged = () => {
    state.surface = { exported: $('#filter-exported').checked, deeplink: $('#filter-deeplink').checked,
      noperm: $('#filter-noperm').checked, type: $('#filter-component-type').value };
    state.listLimit = 200; renderList(); if (state.mode === 'all') renderGraph();
  };
  ['filter-exported', 'filter-deeplink', 'filter-noperm', 'filter-component-type'].forEach(id => $('#' + id).addEventListener('change', surfaceChanged));
  $('#filter-role').addEventListener('change', event => { state.role = event.target.value; state.listLimit = 200; renderList(); if (state.mode === 'all') renderGraph(); });
  $('#jump-declaration').addEventListener('click', jumpDeclaration);
  $('#copy-source').addEventListener('click', async () => { try { await navigator.clipboard.writeText(state.source); toast('Code copied.'); } catch { toast('The browser did not allow copying. Select the text in the panel.'); } });
  // Exports go through the server so the JSON follows the documented schema and never carries the absolute path.
  const download = (text, name, type) => {
    const url = URL.createObjectURL(new Blob([text], { type })), link = make('a');
    link.href = url; link.download = name; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  const anonymize = () => ($('#export-anonymize').checked ? '1' : '0');
  $('#export-graph').addEventListener('click', async () => {
    try { download(JSON.stringify(await api(`/api/report?format=json&anonymize=${anonymize()}`), null, 2), 'jadx-atlas.json', 'application/json'); } catch (error) { toast(error.message); }
  });
  $('#export-report').addEventListener('click', async () => {
    try { download((await api(`/api/report?format=md&anonymize=${anonymize()}`)).markdown, 'jadx-atlas-report.md', 'text/markdown'); } catch (error) { toast(error.message); }
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
  loadProject().catch(error => { $('#visible-count').textContent = 'Could not load the project'; toast(error.message); });
}

try { setup(); } catch (error) { toast(`Failed to start: ${error.message}`); }
