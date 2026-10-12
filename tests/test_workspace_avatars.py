"""Animal avatar choices survive app reloads independently of account login."""

from pathlib import Path
import xml.etree.ElementTree as ET

from tests.test_workspace_interactions import run_node


def test_avatar_selection_survives_reload_and_handles_unavailable_storage():
    run_node(r'''
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
const source = fs.readFileSync("sona_code/web/static/workspace-avatars.js", "utf8");
const saved = new Map(), notices = [];
let blocked = false;
const storage = {getItem(key) {if (blocked) throw Error("Storage unavailable"); return saved.get(key) ?? null;},
  setItem(key, value) {if (blocked) throw Error("Storage unavailable"); saved.set(key, value);}};
function launch() {
  const listeners = new Map();
  const document = {activeElement: null};
  const node = (tag, props = {}, ...children) => {
    const events = new Map();
    return {tag, ...props, children, isConnected: true, style: {},
      append(...items) {this.children.push(...items);},
      replaceChildren(...items) {this.children = items;},
      setAttribute(key, value) {this[key] = value;},
      addEventListener(key, fn) {events.set(key, fn);},
      removeEventListener(key, fn) {assert.equal(events.get(key), fn); events.delete(key);},
      fire(key, event = {}) {events.get(key)?.(event);},
      focus() {document.activeElement = this;},
      showModal() {this.open = true;}, close() {this.open = false; this.fire("close");},
      remove() {this.isConnected = false; document.body.children = document.body.children.filter(item => item !== this);},
      getBoundingClientRect() {return tag === "dialog" ? {left: 18, top: 200, right: 338, bottom: 650, width: 320, height: 450}
        : {left: 18, top: 720, right: 54, bottom: 756, width: 36, height: 36};},
    };
  };
  document.body = node("body");
  const trigger = node("button");
  const context = vm.createContext({el: node, document, localStorage: storage,
    workspaceIcon: name => node("svg", {name}),
    window: {innerWidth: 976, innerHeight: 774,
      addEventListener(key, fn) {listeners.set(key, fn);},
      removeEventListener(key, fn) {assert.equal(listeners.get(key), fn); listeners.delete(key);}},
  });
  vm.runInContext(source, context);
  const controller = context.createWorkspaceAvatarPicker(trigger, (...args) => notices.push(args));
  const descendants = item => [item, ...item.children.filter(item => typeof item === "object").flatMap(descendants)];
  return {trigger, controller, context, document, listeners,
    open() {trigger.fire("click"); return document.body.children[0];},
    select(name) {
      const dialog = this.open();
      const choice = descendants(dialog).find(item => item.tag === "button" && item["aria-label"] === name);
      assert(choice); choice.onclick();
      assert.equal(document.body.children.length, 0, "Choosing an avatar closes the picker");
      assert.equal(document.activeElement, trigger, "Focus returns to the avatar trigger");
    },
  };
}
const first = launch();
assert.equal(first.trigger.children[0].src, "avatars/fox.svg", "Fresh installs show the default fox");
first.select("熊猫");
assert.equal(first.trigger.children[0].src, "avatars/panda.svg");
assert.equal(saved.get("sona-code:account-avatar"), "panda");
first.controller.dispose(); assert.equal(first.listeners.size, 0);
const reopened = launch();
assert.equal(reopened.trigger.children[0].src, "avatars/panda.svg", "A fresh app context restores the last choice");
let dialog = reopened.open();
assert.equal(reopened.document.activeElement["aria-label"], "熊猫");
assert.equal(reopened.document.activeElement["aria-pressed"], "true");
dialog.fire("cancel", {preventDefault() {}});
assert.equal(reopened.document.body.children.length, 0);
assert.equal(saved.get("sona-code:account-avatar"), "panda", "Escape does not change the avatar");
reopened.select("浣熊"); reopened.controller.dispose();
const restarted = launch(); assert.equal(restarted.trigger.children[0].src, "avatars/raccoon.svg");
restarted.open(); restarted.controller.dispose();
assert.equal(restarted.document.body.children.length, 0, "Route disposal must remove the open dialog");
saved.set("sona-code:account-avatar", "https://untrusted.example/avatar.svg");
const invalid = launch(); assert.equal(invalid.trigger.children[0].src, "avatars/fox.svg"); invalid.controller.dispose();
blocked = true;
const unavailable = launch();
unavailable.select("小猫"); assert.equal(unavailable.trigger.children[0].src, "avatars/cat.svg");
assert.equal(notices.length, 1, "Failed persistence is reported rather than silently promising to save");
assert.match(notices[0][0], /无法保存/);
unavailable.controller.dispose();
const sameSession = unavailable.context.createWorkspaceAvatarPicker(unavailable.trigger);
assert.equal(unavailable.trigger.children[0].src, "avatars/cat.svg", "A blocked browser still retains choices across routes");
sameSession.dispose();
''')


def test_builtin_avatars_are_self_contained_and_included_in_packages():
    root = Path(__file__).resolve().parents[1]
    static = root / "sona_code" / "web" / "static"
    avatars = list((static / "avatars").glob("*.svg"))
    assert len(avatars) == 16
    for asset in avatars:
        image = ET.parse(asset).getroot()
        assert image.attrib["viewBox"] == "0 0 96 96"
        assert image.attrib["aria-label"]
        assert all(node.tag.rsplit("}", 1)[-1] in {
            "svg", "defs", "clipPath", "g", "path", "circle", "ellipse"
        } for node in image.iter())
        assert not any("href" in key for node in image.iter() for key in node.attrib)
    package_data = (root / "pyproject.toml").read_text().split("[tool.setuptools.package-data]", 1)[1]
    assert '"web/static/avatars/*"' in package_data.split("]", 1)[0]
