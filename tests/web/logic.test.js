// Front-end logic tests: node --test tests/web/
import assert from 'node:assert/strict';
import test from 'node:test';

import * as logic from '../../atlas/web/logic.js';

const node = (id, extra = {}) => ({ id, name: id.split('.').pop(), package: id.split('.').slice(0, -1).join('.'), kind: 'class', external: false, ...extra });
const edge = (id, source, target, kind = 'extends') => ({ id, source, target, kind, resolution: 'resolved' });

function fixture() {
  const nodes = [
    node('app.A'), node('app.B'), node('app.Base'), node('lib.C', { library: true }),
    node('android.app.Activity', { external: true, package: 'android.app' }), node('app.I', { kind: 'interface' }),
  ];
  const project = {
    nodes,
    edges: [edge('e0', 'app.A', 'app.Base'), edge('e1', 'app.B', 'app.Base'), edge('e2', 'app.Base', 'android.app.Activity'), edge('e3', 'app.A', 'app.I', 'implements')],
    intentEdges: [{ id: 'i0', source: 'app.A', target: 'app.B', kind: 'launches', via: 'startActivity', line: 3, confidence: 'high' }],
  };
  return { project, map: new Map(nodes.map(n => [n.id, n])) };
}

const outgoingOf = edges => {
  const map = new Map();
  for (const e of edges) { if (!map.has(e.source)) map.set(e.source, []); map.get(e.source).push(e); }
  return map;
};

test('exposure helpers never call an unknown value exported', () => {
  assert.equal(logic.isExposed({ exported: true }), true);
  assert.equal(logic.isExposed({ exported: 'unknown', exportedGuess: true }), true);
  assert.equal(logic.isPotential({ exported: 'unknown', exportedGuess: false }), false);
  assert.match(logic.exposureLabel({ exported: 'unknown', exportedGuess: true }), /potentially/);
  assert.equal(logic.exposureLabel({ exported: true, permission: null }), 'exported · no permission');
});

test('ancestry is breadth first and cycle safe', () => {
  const edges = [edge('a', 'X', 'Y'), edge('b', 'Y', 'Z'), edge('c', 'Z', 'X')];
  const result = logic.ancestryOf(outgoingOf(edges), 'X');
  assert.deepEqual(result.map(e => [e.id, e.depth]), [['Y', 1], ['Z', 2], ['X', 3]]);
});

test('root class follows extends of classes only and stops on cycles', () => {
  const { project, map } = fixture();
  assert.equal(logic.rootClassOf(outgoingOf(project.edges), map, 'app.A'), 'android.app.Activity');
  const cyclic = [edge('a', 'P', 'Q'), edge('b', 'Q', 'P')];
  const nodes = new Map([['P', node('P')], ['Q', node('Q')]]);
  assert.ok(['P', 'Q'].includes(logic.rootClassOf(outgoingOf(cyclic), nodes, 'P')));
});

test('overview keeps project classes and their external parents', () => {
  const { project, map } = fixture();
  const graph = logic.selectGraph(project, map, { mode: 'all', layer: 'inheritance' });
  assert.deepEqual(new Set(graph.nodes.map(n => n.id)), new Set(['app.A', 'app.B', 'app.Base', 'lib.C', 'app.I', 'android.app.Activity']));
  assert.equal(graph.edges.length, 4);
  const noExternal = logic.selectGraph(project, map, { mode: 'all', showExternal: false });
  assert.ok(!noExternal.nodes.some(n => n.external));
});

test('layers pick inheritance, intents or both; unknown kinds do not crash', () => {
  const { project, map } = fixture();
  const intents = logic.selectGraph(project, map, { mode: 'all', layer: 'intents' });
  assert.deepEqual(intents.edges.map(e => e.id), ['i0']);
  const all = logic.selectGraph(project, map, { mode: 'all', layer: 'all', showKind: kind => kind !== 'extends' });
  assert.deepEqual(new Set(all.edges.map(e => e.kind)), new Set(['implements', 'launches']));
  project.edges.push({ id: 'x', source: 'app.A', target: 'app.B', kind: 'future_kind' });
  assert.doesNotThrow(() => logic.selectGraph(project, map, { mode: 'all' }));
});

test('focus respects direction and depth', () => {
  const { project, map } = fixture();
  const parents = logic.selectGraph(project, map, { mode: 'focus', selected: 'app.A', direction: 'parents', depth: 1 });
  assert.deepEqual(new Set(parents.nodes.map(n => n.id)), new Set(['app.A', 'app.Base', 'app.I']));
  const deeper = logic.selectGraph(project, map, { mode: 'focus', selected: 'app.A', direction: 'parents', depth: 2 });
  assert.ok(deeper.nodes.some(n => n.id === 'android.app.Activity'));
  const children = logic.selectGraph(project, map, { mode: 'focus', selected: 'app.Base', direction: 'children', depth: 1 });
  assert.deepEqual(new Set(children.nodes.map(n => n.id)), new Set(['app.Base', 'app.A', 'app.B']));
});

test('limit keeps the selection and is deterministic', () => {
  const { project, map } = fixture();
  const first = logic.selectGraph(project, map, { mode: 'all', selected: 'app.I', limit: 2 });
  const second = logic.selectGraph(project, map, { mode: 'all', selected: 'app.I', limit: 2 });
  assert.equal(first.total, 6);
  assert.equal(first.nodes.length, 2);
  assert.ok(first.nodes.some(n => n.id === 'app.I'));
  assert.deepEqual(first.nodes.map(n => n.id), second.nodes.map(n => n.id));
});

test('library hiding never hides the app package', () => {
  const app = node('com.google.app.Main', { library: true });
  const lib = node('com.google.gson.Gson', { library: true });
  const options = { hide: true, prefixes: ['io.sdk.'], app: 'com.google.app' };
  assert.equal(logic.isHiddenLibrary(app, options), false);
  assert.equal(logic.isHiddenLibrary(lib, options), true);
  assert.equal(logic.isHiddenLibrary(node('io.sdk.X'), options), true);
  assert.equal(logic.isHiddenLibrary(lib, { ...options, hide: false }), false);
});

test('surface and role filters', () => {
  const exported = node('app.E', { component: { exported: true, deepLinks: [{ uri: 'app://x' }], permission: null, type: 'activity' }, roles: [{ role: 'activity' }] });
  const hidden = node('app.H', { component: { exported: false, deepLinks: [], permission: 'p', type: 'service' } });
  const surface = { exported: true, deeplink: true, noperm: true, type: '' };
  assert.equal(logic.matchesSurface(exported, surface), true);
  assert.equal(logic.matchesSurface(hidden, surface), false);
  assert.equal(logic.matchesSurface(hidden, {}), true);
  assert.equal(logic.matchesRole(exported, 'activity'), true);
  assert.equal(logic.matchesRole(hidden, 'activity'), false);
});

test('package grouping counts classes and cross-package edges', () => {
  const { project, map } = fixture();
  const grouped = logic.groupPackages(project, map, { layer: 'all' });
  const app = grouped.nodes.find(n => n.id === 'pkg:app');
  assert.equal(app.count, 4);
  assert.equal(grouped.edges.length, 0); // every project edge stays inside `app`
  const keepNoLib = logic.groupPackages(project, map, { keep: n => !n.library });
  assert.ok(!keepNoLib.nodes.some(n => n.id === 'pkg:lib'));
});

test('obfuscated-looking names', () => {
  for (const name of ['a', 'ab', 'a$b', 'c0']) assert.equal(logic.looksObfuscated(name), true, name);
  assert.equal(logic.looksObfuscated('MainActivity'), false);
});
