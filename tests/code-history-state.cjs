const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
vm.runInThisContext(fs.readFileSync('sona_code/web/static/workspace-code-history.js', 'utf8'));
const place = (path, line = 1, projectId = 'p') => ({projectId, path,
  selection:{startLineNumber:line,startColumn:1,endLineNumber:line,endColumn:1},
  viewState:{scrollTop:line * 10}});
const deferred = () => {let resolve; const promise = new Promise(r => resolve = r); return {promise,resolve};};
(async () => {
  let current = place('A.java',20), open = async p => {current = p;return true;};
  const errors = [], opened = [];
  const h = createCodeHistory({capture:()=>current, openTarget:async p=>{opened.push(p);return open(p);},
    onChange(){},onError:m=>errors.push(m)}, process.argv[2] === 'limit' ? 3 : 100);
  const visit = (path,line=1,project='p') => {const target=place(path,line,project);h.record(current,target);current=target;};
  if (process.argv[2] === 'chain') {
    visit('B.java',50);visit('C.java',10);
    await h.move(-1);assert.equal(current.path,'B.java');assert.equal(current.selection.startLineNumber,50);
    await h.move(-1);assert.equal(current.path,'A.java');assert.equal(current.selection.startLineNumber,20);
    await h.move(1);assert.equal(current.path,'B.java');await h.move(1);assert.equal(current.path,'C.java');
    visit('C.java',30);await h.move(-1);assert.equal(current.selection.startLineNumber,10);
    await h.move(-1);visit('D.py',5,'other');assert.equal(h.canForward,false);
    await h.move(-1);assert.equal(current.projectId,'p');await h.move(1);assert.equal(current.projectId,'other');
    h.record(current,current);await h.move(-1);assert.equal(current.path,'B.java');
  } else if (process.argv[2] === 'limit') {
    visit('B');visit('C');visit('D');visit('E');
    await h.move(-1);await h.move(-1);assert.equal(current.path,'C');assert.equal(h.canBack,false);
    await h.move(1);await h.move(1);assert.equal(current.path,'E');
  } else if (process.argv[2] === 'deleted') {
    visit('B');visit('C');open=async p=>{if(p.path==='B')throw {status:404};current=p;return true;};
    await h.move(-1);assert.equal(current.path,'A.java');assert.equal(errors.length,1);
    await h.move(1);assert.equal(current.path,'C');assert.equal(errors.length,2);
  } else if (process.argv[2] === 'busy') {
    visit('B');visit('C');const gate=deferred();open=async p=>{await gate.promise;current=p;return true;};
    const pending=h.move(-1);await h.move(-1);assert.equal(opened.length,1);
    gate.resolve();await pending;assert.equal(current.path,'B');
  } else if (process.argv[2] === 'failed') {
    visit('B');visit('C');open=async()=>false;await h.move(-1);assert.equal(current.path,'C');assert.equal(h.canForward,false);
    open=async()=>{throw Error('read failed');};await h.move(-1);assert.equal(h.canForward,false);assert.equal(errors.length,1);
    open=async p=>{current=p;return true;};await h.move(-1);assert.equal(current.path,'B');
  } else if (process.argv[2] === 'clear') {
    visit('B');const gate=deferred();open=()=>gate.promise;const pending=h.move(-1);
    h.clear();gate.resolve(true);await pending;assert.equal(h.canBack,false);assert.equal(h.canForward,false);
    current=place('X');visit('Y');await h.move(-1);assert.equal(opened.at(-1).path,'X');
  } else if (process.argv[2] === 'snapshot') {
    visit('B');current.viewState.scrollTop=999;current.selection.endColumn=4;visit('C');
    await h.move(-1);assert.equal(current.viewState.scrollTop,999);assert.equal(current.selection.endColumn,4);
    current.viewState.scrollTop=555;await h.move(-1);await h.move(1);assert.equal(current.viewState.scrollTop,555);
    current.selection.startLineNumber=999;await h.move(1);await h.move(-1);assert.equal(current.selection.startLineNumber,999);
  } else throw Error('Unknown history scenario');
  console.log('passed', process.argv[2]);
})().catch(error=>{console.error(error);process.exitCode=1;});
