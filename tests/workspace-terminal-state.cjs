const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = 'sona_code/web/static/workspace-terminal.js';
assert.ok(fs.existsSync(source), 'workspace terminal controller must exist');
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.hidden = false; this.clientWidth = 800; this.clientHeight = 400; this.isConnected = true; this.classList = {toggle() {}, add() {}, remove() {}}; }
  append(...nodes) { for (const n of nodes) { if (n.parentNode) n.remove(); n.parentNode = this; this.children.push(n); } }
  replaceChildren(...nodes) { for (const n of this.children) n.parentNode = null; this.children = []; this.append(...nodes); }
  remove() { if (this.parentNode) { this.parentNode.children = this.parentNode.children.filter(n => n !== this); this.parentNode = null; } }
  addEventListener() {}
  setAttribute(key, value) { this[key] = value; }
}
const terminals = [], sockets = [], calls = [], frames = [], timers = [];
class Terminal {
  constructor() { this.cols = 80; this.rows = 24; terminals.push(this); this.writes = []; }
  loadAddon() {} open(node) { this.node = node; } onData(fn) { this.input = fn; } focus() { this.focused = true; }
  write(data) { this.writes.push(data); } reset() { this.resets = (this.resets || 0) + 1; } dispose() { this.disposed = true; this.disposeCount = (this.disposeCount || 0) + 1; }
}
class Socket {
  constructor(url) { this.url = url; this.readyState = 1; this.sent = []; sockets.push(this); }
  send(data) { this.sent.push(JSON.parse(data)); } close() { this.closed = true; }
}
const context = vm.createContext({Terminal, FitAddon: {FitAddon: class {fit() {}}}, WebSocket: Socket,
  ResizeObserver: class {observe() {} disconnect() {}}, Uint8Array, console,
  el: (tag, props, ...children) => { const n = new Element(tag); Object.assign(n, props); n.append(...children); return n; },
  requestAnimationFrame: fn => frames.push(fn), setTimeout: fn => {timers.push(fn); return fn;}, clearTimeout: fn => {const i = timers.indexOf(fn); if(i >= 0) timers.splice(i,1);},
});
vm.runInContext(fs.readFileSync(source, 'utf8') + '\nglobalThis.create = createWorkspaceTerminal;', context);
const workspaceSource = fs.readFileSync('sona_code/web/static/workspace.js', 'utf8');
const entry = workspaceSource.match(/  function openProjectTerminal\(project\) \{[\s\S]*?\n  \}/);
assert.ok(entry, 'empty projects need a direct terminal entry');
const selection = {projectId: 'initial', tab: 'chat'}, remembered = {tab: 'chat'};
let selectedProjects = 0, savedSelection = 0, editorHidden = 0, mainRendered = 0;
const entryContext = vm.createContext({state: selection, workspaceSelection: remembered,
  codeEditor: {hide() {editorHidden++;}}, selectProject: id => {selectedProjects++; selection.projectId = id; selection.tab = 'chat';},
  persistWorkspaceSelection: () => savedSelection++, renderHeader() {}, renderMain: () => mainRendered++});
