// Pure functions of the JADX Atlas UI (no DOM, no globals), tested with `node --test tests/web`.

export const LIMIT = 600;

export function isPotential(component) {
  return ['unknown', 'inconsistent'].includes(component.exported) && component.exportedGuess === true;
}

export function isExposed(component) { return component.exported === true || isPotential(component); }

export function exposureLabel(component) {
  if (component.exported === true) return component.permission ? `exportado · exige ${component.permission} (${component.protectionLevel || 'unknown'})` : 'exportado · sem permissão';
  if (isPotential(component)) return component.exported === 'inconsistent' ? 'potencialmente exportado · Manifest inconsistente' : 'potencialmente exportado · valor desconhecido';
  if (component.exported === 'unknown') return 'exportação desconhecida';
  if (component.exported === 'inconsistent') return 'Manifest inconsistente';
  return 'não exportado';
}

export function surfaceClass(node) {
  if (!node.component) return '';
  return node.component.exported === true ? 'exported' : isPotential(node.component) ? 'potential' : '';
}

export function looksObfuscated(name) { return name.length <= 3 || /^[a-z]{1,3}(\$[a-z0-9]{1,3})*$/.test(name); }

export function surfaceFilterActive(surface) { return Boolean(surface.exported || surface.deeplink || surface.noperm || surface.type); }

export function matchesSurface(node, surface) {
  if (!surfaceFilterActive(surface)) return true;
  const component = node.component;
  if (!component) return false;
  return (!surface.exported || isExposed(component)) &&
    (!surface.deeplink || component.deepLinks.length > 0) &&
    (!surface.noperm || !component.permission) &&
    (!surface.type || component.type === surface.type);
}

export function matchesRole(node, role) { return !role || Boolean(node.roles?.some(r => r.role === role)); }

/** Library classes are hidden only on request, and never under the app's own package. */
export function isHiddenLibrary(node, { hide, prefixes = [], app = '' }) {
  if (!hide || node.external) return false;
  if (app && (node.id + '.').startsWith(app + '.')) return false;
  return Boolean(node.library) || prefixes.some(prefix => (node.id + '.').startsWith(prefix));
}

/** Breadth-first ancestors (extends + implements) with depth; cycle safe. */
export function ancestryOf(outgoing, start) {
  const visited = new Set(start ? [start] : []), queue = start ? [{ id: start, depth: 0 }] : [];
  const entries = [];
  while (queue.length) {
    const current = queue.shift();
    for (const edge of outgoing.get(current.id) || []) {
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

/** Top of the `extends` chain of classes (interfaces ignored); stops on cycles. */
export function rootClassOf(outgoing, nodes, start) {
  let current = start;
  const visited = new Set();
  while (current && !visited.has(current)) {
    visited.add(current);
    const parent = (outgoing.get(current) || []).find(edge => {
      const target = nodes.get(edge.target);
      return edge.kind === 'extends' && target && !['interface', 'annotation'].includes(target.kind);
    });
    if (!parent) return current;
    current = parent.target;
  }
  return current || start;
}

/**
 * Nodes and edges to draw.
 * options: { mode: 'all'|'focus', selected, depth, direction: 'both'|'parents'|'children', fullHierarchy,
 *            layer: 'inheritance'|'intents'|'all', showKind(kind) → bool, showExternal, pkg, keep(node) → bool,
 *            limit, degree(id) → number }
 */
export function selectGraph(project, nodes, options) {
  const { mode, selected, layer = 'inheritance', showExternal = true, pkg = '', limit = LIMIT } = options;
  const showKind = options.showKind || (() => true);
  const keep = options.keep || (() => true);
  const degree = options.degree || (() => 0);
  const inheritance = layer === 'intents' ? [] : project.edges;
  const intents = layer === 'inheritance' ? [] : (project.intentEdges || []);
  // Unknown edge kinds stay visible instead of crashing the map.
  const edges = [...inheritance, ...intents].filter(e => showKind(e.kind) &&
    (showExternal || (!nodes.get(e.target)?.external && !nodes.get(e.source)?.external)));
  let ids;
  if (mode === 'focus') {
    ids = new Set(selected ? [selected] : []);
    const adjacent = new Map();
    const connect = (from, to) => { if (!adjacent.has(from)) adjacent.set(from, []); adjacent.get(from).push(to); };
    for (const e of edges) {
      if (options.direction !== 'children') connect(e.source, e.target);
      if (options.direction !== 'parents') connect(e.target, e.source);
    }
    let frontier = [...ids];
    const levels = options.fullHierarchy ? Number.POSITIVE_INFINITY : (options.depth || 1);
    for (let i = 0; i < levels && frontier.length; i++) {
      const next = [];
      for (const id of frontier) for (const target of adjacent.get(id) || []) if (!ids.has(target)) { ids.add(target); next.push(target); }
      frontier = next;
    }
  } else {
    ids = new Set(project.nodes.filter(n => !n.external && keep(n) && (!pkg || n.package === (pkg === '__default' ? '' : pkg))).map(n => n.id));
    const seeds = new Set(ids);
    for (const e of edges) if (seeds.has(e.source) && (nodes.get(e.target)?.external || pkg)) ids.add(e.target);
    if (selected && !pkg && nodes.has(selected) && (showExternal || !nodes.get(selected).external)) ids.add(selected);
  }
  if (!showExternal) for (const id of ids) if (nodes.get(id)?.external) ids.delete(id);
  const total = ids.size;
  // A bounded canvas keeps large projects navigable; the full index stays searchable.
  if (ids.size > limit) {
    const ordered = [...ids];
    ordered.sort((a, b) => Number(b === selected) - Number(a === selected) || degree(b) - degree(a) || (a < b ? -1 : a > b ? 1 : 0));
    ids = new Set(ordered.slice(0, limit));
  }
  return { nodes: [...ids].map(id => nodes.get(id)).filter(Boolean), edges: edges.filter(e => ids.has(e.source) && ids.has(e.target)), total };
}

/** One node per package, edges counted between packages. */
export function groupPackages(project, nodes, { layer = 'inheritance', keep = () => true } = {}) {
  const packages = new Map();
  const keyOf = node => node.package || '(sem pacote)';
  for (const node of project.nodes) {
    if (node.external || !keep(node)) continue;
    const key = keyOf(node);
    if (!packages.has(key)) packages.set(key, { id: `pkg:${key}`, name: key, kind: 'package', external: false, package: key, count: 0, findings: 0 });
    const group = packages.get(key);
    group.count += 1;
    group.findings += node.findings ? (node.findings.high || 0) + (node.findings.medium || 0) : 0;
  }
  const edges = layer === 'intents' ? (project.intentEdges || []) : [...project.edges, ...(layer === 'all' ? project.intentEdges || [] : [])];
  const counts = new Map();
  for (const edge of edges) {
    const a = nodes.get(edge.source), b = nodes.get(edge.target);
    if (!a || !b || a.external || b.external || !packages.has(keyOf(a)) || !packages.has(keyOf(b))) continue;
    const from = `pkg:${keyOf(a)}`, to = `pkg:${keyOf(b)}`;
    if (from !== to) counts.set(`${from}→${to}`, (counts.get(`${from}→${to}`) || 0) + 1);
  }
  const groupEdges = [...counts].sort().map(([key, count], index) => { const [source, target] = key.split('→'); return { id: `g${index}`, source, target, kind: 'grouped', via: `${count}×`, line: '' }; });
  return { nodes: [...packages.values()].sort((a, b) => (a.id < b.id ? -1 : 1)), edges: groupEdges, total: packages.size };
}
