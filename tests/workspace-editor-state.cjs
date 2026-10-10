const fs = require('fs');
const vm = require('vm');
const assert = require('node:assert/strict');
class Element {
  constructor(tag, attrs = {}, children = []) {
    this.tag = tag;
    Object.assign(this, attrs);
    this.children = children;
    this.events = {};
    this.open = false;
  }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items; }
  setAttribute(key, value) { this[key] = value; }
  addEventListener(name, callback) { this.events[name] = callback; }
  showModal() { this.open = true; }
  close() { this.open = false; }
  focus() {}
  remove() {}
}
const source = fs.readFileSync('sona_code/web/static/workspace-editor.js', 'utf8');
function harness(preferences = {}) {
  const models = new Map();
  const requests = [];
  const root = new Element('root');
  const listeners = {};
  const timers = new Map(); let timerId = 0;
  const storage = new Map([['sona-code:editor-preferences', JSON.stringify(preferences)]]);
  let selected;
  const editor = {
    layout() {}, focus() {},
    updateOptions(options) { this.options = options; },
    setModel(model) { selected = model; },
    saveViewState() { return {}; },
    restoreViewState() {}, onDidChangeCursorPosition() {}, dispose() {},
  };
  const monaco = {
    Uri: { from: value => JSON.stringify(value) },
    languages: { getLanguages: () => [{ id: 'plaintext' }] },
    editor: {
      create: () => editor,
      registerEditorOpener: () => ({dispose(){}}),
      createModel(value, language, uri) {
        if (models.has(uri)) throw Error('ModelService: Cannot add model because it already exists!');
        const model = {
          value, uri,
          getValue() { return this.value; },
          getLanguageId() { return language; },
          onDidChangeContent(fn) { this.change = fn; return { dispose() {} }; },
          dispose() { models.delete(uri); },
        };
        models.set(uri, model);
        return model;
      },
    },
  };
  const context = vm.createContext({
    el: (tag, attrs, ...children) => new Element(tag, attrs, children),
    window: { addEventListener() {}, removeEventListener() {}, confirm: () => true },
    document: { documentElement: {dataset: {}}, addEventListener: (name, callback) => {listeners[name] = callback;}, removeEventListener: name => {delete listeners[name];} },
    localStorage: {getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value)},
    setTimeout: fn => {timers.set(++timerId, fn); return timerId;}, clearTimeout: id => timers.delete(id),
    requestAnimationFrame: fn => fn(),
    loadWorkspaceMonaco: async () => monaco,
    workspaceCodeLanguage: () => 'plaintext',
    console,
  });
  vm.runInContext(source, context);
  const api = context.createWorkspaceEditor(root, {
    api: () => new Promise((resolve, reject) => {resolve.reject = reject; requests.push(resolve);}),
    projects: () => [{ id: 'p', name: 'P' }],
    error: console.error,
  });
  const find = text => {
    let found;
    function walk(element) {
      if (element.text === text) found = element;
      for (const child of element.children || []) walk(child);
    }
    walk(root);
    return found;
  };
  return { api, requests, editor, models, find, root, listeners, timers, selected: () => selected,
    fireTimers() {const pending = [...timers.values()]; timers.clear(); pending.forEach(fn => fn());} };
}
const data = content => ({ content, revision: 'a'.repeat(64), read_only: false, encoding: 'utf-8', eol: 'LF' });
const tick = () => new Promise(resolve => setImmediate(resolve));
(async () => {
  if (process.argv[2] === 'reload') {
  const h = harness();
  const opening = h.api.open({ id: 'p', name: 'P' }, 'a.txt');
  h.requests.shift()(data('disk'));
  await opening;
  h.find('编辑').onclick();
  h.selected().value = 'unsaved';
  h.find('重新加载').onclick();
  await tick();
  assert.equal(h.editor.options.readOnly, true, 'Reload must lock input before awaiting the disk');
  h.requests.shift()(data('disk'));
  await tick();
  assert.equal(h.selected().value, 'disk');
  h.api.dispose();
  } else if (process.argv[2] === 'reopen') {
  const r = harness();
  const first = r.api.open({ id: 'p', name: 'P' }, 'a.txt');
  r.find('×').onclick();
  const second = r.api.open({ id: 'p', name: 'P' }, 'a.txt');
  r.requests.shift()(data('old request'));
  await first;
  r.requests.shift()(data('new request'));
  await second;
  assert.equal(r.selected()?.value, 'new request', 'A closed tab response cannot recreate the same URI');
  assert.equal(r.models.size, 1);
  const message = r.root.children[0].children.find(item => item.tag === 'p');
  assert.equal(message.textContent, '');
  r.api.dispose(); assert.equal(r.models.size, 0);
  } else if (process.argv[2] === 'save') {
    const h = harness();
    const opening = h.api.open({id:'p',name:'P'}, 'a.txt');
    h.requests.shift()(data('disk')); await opening;
    h.find('编辑').onclick(); h.selected().value = 'first edit'; h.selected().change();
    h.find('保存').onclick();
    h.selected().value = 'later edit'; h.selected().change();
    h.requests.shift()(data('first edit')); await tick();
    assert.equal(h.selected().value, 'later edit', 'Save completion must not replace edits made during the request');
    assert.ok(h.find('● a.txt'), 'Later edits remain dirty after saving the earlier snapshot');
    h.api.dispose();
  } else if (process.argv[2] === 'shortcuts') {
    const h = harness();
    const opening = h.api.open({id:'p',name:'P'}, 'a.txt');
    h.requests.shift()(data('disk')); await opening;
    assert.equal(h.root.children[0].tag, 'section');
    assert.equal(h.editor.options.readOnly, true);
    let prevented = 0;
    const key = value => h.listeners.keydown({ctrlKey:true, key:value, preventDefault(){prevented++;}, stopPropagation(){}});
    key('i'); assert.equal(h.editor.options.readOnly, false);
    h.selected().value = 'edit'; h.selected().change();
    key('i'); assert.equal(h.editor.options.readOnly, true);
    assert.equal(h.selected().value, 'edit', 'Ending editing keeps the draft');
    key('s'); assert.equal(h.requests.length, 1);
    h.requests.shift()(data('edit')); await tick();
    h.api.suspend(); key('i'); assert.equal(prevented, 3);
    h.api.resume(); key('i'); assert.equal(h.editor.options.readOnly, false);
    h.api.dispose(); assert.equal(h.listeners.keydown, undefined);
  } else if (process.argv[2] === 'autosave') {
    const h = harness({defaultEditing:true, autoSave:true});
    const opening = h.api.open({id:'p',name:'P'}, 'a.txt');
    h.requests.shift()(data('disk')); await opening;
    assert.equal(h.editor.options.readOnly, false, 'Saved default-edit preference applies on open');
    h.selected().value = 'first'; h.selected().change();
    h.selected().value = 'second'; h.selected().change();
    assert.equal(h.timers.size, 1, 'Typing is debounced');
    h.fireTimers(); assert.equal(h.requests.length, 1);
    h.selected().value = 'third'; h.selected().change();
    h.requests.shift()(data('second')); await tick();
    assert.equal(h.selected().value, 'third'); assert.equal(h.timers.size, 1);
    h.fireTimers(); h.requests.shift().reject({status:409, detail:'external write'}); await tick();
    assert.equal(h.timers.size, 0, 'Conflict suspends autosave');
    h.selected().value = 'keep draft'; h.selected().change();
    assert.equal(h.timers.size, 0); assert.ok(h.find('● a.txt'));
    h.find('保存').onclick(); h.requests.shift()(data('keep draft')); await tick();
    h.selected().value = 'new'; h.selected().change(); assert.equal(h.timers.size, 1);
    h.api.dispose(); assert.equal(h.timers.size, 0);
  } else throw new Error('Unknown editor scenario');
  console.log('passed', process.argv[2]);
})().catch(error => {console.error(error);process.exitCode=1;});
