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
  append(...items) { this.children.push(...items); for(const item of items)if(item&&typeof item==='object')item.parent=this; }
  replaceChildren(...items) { for(const item of this.children)if(item&&typeof item==='object')item.parent=null;this.children = [];this.append(...items); }
  setAttribute(key, value) { this[key] = value; }
  addEventListener(name, callback) { this.events[name] = callback; }
  removeEventListener(name) { delete this.events[name]; }
  showModal() { this.open = true; }
  close() { this.open = false; }
  focus() {}
  remove() {if(this.parent){this.parent.children=this.parent.children.filter(item=>item!==this);this.parent=null;}}
}
const source = fs.readFileSync('sona_code/web/static/workspace-editor.js', 'utf8');
function harness(preferences = {}) {
  const models = new Map();
  const requests = [];
  const root = new Element('root');
  const listeners = {};
  const timers = new Map(); let timerId = 0;
  const storage = new Map([['sona-code:editor-preferences', JSON.stringify(preferences)]]);
  let selected, languageOptions, confirmClose=true;
  const locations = [], errors = [], languageCalls = [];
  const actions = [], cursors = [], modelChanges = [];
  const client = {open:s=>languageCalls.push(['open',s]),change:s=>languageCalls.push(['change',s]),
    close:(...args)=>languageCalls.push(['close',...args]),saved:(...args)=>languageCalls.push(['saved',...args]),
    setActive:(...args)=>languageCalls.push(['active',...args]),dispose:()=>languageCalls.push(['dispose']),
    reanalyze(){},subscribe:()=>()=>{},stamp:()=> '1',definition:async()=>[],implementation:async()=>[]};
  const editor = {
    textFocus: true, selection: {startLineNumber:1,startColumn:1,endLineNumber:1,endColumn:1},
    view: {scrollTop:0}, layout() {}, focus() {this.textFocus=true;}, hasTextFocus() {return this.textFocus;},
    getSelection() {return this.selection;}, setSelection(value) {this.selection=value;}, revealRangeInCenter() {},
    getPosition() {return {lineNumber:this.selection.startLineNumber,column:this.selection.startColumn};},
    getModel() {return selected;}, addAction(action) {actions.push(action);return {dispose(){}};},
    onDidChangeModel(fn) {modelChanges.push(fn);return {dispose(){}};},
    updateOptions(options) { this.options = options; },
    setModel(model) { selected = model;this.selection={startLineNumber:1,startColumn:1,endLineNumber:1,endColumn:1};modelChanges.forEach(fn=>fn()); },
    saveViewState() { return JSON.parse(JSON.stringify(this.view)); },
    restoreViewState(value) {this.view=value;}, onDidChangeCursorPosition(fn) {cursors.push(fn);return {dispose(){}};}, dispose() {},
  };
  const monaco = {
    Uri: { from: value => JSON.stringify(value) },
    KeyMod:{CtrlCmd:2048,Alt:512},KeyCode:{F12:59,KeyB:32},languages: { getLanguages: () => [{ id: 'plaintext' },{id:'typescript'}],registerDefinitionProvider:()=>({dispose(){}}),registerImplementationProvider:()=>({dispose(){}}) },
    editor: {
      create: () => editor,MouseTargetType:{CONTENT_TEXT:6},
      registerEditorOpener: () => ({dispose(){}}),
      createModel(value, language, uri) {
        if (models.has(uri)) throw Error('ModelService: Cannot add model because it already exists!');
        const model = {
          value, uri,
          getValue() { return this.value; },
          getLanguageId() { return this.language; },language,
          getWordAtPosition() {return {word:'value'};},
          validateRange(range) {
            const lines=this.value.split('\n');const clamp=(line,column)=>{
              line=Math.max(1,Math.min(line,lines.length));return [line,Math.max(1,Math.min(column,lines[line-1].length+1))];};
            const [a,b]=clamp(range.startLineNumber,range.startColumn),[c,d]=clamp(range.endLineNumber,range.endColumn);
            return {startLineNumber:a,startColumn:b,endLineNumber:c,endColumn:d};
          },
          onDidChangeContent(fn) { this.change = fn; return { dispose() {} }; },
          dispose() { models.delete(uri); },
        };
        models.set(uri, model);
        return model;
      },
      setModelLanguage(model,language) {model.language=language;},
    },
  };
  const context = vm.createContext({
    el: (tag, attrs, ...children) => new Element(tag, attrs, children),
    window: { addEventListener() {}, removeEventListener() {}, confirm: () => confirmClose },
    document: { body:new Element('body'),documentElement: {dataset: {}}, addEventListener: (name, callback) => {listeners[name] = callback;}, removeEventListener: name => {delete listeners[name];} },
    localStorage: {getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value)},
    setTimeout: fn => {timers.set(++timerId, fn); return timerId;}, clearTimeout: id => timers.delete(id),
    requestAnimationFrame: fn => fn(),
    loadWorkspaceMonaco: async () => monaco,
    workspaceCodeLanguage: (_monaco,path) => path.endsWith('.tsx')||path.endsWith('.ts') ? 'typescript' : 'plaintext',
    createLanguageClient:options=>{languageOptions=options;return client;},AbortController,
    workspaceLanguageFamily:language=>language==='typescript'||language==='typescriptreact'?'typescript':null,
    console,
  });
  vm.runInContext(fs.readFileSync('sona_code/web/static/workspace-code-history.js','utf8'),context);
  vm.runInContext(fs.readFileSync('sona_code/web/static/workspace-code-navigation.js','utf8'),context);
  vm.runInContext(fs.readFileSync('sona_code/web/static/workspace-analysis-settings.js','utf8'),context);
  vm.runInContext(source, context);
  const api = context.createWorkspaceEditor(root, {
    api: () => new Promise((resolve, reject) => {resolve.reject = reject; requests.push(resolve);}),
    projects: () => [{ id: 'p', name: 'P' },{id:'other',name:'Other'}],
    locate: (projectId,path)=>locations.push([projectId,path]),
    error: message=>errors.push(message),
  });
  const find = text => {
    let found;
    function walk(element) {
      if ((element.textContent ?? element.text) === text) found = element;
      for (const child of element.children || []) walk(child);
    }
    walk(root);
    return found;
  };
  return { api, requests, editor, models, find, root, listeners, timers, locations, errors, languageCalls, client, actions, cursors, selected: () => selected,
    analysisStatus: value=>languageOptions.onStatus(value),
    setConfirm:value=>{confirmClose=value;},
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
  } else if (process.argv[2] === 'history') {
    const h=harness();const a=h.api.open({id:'p',name:'P'},'a.txt');h.requests.shift()(data('a\nb\nc'));await a;
    h.editor.setSelection({startLineNumber:2,startColumn:1,endLineNumber:2,endColumn:2});h.editor.view.scrollTop=99;
    h.selected().value='draft\nb\nc';
    const b=h.api.openTarget('other','b.txt',{startLineNumber:99,startColumn:99,endLineNumber:99,endColumn:99});
    assert.equal(h.selected().value,'draft\nb\nc','Pending target preserves active draft');
    h.editor.view.scrollTop=123;
    h.requests.shift()(data('target'));assert.equal(await b,true);assert.equal(h.editor.selection.startLineNumber,1);
    assert.equal(h.editor.selection.startColumn,7,'Definition positions clamp to changed text');
    let prevented=0;const key=(extra={})=>h.listeners.keydown({ctrlKey:true,key:'ArrowLeft',preventDefault(){prevented++;},stopPropagation(){},...extra});
    h.editor.textFocus=false;key();h.editor.textFocus=true;key({isComposing:true});key({shiftKey:true});key({altKey:true});assert.equal(prevented,0);
    key();await tick();assert.equal(prevented,1);assert.equal(h.selected().value,'draft\nb\nc');assert.equal(h.requests.length,0);
    assert.equal(h.editor.selection.startLineNumber,2);assert.equal(h.editor.view.scrollTop,123);
    assert.deepEqual(h.locations.at(-1),['p','a.txt']);
    key({key:'ArrowRight'});await tick();assert.equal(h.selected().value,'target');
    h.find('×').onclick(); // closes the active last tab
    h.find('后退').onclick();await tick();
    assert.equal(h.requests.length,1,'Closed history target reloads from disk');h.requests.shift()(data('reopened'));await tick();
    assert.equal(h.selected().value,'reopened');h.api.dispose();
  } else if (process.argv[2] === 'navigation-failure') {
    const h=harness();const a=h.api.open({id:'p',name:'P'},'a.txt');h.requests.shift()(data('draft'));await a;
    const bad=h.api.openTarget('p','bad.txt');h.requests.shift().reject({status:500,detail:'failed'});assert.equal(await bad,false);
    assert.equal(h.selected().value,'draft');assert.equal(h.find('后退').disabled,true);
    for(let i=0;i<11;i++){const op=h.api.openTarget('p',i+'.txt');h.requests.shift()(data(String(i)));await op;}
    assert.equal(await h.api.openTarget('p','full.txt'),false);assert.equal(h.requests.length,0);assert.equal(h.selected().value,'10');
    const pending=h.api.openTarget('p','a.txt');await pending; // existing draft, no GET
    h.api.dispose();
  } else if (process.argv[2] === 'navigation-close') {
    const h=harness();const a=h.api.open({id:'p',name:'P'},'a.txt');h.requests.shift()(data('disk'));await a;
    const late=h.api.openTarget('p','b.txt');h.find('关闭').onclick();h.requests.shift()(data('late'));
    assert.equal(await late,false);assert.equal(h.models.size,0);assert.equal(h.selected(),null);assert.equal(h.root.children[0].hidden,true);
    const next=h.api.open({id:'p',name:'P'},'c.txt');h.requests.shift()(data('fresh'));await next;
    assert.equal(h.find('后退').disabled,true,'Panel close clears history');h.api.dispose();
  } else if (process.argv[2] === 'definition-load-cancel') {
    for (const trigger of ['cursor','draft','group','suspend','success']) {
      const h=harness();let revision=0;const subscribers=[],opened=new Set();
      const changed=()=>{revision++;subscribers.forEach(fn=>fn('p','typescript'));};
      h.client.subscribe=fn=>{subscribers.push(fn);return()=>{};};h.client.stamp=()=>String(revision);
      h.client.open=s=>{opened.add(s.path);changed();};h.client.close=(_,path)=>{if(opened.delete(path))changed();};
      h.client.change=changed;
      h.client.definition=async()=>[{path:'b.ts',range:{start:{line:0,character:0},end:{line:0,character:1}}}];
      const initial=h.api.open({id:'p',name:'P'},'a.ts');h.requests.shift()(data('source'));await initial;
      const jumping=h.actions.find(a=>a.id==='sona-code.goto-definition').run();await tick();
      assert.equal(h.requests.length,1,'Definition resolved but target GET remains pending');
      if(trigger==='cursor'){h.editor.selection={startLineNumber:1,startColumn:2,endLineNumber:1,endColumn:2};h.cursors.forEach(fn=>fn({position:{lineNumber:1,column:2}}));}
      if(trigger==='draft'){h.selected().value='draft';h.selected().change();}
      if(trigger==='group')changed();
      if(trigger==='suspend')h.api.suspend();
      h.requests.shift()(data('target'));const navigated=await jumping;
      assert.equal(navigated,trigger==='success',trigger+' must be checked before activation');
      assert.equal(h.selected().value,trigger==='success'?'target':trigger==='draft'?'draft':'source');
      assert.equal(h.find('后退').disabled,trigger!=='success','Cancelled target must not enter history');
      assert.equal(h.locations.length,trigger==='success'?2:1);
      assert.equal(h.errors.length,0);h.api.dispose();
    }
  } else if (process.argv[2] === 'navigation-order') {
    const h=harness();const a=h.api.open({id:'p',name:'P'},'a.txt');h.requests.shift()(data('a'));await a;
    const b=h.api.openTarget('p','b.txt'), c=h.api.openTarget('p','c.txt');
    const rb=h.requests.shift(),rc=h.requests.shift();rc(data('c'));assert.equal(await c,true);
    rb(data('b'));assert.equal(await b,false);assert.equal(h.selected().value,'c');
    h.find('后退').onclick();await tick();assert.equal(h.selected().value,'a','Superseded target does not enter history');
    h.find('b.txt').onclick();await tick();assert.equal(h.selected().value,'b');assert.equal(h.find('前进').disabled,true);
    h.api.dispose();
  } else if (process.argv[2] === 'tab-hide-draft') {
    const h=harness({defaultEditing:true});
    const op=h.api.open({id:'p',name:'P'},'a.ts');h.requests.shift()(data('disk'));await op;
    h.selected().value='draft';h.selected().change();
    h.api.hide();assert.equal(h.root.children[0].hidden,true);
    h.api.suspend();h.api.resume();assert.equal(h.root.children[0].hidden,true,'Route resume does not cover the selected workspace Tab again');
    await h.api.open({id:'p',name:'P'},'a.ts');
    assert.equal(h.root.children[0].hidden,false);assert.equal(h.selected().value,'draft');
    assert.equal(h.requests.length,0,'Reopening a hidden editor preserves models');
    const late=h.api.openTarget('p','b.ts');h.api.hide();
    h.requests.shift()(data('late target'));assert.equal(await late,false,'A late file load cannot cover the selected terminal Tab');
    assert.equal(h.root.children[0].hidden,true);assert.equal(h.selected().value,'draft');
    h.api.dispose();
  } else if (process.argv[2] === 'tab-close-progress') {
    const h=harness();
    for(let i=0;i<12;i++){const op=h.api.open({id:'p',name:'P'},i+'.ts');h.requests.shift()(data(String(i)));await op;}
    const tabs=h.root.children[0].children.find(item=>item.role==='tablist');
    const pressedClose=tabs.children[0].children[1];
    h.analysisStatus({projectId:'p',family:'typescript',message:'Indexing project'});
    assert.equal(tabs.children[0].children[1],pressedClose,'Progress between pointerdown and click must keep the pressed close button mounted');
    h.selected().value='draft';h.selected().change();
    assert.equal(tabs.children[0].children[1],pressedClose,'Draft updates must preserve every close button');
    h.setConfirm(false);tabs.children[11].children[1].onclick();
    assert.equal(h.models.size,12,'Cancelled confirmation preserves the active draft');
    assert.equal(h.selected().value,'draft');
    pressedClose.onclick();
    assert.equal(h.models.size,11,'Closing an inactive tab frees a file slot');
    const op=h.api.open({id:'p',name:'P'},'new.ts');h.requests.shift()(data('new'));await op;
    assert.equal(h.models.size,12);h.api.dispose();
  } else if (process.argv[2] === 'language-sync') {
    const h=harness();const a=h.api.open({id:'p',name:'P'},'a.tsx');h.requests.shift()(data('disk'));await a;
    assert.equal(h.languageCalls.find(c=>c[0]==='open')[1].language,'typescriptreact');
    h.selected().value='draft';h.selected().change();const before=h.languageCalls.find(c=>c[0]==='change')[1];
    h.find('保存').onclick();h.requests.shift()(data('draft'));await tick();assert.ok(h.languageCalls.some(c=>c[0]==='saved'));
    h.find('重新加载').onclick();h.requests.shift()(data('new disk'));await tick();
    const after=h.languageCalls.filter(c=>c[0]==='open').at(-1)[1];assert.ok(after.epoch>before.epoch);assert.ok(after.version>before.version);
    h.find('关闭').onclick();assert.ok(h.languageCalls.some(c=>c[0]==='close'));h.api.dispose();assert.equal(h.languageCalls.at(-1)[0],'dispose');
  } else throw new Error('Unknown editor scenario');
  console.log('passed', process.argv[2]);
})().catch(error => {console.error(error);process.exitCode=1;});