vm.runInContext(entry[0] + '\nglobalThis.open = openProjectTerminal;', entryContext);
entryContext.open({id:'empty'});
assert.equal(selection.projectId, 'empty'); assert.equal(selection.tab, 'terminal'); assert.equal(remembered.tab, 'terminal');
assert.equal(selectedProjects, 1); assert.equal(editorHidden, 1); assert.equal(savedSelection, 1); assert.equal(mainRendered, 1);
entryContext.open({id:'empty'}); assert.equal(selectedProjects, 1, 'opening same project must not reset its current selection');
assert.ok(workspaceSource.includes('["打开终端", () => openProjectTerminal(project)]'), 'project menu must expose terminal entry');
let rejectDelete = false, pending = null, pendingDelete = null, nextId = 0;
const api = async (path, opts = {}) => {
  calls.push({path, opts});
  if (opts.method === 'DELETE') { if (rejectDelete) throw new Error('close refused'); if (pendingDelete) return pendingDelete; return {}; }
  if (pending) return pending;
  return {id: 'term' + (++nextId), alive: true, title: 'Shell'};
};
const flush = async () => { await Promise.resolve(); await Promise.resolve(); while (frames.length) frames.shift()(); };
const project = id => ({id, path: '/projects/' + id});
(async () => {
  const controller = context.create({api, wsUrl: id => 'ws://host/' + id});
  const host = new Element('div');
  controller.mount(host, project('a')); await flush();
  assert.equal(calls.filter(c => c.opts.method === 'POST').length, 1);
  assert.equal(calls[0].opts.body.cwd, '/projects/a'); assert.equal(calls[0].opts.body.kind, 'shell');
  const a = terminals[0], aNode = a.node;
  const find = (root, predicate) => predicate(root) ? root : root.children.map(n => find(n, predicate)).find(Boolean);
  const closeButton = find(host, n => n['aria-label'] === '关闭终端：Shell');
  controller.mount(host, project('a')); await flush();
  assert.equal(find(host, n => n['aria-label'] === '关闭终端：Shell'), closeButton, 'SSE redraw must retain the close button between pointerdown and click');
  controller.mount(host, project('a')); await flush(); assert.equal(terminals.length, 1); assert.equal(a.node, aNode);
  a.input('early'); assert.equal(sockets[0].sent.filter(x => x.type === 'input').length, 0);
  sockets[0].onmessage({data: JSON.stringify({type:'attached', alive:true})});
  assert.equal(sockets[0].sent.find(x => x.type === 'input').data, 'early');
  sockets[0].onmessage({data: new Uint8Array([65]).buffer}); assert.equal(a.writes.length, 1);
  controller.hide(); controller.mount(host, project('b')); await flush();
  controller.mount(host, project('a')); await flush(); assert.equal(terminals.length, 2); assert.equal(a.node, aNode); assert.ok(!a.disposed);
  rejectDelete = true; await controller.close('a', 'term1'); assert.ok(!a.disposed, 'failed delete must retain live terminal');
  rejectDelete = false; await controller.close('a', 'term1'); assert.ok(a.disposed);
  controller.mount(host, project('a')); await flush(); assert.equal(terminals.length, 2, 'closing last shell must not auto-create');
  await controller.create('a'); await flush(); assert.equal(terminals.length, 3);
  const socket = sockets.at(-1); socket.onmessage({data: JSON.stringify({type:'attached', alive:true})}); socket.closed = true; socket.onclose({code:1006}); assert.equal(timers.length, 1);
  timers.shift()(); const reconnected = sockets.at(-1); reconnected.onmessage({data: JSON.stringify({type:'attached', alive:true})}); assert.equal(terminals.at(-1).resets, 2);
  reconnected.onmessage({data: JSON.stringify({type:'exit', code:0})}); terminals.at(-1).input('ignored'); assert.equal(reconnected.sent.filter(x => x.type === 'input').length, 0);
  rejectDelete = true;
  await assert.rejects(controller.removeProject('b'), /close refused/);
  assert.ok(!terminals[1].disposed, 'project removal must not discard a shell whose delete failed');
  rejectDelete = false;
  await controller.removeProject('b'); assert.ok(terminals[1].disposed);
  let slowResolve; pending = new Promise(done => slowResolve = done);
  controller.mount(host, project('slow')); await flush();
  controller.mount(host, project('a')); await flush();
  slowResolve({id:'slow', alive:true}); await flush(); pending = null;
  assert.equal(terminals.at(-1).node.parentNode.hidden, true, 'late creation must remain attached to its original project');
  let resolve; pending = new Promise(done => resolve = done);
  controller.mount(host, project('late')); await flush(); controller.dispose();
  resolve({id:'late', alive:true}); await flush(); assert.ok(calls.some(c => c.path.endsWith('/late') && c.opts.method === 'DELETE'), 'dispose must clean up late process');
  assert.ok(sockets.every(s => s.closed));
  pending = null;
  const closingController = context.create({api, wsUrl: id => 'ws://host/' + id});
  closingController.mount(host, project('disposing')); await flush();
  const disposingTerm = terminals.at(-1); const disposingId = sockets.at(-1).url.split('/').at(-1);
  let deleteResolve; pendingDelete = new Promise(done => deleteResolve = done);
  const closing = closingController.close('disposing', disposingId);
  closingController.dispose(); deleteResolve({}); await closing;
  assert.equal(disposingTerm.disposeCount, 1, 'late DELETE response must not dispose xterm twice');
  console.log('workspace terminal lifecycle passed');
})().catch(e => {console.error(e); process.exitCode = 1;});
