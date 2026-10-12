"use strict";

function createCodeHistory({capture, openTarget, onChange, onError}, limit = 100) {
  const copy = value => value ? JSON.parse(JSON.stringify(value)) : null;
  const same = (a, b) => a?.projectId === b?.projectId && a?.path === b?.path &&
    JSON.stringify(a?.selection) === JSON.stringify(b?.selection);
  let entries = [], index = -1, generation = 0, busy = false;
  function record(from, to) {
    if (!to) return;
    ++generation; busy = false;
    if (index < 0 && from) { entries.push(copy(from)); index = 0; }
    else if (index >= 0 && from) entries[index] = copy(from);
    if (index >= 0 && same(entries[index], to)) entries[index] = copy(to);
    else {
      entries.splice(index + 1); entries.push(copy(to)); index = entries.length - 1;
      if (entries.length > limit) { entries.splice(0, entries.length - limit); index = entries.length - 1; }
    }
    onChange?.();
  }
  async function move(direction) {
    if (busy || ![-1, 1].includes(direction)) return false;
    let candidate = index + direction;
    if (candidate < 0 || candidate >= entries.length) return false;
    const leaving = capture();
    if (leaving) entries[index] = copy(leaving);
    const stamp = ++generation; busy = true; onChange?.();
    try {
      while (candidate >= 0 && candidate < entries.length && stamp === generation) {
        try {
          if (!await openTarget(copy(entries[candidate])) || stamp !== generation) return false;
          index = candidate; return true;
        } catch (error) {
          if (stamp !== generation) return false;
          if (error.status !== 404) { onError?.(error.detail || error.message || "无法打开历史位置"); return false; }
          onError?.("历史文件已删除，跳过：" + entries[candidate].path);
          candidate += direction;
        }
      }
      return false;
    } finally { if (stamp === generation) { busy = false; onChange?.(); } }
  }
  return {record, move, clear() { ++generation; entries = []; index = -1; busy = false; onChange?.(); },
    get canBack() { return !busy && index > 0; },
    get canForward() { return !busy && index >= 0 && index < entries.length - 1; }};
}
