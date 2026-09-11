// src/plugin.ts
import fs2 from "node:fs";
import path2 from "node:path";

// src/constants.ts
var ASSET_DIR_NAME = "_widget_assets";
var RUNTIME_MODULE_NAME = "myst-anywidget-static-host.mjs";
var MANIFEST_NAME = "manifest.json";
var WIDGET_VIEW_MIME = "application/vnd.jupyter.widget-view+json";
var WIDGET_STATE_MIME = "application/vnd.jupyter.widget-state+json";
var IPY_MODEL_PREFIX = "IPY_MODEL_";

// src/transform/emit.ts
import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";

// src/generated/runtime-source.ts
var RUNTIME_SOURCE = `var __defProp = Object.defineProperty;
var __defNormalProp = (obj, key, value) => key in obj ? __defProp(obj, key, { enumerable: true, configurable: true, writable: true, value }) : obj[key] = value;
var __publicField = (obj, key, value) => __defNormalProp(obj, typeof key !== "symbol" ? key + "" : key, value);

// src/runtime/css.ts
function ensureShadowCss(el, cssText, cacheKey) {
  if (!el || !cssText) return;
  const root = el.getRootNode && el.getRootNode();
  if (!root || root === document) return;
  const key = cacheKey || cssText.length.toString();
  if (root.querySelector('style[data-myst-css="' + key + '"]')) return;
  const style = document.createElement("style");
  style.setAttribute("data-myst-css", key);
  style.textContent = cssText;
  root.appendChild(style);
}

// src/runtime/buffers.ts
function base64ToArrayBuffer(b64) {
  const bin = typeof atob === "function" ? atob(b64) : Buffer.from(b64, "base64").toString("binary");
  const len = bin.length;
  const buf = new ArrayBuffer(len);
  const view = new Uint8Array(buf);
  for (let i = 0; i < len; i++) view[i] = bin.charCodeAt(i);
  return buf;
}
function putBuffers(state, bufferPaths, buffers) {
  for (let i = 0; i < bufferPaths.length; i++) {
    const path = bufferPaths[i];
    let buf = buffers[i];
    if (!(buf instanceof DataView)) {
      buf = new DataView(buf instanceof ArrayBuffer ? buf : buf.buffer);
    }
    let obj = state;
    for (let j = 0; j < path.length - 1; j++) obj = obj[path[j]];
    obj[path[path.length - 1]] = buf;
  }
}
function applyBuffers(state, buffersList) {
  if (!buffersList || buffersList.length === 0) return;
  const paths = buffersList.map((b) => b.path);
  const arrayBuffers = buffersList.map((b) => base64ToArrayBuffer(b.data));
  putBuffers(state, paths, arrayBuffers);
}

// src/runtime/emitter.ts
function eventTarget() {
  if (typeof EventTarget === "function") return new EventTarget();
  return null;
}
function eventDetail(ev) {
  return ev && "detail" in ev ? ev.detail : void 0;
}
function splitEvents(event) {
  return String(event).split(/\\s+/).filter(Boolean);
}
var Emitter = class {
  constructor() {
    __publicField(this, "_target");
    __publicField(this, "_listeners");
    this._target = eventTarget();
    this._listeners = /* @__PURE__ */ new Map();
  }
  _listenersFor(event) {
    if (!this._listeners.has(event)) this._listeners.set(event, /* @__PURE__ */ new Map());
    return this._listeners.get(event);
  }
  on(event, fn) {
    if (!fn) return;
    for (const name of splitEvents(event)) this._onOne(name, fn);
  }
  off(event, fn) {
    for (const name of splitEvents(event)) this._offOne(name, fn);
  }
  _onOne(event, fn) {
    if (this._target) {
      const listeners = this._listenersFor(event);
      const wrapped = (ev) => {
        fn(eventDetail(ev), ev && ev.__extra);
      };
      listeners.set(fn, wrapped);
      this._target.addEventListener(event, wrapped);
      return;
    }
    this._listenersFor(event).set(fn, fn);
  }
  _offOne(event, fn) {
    const listeners = this._listeners.get(event);
    if (!listeners) return;
    const wrapped = listeners.get(fn);
    if (this._target && wrapped) {
      this._target.removeEventListener(event, wrapped);
    }
    listeners.delete(fn);
    if (listeners.size === 0) this._listeners.delete(event);
  }
  emit(event, detail, extra) {
    if (this._target) {
      const ev = typeof CustomEvent === "function" ? new CustomEvent(event, { detail }) : Object.assign(new Event(event), { detail });
      if (extra !== void 0) ev.__extra = extra;
      this._target.dispatchEvent(ev);
      return;
    }
    const listeners = this._listeners.get(event);
    if (!listeners) return;
    for (const fn of Array.from(listeners.values())) {
      try {
        fn(detail, extra);
      } catch (e) {
        console.error("[myst-host] listener error", e);
      }
    }
  }
};

// src/runtime/submodel.ts
var SubModel = class {
  constructor(state, buffers) {
    __publicField(this, "_state");
    __publicField(this, "_events");
    __publicField(this, "__widget_manager");
    // Identity fields attached by the registry/setup code.
    __publicField(this, "model_id");
    __publicField(this, "name");
    __publicField(this, "module");
    this._state = JSON.parse(JSON.stringify(state || {}));
    applyBuffers(this._state, buffers || []);
    this._events = new Emitter();
  }
  get(key) {
    return this._state[key];
  }
  set(key, value) {
    this._state[key] = value;
    this._events.emit("change:" + key);
    this._events.emit("change");
  }
  on(event, fn) {
    this._events.on(event, fn);
    return this;
  }
  off(event, fn) {
    this._events.off(event, fn);
    return this;
  }
  save_changes() {
  }
  send() {
  }
  // Comm mock: fire this proxy's "msg:custom" listeners locally, simulating an
  // inbound kernel->frontend custom message. See setupModel for the root-model
  // equivalent. Dispatches locally only \u2014 there is no kernel.
  receiveCustomMessage(content, buffers) {
    this._events.emit("msg:custom", content, buffers);
  }
  get widget_manager() {
    return this.__widget_manager;
  }
  set widget_manager(wm) {
    this.__widget_manager = wm;
  }
};

// src/runtime/refs.ts
function normalizeRef(ref) {
  if (typeof ref !== "string") return ref;
  if (ref.startsWith("anywidget:")) return ref.slice("anywidget:".length);
  if (ref.startsWith("IPY_MODEL_")) return ref.slice("IPY_MODEL_".length);
  return ref;
}
function uniqueKeys(keys) {
  const out = [];
  const seen = /* @__PURE__ */ new Set();
  for (let i = 0; i < (keys || []).length; i++) {
    const key = normalizeRef(keys[i]);
    if (!key || seen.has(key)) continue;
    seen.add(key);
    out.push(key);
  }
  return out;
}
function keysForState(state, rootId) {
  const keys = [rootId, state && state.widget_id, state && state._anywidget_id];
  if (state && state._layer_type) keys.push("_layer_type:" + state._layer_type);
  if (state && state._control_type) keys.push("_control_type:" + state._control_type);
  return keys;
}

// src/runtime/registry.ts
function normalizeWidgetModule(mod) {
  const candidate = mod && mod.default !== void 0 ? mod.default : mod;
  if (typeof candidate === "function") return candidate;
  return candidate || {};
}
function modelUuid(model) {
  if (!model) return null;
  if (model.model_id) return String(model.model_id);
  const rootId = typeof model.get === "function" ? model.get("_myst_root_id") : null;
  return rootId ? String(rootId) : null;
}
function installMirrorSet(from, to, guard) {
  if (!from || typeof from.set !== "function") return;
  const original = from.set.bind(from);
  from.set = function(key, value, options) {
    const result = original(key, value, options);
    if (!guard.syncing) {
      guard.syncing = true;
      try {
        if (key && typeof key === "object") {
          for (const k of Object.keys(key)) {
            if (to.get(k) !== key[k]) to.set(k, key[k]);
          }
        } else if (to.get(key) !== value) {
          to.set(key, value);
        }
      } finally {
        guard.syncing = false;
      }
    }
    return result;
  };
}
function mirrorModels(a, b) {
  if (!a || !b || a === b) return;
  if (!a.__mystMirrors) a.__mystMirrors = /* @__PURE__ */ new Set();
  if (a.__mystMirrors.has(b)) return;
  a.__mystMirrors.add(b);
  if (!b.__mystMirrors) b.__mystMirrors = /* @__PURE__ */ new Set();
  b.__mystMirrors.add(a);
  const guard = { syncing: false };
  installMirrorSet(a, b, guard);
  installMirrorSet(b, a, guard);
}
var MODEL_REGISTERED_EVENT = "model:registered";
var WIDGET_REGISTERED_EVENT = "widget:registered";
function createRegistry() {
  const _byKey = /* @__PURE__ */ new Map();
  const _all = [];
  const _bindings = /* @__PURE__ */ new Map();
  const _links = /* @__PURE__ */ new Map();
  const _boundLinks = /* @__PURE__ */ new Set();
  const _childModules = /* @__PURE__ */ new Map();
  const _events = new Emitter();
  function linkKey(link) {
    if (link && link.id) return String(link.id);
    return JSON.stringify([
      link && link.bidirectional ? "bi" : "dir",
      link && link.sourceId,
      link && link.sourceAttr,
      link && link.targetId,
      link && link.targetAttr
    ]);
  }
  function bindLinkIfReady(link) {
    const key = linkKey(link);
    if (_boundLinks.has(key)) return;
    const source = _byKey.get(normalizeRef(link.sourceId));
    const target = _byKey.get(normalizeRef(link.targetId));
    if (!source || !target) return;
    _boundLinks.add(key);
    let _updating = false;
    const fwd = () => {
      if (_updating) return;
      _updating = true;
      try {
        target.set(link.targetAttr, source.get(link.sourceAttr));
      } finally {
        _updating = false;
      }
    };
    source.on("change:" + link.sourceAttr, fwd);
    fwd();
    if (link.bidirectional) {
      const rev = () => {
        if (_updating) return;
        _updating = true;
        try {
          source.set(link.sourceAttr, target.get(link.targetAttr));
        } finally {
          _updating = false;
        }
      };
      target.on("change:" + link.targetAttr, rev);
    }
  }
  function bindReadyLinks() {
    for (const link of _links.values()) bindLinkIfReady(link);
  }
  function installLinks(links) {
    if (!Array.isArray(links)) return;
    for (const link of links) {
      if (!link || !link.sourceId || !link.targetId || !link.sourceAttr || !link.targetAttr)
        continue;
      const key = linkKey(link);
      if (_links.has(key)) continue;
      _links.set(key, link);
    }
    bindReadyLinks();
  }
  function registerModel(model, keys) {
    const registeredKeys = [];
    for (const key of uniqueKeys(keys)) {
      const existing = _byKey.get(key);
      if (existing) {
        if (existing !== model && modelUuid(existing) === key && modelUuid(model) === key) {
          mirrorModels(existing, model);
        }
        continue;
      }
      _byKey.set(key, model);
      registeredKeys.push(key);
    }
    if (_all.indexOf(model) < 0) _all.push(model);
    for (const key of registeredKeys) {
      _events.emit(MODEL_REGISTERED_EVENT, { key, model });
    }
    bindReadyLinks();
  }
  function registerBinding(model, binding) {
    const keys = uniqueKeys(binding && binding.keys);
    registerModel(model, keys);
    const registeredKeys = [];
    for (const key of keys) {
      if (_bindings.has(key)) continue;
      _bindings.set(key, binding);
      registeredKeys.push(key);
    }
    for (const key of registeredKeys) {
      _events.emit(WIDGET_REGISTERED_EVENT, { key, binding });
    }
  }
  function waitForModel(ref, options) {
    const key = normalizeRef(ref);
    const model = _byKey.get(key);
    if (model) return Promise.resolve(model);
    const timeout = options && typeof options.timeout === "number" ? options.timeout : 5e3;
    return new Promise((resolve, reject) => {
      let done = false;
      let timer = null;
      const cleanup = () => {
        _events.off(MODEL_REGISTERED_EVENT, onRegistered);
        if (timer) clearTimeout(timer);
      };
      const finish = (fn, value) => {
        if (done) return;
        done = true;
        cleanup();
        fn(value);
      };
      const onRegistered = (detail) => {
        if (detail && detail.key === key) finish(resolve, detail.model);
      };
      _events.on(MODEL_REGISTERED_EVENT, onRegistered);
      if (timeout >= 0) {
        timer = setTimeout(() => {
          finish(reject, new Error("[myst-host] timeout waiting for model: " + String(ref)));
        }, timeout);
      }
    });
  }
  function importChildModule(esm) {
    let promise = _childModules.get(esm);
    if (!promise) {
      const url = URL.createObjectURL(new Blob([esm], { type: "text/javascript" }));
      promise = import(
        /* @vite-ignore */
        url
      );
      _childModules.set(esm, promise);
    }
    return promise;
  }
  async function renderChild(ref, el) {
    const model = await waitForModel(ref);
    const esm = model && typeof model.get === "function" ? model.get("_esm") : null;
    if (!esm) throw new Error("[myst-host] child has no _esm: " + String(ref));
    const userModule = await importChildModule(esm);
    let widget = normalizeWidgetModule(userModule);
    ensureShadowCss(el, model.get("_css"));
    const nextArgs = { model, el, host: host() };
    if (typeof widget === "function") widget = await widget(nextArgs);
    if (widget && typeof widget.initialize === "function") await widget.initialize(nextArgs);
    let cleanup;
    if (widget && typeof widget.render === "function") cleanup = await widget.render(nextArgs);
    return typeof cleanup === "function" ? cleanup : () => {
    };
  }
  return {
    register: registerModel,
    get: (key) => _byKey.get(normalizeRef(key)),
    findFirst: (pred) => _all.find(pred),
    filter: (pred) => _all.filter(pred),
    all: () => _all.slice(),
    registerBinding,
    installLinks,
    getBinding: (key) => _bindings.get(normalizeRef(key)),
    getModel: (ref) => {
      const key = normalizeRef(ref);
      const model = _byKey.get(key);
      if (model) return Promise.resolve(model);
      return Promise.reject(new Error("[myst-host] unknown model: " + String(ref)));
    },
    waitForModel,
    getWidget: (ref) => {
      const key = normalizeRef(ref);
      const binding = _bindings.get(key);
      if (!binding) return Promise.reject(new Error("[myst-host] unknown widget: " + String(ref)));
      return Promise.resolve({ exports: binding.exports, render: binding.render });
    },
    renderChild,
    on: (event, fn) => _events.on(event, fn),
    off: (event, fn) => _events.off(event, fn),
    emit: (event, detail) => _events.emit(event, detail)
  };
}
function registry() {
  if (!window.__myst_anywidget_hosts) window.__myst_anywidget_hosts = /* @__PURE__ */ new Map();
  const scope = document && document.baseURI ? document.baseURI : "default";
  if (!window.__myst_anywidget_hosts.has(scope)) {
    window.__myst_anywidget_hosts.set(scope, createRegistry());
  }
  return window.__myst_anywidget_hosts.get(scope);
}
function host() {
  const reg = registry();
  return {
    getModel: (ref) => reg.getModel(ref),
    waitForModel: (ref, options) => reg.waitForModel(ref, options),
    getWidget: (ref) => reg.getWidget(ref),
    renderChild: (ref, el) => reg.renderChild(ref, el),
    on: (event, fn) => reg.on(event, fn),
    off: (event, fn) => reg.off(event, fn),
    emit: (event, detail) => reg.emit(event, detail)
  };
}
function setupModel(model) {
  if (!model || model.__mystSetupDone) return;
  model.__mystSetupDone = true;
  model.save_changes = function() {
  };
  model.send = function() {
  };
  const _on = typeof model.on === "function" ? model.on.bind(model) : null;
  const _active = /* @__PURE__ */ new Map();
  const splitEvents2 = (event) => String(event).split(/\\s+/).filter(Boolean);
  model.on = function(event, fn) {
    if (_on && typeof fn === "function") {
      for (const name of splitEvents2(event)) {
        let perFn = _active.get(name);
        if (!perFn) {
          perFn = /* @__PURE__ */ new Map();
          _active.set(name, perFn);
        }
        const rec = { active: true };
        perFn.set(fn, rec);
        _on(name, (detail) => {
          if (rec.active) fn(detail);
        });
      }
    }
    return model;
  };
  model.off = function(event, fn) {
    for (const name of splitEvents2(event)) {
      const perFn = _active.get(name);
      const rec = perFn && perFn.get(fn);
      if (rec) {
        rec.active = false;
        perFn.delete(fn);
      }
    }
    return model;
  };
  model.receiveCustomMessage = function(content, buffers) {
    const perFn = _active.get("msg:custom");
    if (!perFn) return;
    for (const [fn, rec] of Array.from(perFn)) {
      if (!rec.active) continue;
      try {
        fn(content, buffers);
      } catch (e) {
        console.error("[myst-host] msg:custom listener error", e);
      }
    }
  };
  const rootBuffers = typeof model.get === "function" && model.get("_myst_buffers") || [];
  if (Array.isArray(rootBuffers) && rootBuffers.length > 0) {
    const grouped = /* @__PURE__ */ new Map();
    for (const buf of rootBuffers) {
      const topKey = buf.path[0];
      if (!grouped.has(topKey)) grouped.set(topKey, []);
      grouped.get(topKey).push(buf);
    }
    for (const [topKey, bufs] of grouped.entries()) {
      const localPaths = bufs.map((b) => b.path.slice(1));
      const arrayBuffers = bufs.map((b) => base64ToArrayBuffer(b.data));
      if (bufs.length === 1 && bufs[0].path.length === 1) {
        model.set(topKey, new DataView(arrayBuffers[0]));
        continue;
      }
      const topVal = model.get(topKey);
      if (topVal == null) continue;
      putBuffers(topVal, localPaths, arrayBuffers);
    }
  }
  const submodels = typeof model.get === "function" && model.get("_myst_submodels") || {};
  const cache = /* @__PURE__ */ new Map();
  const reg = registry();
  for (const [id, entry] of Object.entries(submodels)) {
    const proxy = new SubModel(entry.state, entry.buffers);
    proxy.model_id = id;
    proxy.name = entry.model_name;
    proxy.module = entry.model_module;
    cache.set(id, proxy);
    reg.register(proxy, keysForState(entry.state, id));
  }
  const wm = {
    get_model: (id) => {
      if (cache.has(id)) return Promise.resolve(cache.get(id));
      const entry = submodels[id];
      if (!entry) return Promise.reject(new Error("[myst-shim] unknown sub-model: " + id));
      const proxy = new SubModel(entry.state, entry.buffers);
      proxy.widget_manager = wm;
      proxy.model_id = id;
      proxy.name = entry.model_name;
      proxy.module = entry.model_module;
      cache.set(id, proxy);
      reg.register(proxy, keysForState(entry.state, id));
      return Promise.resolve(proxy);
    },
    resolve_url: (url) => Promise.resolve(url)
  };
  for (const proxy of cache.values()) proxy.widget_manager = wm;
  Object.defineProperty(model, "widget_manager", {
    configurable: true,
    writable: true,
    value: wm
  });
  const rootId = typeof model.get === "function" && model.get("_myst_root_id") || null;
  const widgetIdField = typeof model.get === "function" && model.get("widget_id") || null;
  const anywidgetIdField = typeof model.get === "function" && model.get("_myst_anywidget_id") || null;
  reg.register(
    model,
    keysForState({ widget_id: widgetIdField, _anywidget_id: anywidgetIdField }, rootId)
  );
  reg.installLinks(typeof model.get === "function" && model.get("_myst_links") || []);
}

// src/runtime/index.ts
function normalizeModule(mod) {
  const candidate = mod && mod.default !== void 0 ? mod.default : mod;
  if (typeof candidate === "function") return candidate;
  return candidate || {};
}
async function initializeStaticWidget(userModule, args) {
  let widget = normalizeModule(userModule);
  setupModel(args.model);
  const nextArgs = Object.assign({}, args, { host: host() });
  if (typeof widget === "function") {
    widget = await widget(nextArgs);
  }
  let exports = void 0;
  if (widget && typeof widget.initialize === "function") {
    const result = await widget.initialize(nextArgs);
    if (result && typeof result === "object") exports = result;
  }
  const rootId = args.model && args.model.get && args.model.get("_myst_root_id");
  const widgetId = args.model && args.model.get && args.model.get("widget_id");
  const anywidgetId = args.model && args.model.get && args.model.get("_myst_anywidget_id");
  const reg = registry();
  reg.registerBinding(args.model, {
    keys: keysForState({ widget_id: widgetId, _anywidget_id: anywidgetId }, rootId),
    exports,
    render: (opts) => renderStaticWidget(widget, Object.assign({}, opts, { model: args.model }))
  });
  args.model.__mystUserWidget = widget;
  args.model.__mystUserExports = exports;
  return exports;
}
async function renderStaticWidget(userModule, args) {
  let widget = args.model && args.model.__mystUserWidget ? args.model.__mystUserWidget : normalizeModule(userModule);
  setupModel(args.model);
  if (args.model && args.model.get) {
    ensureShadowCss(
      args.el,
      args.model.get("_myst_css_text"),
      args.model.get("_myst_css_key")
    );
  }
  const nextArgs = Object.assign({}, args, { host: args.host || host() });
  if (typeof widget === "function") {
    widget = await widget(nextArgs);
    if (args.model) args.model.__mystUserWidget = widget;
  }
  if (widget && typeof widget.render === "function") return widget.render(nextArgs);
}
export {
  initializeStaticWidget,
  renderStaticWidget
};
`;

// src/transform/emit.ts
function shortHash(str) {
  return crypto.createHash("sha256").update(str).digest("hex").slice(0, 16);
}
function nanoidLike() {
  return crypto.randomBytes(10).toString("hex");
}
function writeFileIfChanged(filePath, content) {
  if (fs.existsSync(filePath) && fs.readFileSync(filePath, "utf8") === content) return;
  fs.writeFileSync(filePath, content);
}
function buildWrapperModule(source) {
  return `${RUNTIME_SOURCE}

const __mystUserModuleSource = ${JSON.stringify(source)};
const __mystUserModuleUrl = URL.createObjectURL(new Blob([__mystUserModuleSource], { type: 'text/javascript' }));
const __mystUserModulePromise = import(__mystUserModuleUrl);

async function __mystUserWidget() {
  const mod = await __mystUserModulePromise;
  return mod.default !== undefined ? mod.default : mod;
}

export default {
  async initialize(args) {
    return initializeStaticWidget(await __mystUserWidget(), args);
  },
  async render(args) {
    return renderStaticWidget(await __mystUserWidget(), args);
  },
};
`;
}
function ensureStaticAssetDir(sourcePath) {
  const sourceDir = path.dirname(sourcePath);
  const assetDir = path.join(sourceDir, ASSET_DIR_NAME);
  if (!fs.existsSync(assetDir)) fs.mkdirSync(assetDir, { recursive: true });
  writeFileIfChanged(path.join(assetDir, RUNTIME_MODULE_NAME), RUNTIME_SOURCE);
  return assetDir;
}
function emitStaticWidgetAssets(assetDir, esm, css, model) {
  const sourceEsmName = `${shortHash(esm)}.source.mjs`;
  writeFileIfChanged(path.join(assetDir, sourceEsmName), esm);
  const wrapperEsm = buildWrapperModule(esm);
  const wrapperEsmName = `${shortHash(wrapperEsm)}.wrapper.mjs`;
  writeFileIfChanged(path.join(assetDir, wrapperEsmName), wrapperEsm);
  let cssRel;
  if (css) {
    const cssName = `${shortHash(css)}.css`;
    writeFileIfChanged(path.join(assetDir, cssName), css);
    cssRel = `${ASSET_DIR_NAME}/${cssName}`;
  }
  const stateName = `${shortHash(JSON.stringify(model))}.state.json`;
  writeFileIfChanged(path.join(assetDir, stateName), `${JSON.stringify(model, null, 2)}
`);
  return {
    module: `${ASSET_DIR_NAME}/${wrapperEsmName}`,
    sourceModule: `${ASSET_DIR_NAME}/${sourceEsmName}`,
    runtime: `${ASSET_DIR_NAME}/${RUNTIME_MODULE_NAME}`,
    state: `${ASSET_DIR_NAME}/${stateName}`,
    css: cssRel
  };
}
function writeManifest(assetDir, sourcePath, manifestEntries, links = []) {
  const manifestPath = path.join(assetDir, MANIFEST_NAME);
  let manifest = { runtime: `${ASSET_DIR_NAME}/${RUNTIME_MODULE_NAME}`, pages: {} };
  if (fs.existsSync(manifestPath)) {
    try {
      const existing = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
      if (existing && typeof existing === "object") {
        manifest = {
          runtime: existing.runtime ?? manifest.runtime,
          pages: existing.pages ?? {}
        };
      }
    } catch {
    }
  }
  manifest.runtime = `${ASSET_DIR_NAME}/${RUNTIME_MODULE_NAME}`;
  manifest.pages[path.basename(sourcePath)] = {
    source: path.basename(sourcePath),
    widgets: manifestEntries,
    links
  };
  manifest.widgets = Object.values(manifest.pages).flatMap((page) => page.widgets ?? []);
  writeFileIfChanged(manifestPath, `${JSON.stringify(manifest, null, 2)}
`);
}

// src/transform/walk.ts
function findOutputsWithParent(tree) {
  const results = [];
  function visit(node, parent) {
    if (!node || typeof node !== "object") return;
    if (Array.isArray(node)) {
      for (const n of node) visit(n, parent);
      return;
    }
    if (node.type === "output") results.push({ node, parent });
    if (Array.isArray(node.children)) {
      for (const c of node.children) visit(c, node);
    }
  }
  visit(tree, null);
  return results;
}
function parseViewMime(viewMime) {
  if (!viewMime) return null;
  const raw = typeof viewMime.content === "string" ? viewMime.content : null;
  if (raw) {
    try {
      return JSON.parse(raw);
    } catch {
      return null;
    }
  }
  return viewMime.content ?? null;
}

// src/transform/rewrite.ts
function suppressJslinkReprs(tree) {
  const reprPattern = /^(?:Link|DirectionalLink)\(source=\(.+,\s*'[^']+'\),\s*target=\(.+,\s*'[^']+'\)\)\s*$/;
  for (const { node, parent } of findOutputsWithParent(tree)) {
    if (node.type !== "output") continue;
    const data = node?.jupyter_data?.data;
    if (!data) continue;
    const keys = Object.keys(data);
    if (keys.length !== 1 || keys[0] !== "text/plain") continue;
    const raw = data["text/plain"];
    const text = Array.isArray(raw?.content) ? raw.content.join("") : typeof raw?.content === "string" ? raw.content : Array.isArray(raw) ? raw.join("") : typeof raw === "string" ? raw : "";
    if (!reprPattern.test(text.trim())) continue;
    if (parent && Array.isArray(parent.children)) {
      const idx = parent.children.indexOf(node);
      if (idx >= 0) parent.children.splice(idx, 1);
    }
  }
}
function rewriteOutputNodeToAnywidget(node, rootId, model, assets) {
  delete node.jupyter_data;
  node.type = "anywidget";
  node.esm = assets.module;
  node.model = model;
  node.id = rootId;
  node.children = [];
  if (!node.key) node.key = nanoidLike();
}

// src/transform/widget-state.ts
function collectIpyRefs(value, out = []) {
  if (typeof value === "string") {
    if (value.startsWith(IPY_MODEL_PREFIX)) out.push(value.slice(IPY_MODEL_PREFIX.length));
    return out;
  }
  if (Array.isArray(value)) {
    for (const v of value) collectIpyRefs(v, out);
    return out;
  }
  if (value && typeof value === "object") {
    for (const v of Object.values(value)) collectIpyRefs(v, out);
    return out;
  }
  return out;
}
function findAnywidgetDescendant(modelId, widgetState, visited = /* @__PURE__ */ new Set()) {
  if (visited.has(modelId)) return null;
  visited.add(modelId);
  const entry = widgetState[modelId];
  if (!entry) return null;
  if (entry.model_module === "anywidget") return modelId;
  const children = entry.state?.children;
  if (Array.isArray(children)) {
    for (const child of children) {
      if (typeof child === "string" && child.startsWith(IPY_MODEL_PREFIX)) {
        const found = findAnywidgetDescendant(
          child.slice(IPY_MODEL_PREFIX.length),
          widgetState,
          visited
        );
        if (found) return found;
      }
    }
  }
  return null;
}
function collectJsLinks(widgetState) {
  const out = [];
  for (const [id, entry] of Object.entries(widgetState)) {
    const name = entry?.model_name;
    if (name !== "LinkModel" && name !== "DirectionalLinkModel") continue;
    const src = entry.state?.source;
    const tgt = entry.state?.target;
    if (!Array.isArray(src) || src.length !== 2) continue;
    if (!Array.isArray(tgt) || tgt.length !== 2) continue;
    const [sourceRef, sourceAttr] = src;
    const [targetRef, targetAttr] = tgt;
    if (typeof sourceRef !== "string" || !sourceRef.startsWith(IPY_MODEL_PREFIX)) continue;
    if (typeof targetRef !== "string" || !targetRef.startsWith(IPY_MODEL_PREFIX)) continue;
    out.push({
      id,
      bidirectional: name === "LinkModel",
      sourceId: sourceRef.slice(IPY_MODEL_PREFIX.length),
      sourceAttr,
      targetId: targetRef.slice(IPY_MODEL_PREFIX.length),
      targetAttr
    });
  }
  return out;
}
function buildSubModels(rootId, widgetState) {
  const out = {};
  const queue = [...collectIpyRefs(widgetState[rootId]?.state)];
  const seen = /* @__PURE__ */ new Set([rootId]);
  while (queue.length > 0) {
    const id = queue.shift();
    if (seen.has(id)) continue;
    seen.add(id);
    const entry = widgetState[id];
    if (!entry) continue;
    out[id] = {
      state: entry.state ?? {},
      buffers: entry.buffers ?? [],
      model_module: entry.model_module,
      model_name: entry.model_name
    };
    queue.push(...collectIpyRefs(entry.state));
  }
  return out;
}
function buildInitialModel(rootId, widgetState, links = []) {
  const rootEntry = widgetState[rootId];
  if (!rootEntry) return {};
  const state = rootEntry.state || {};
  const model = {};
  for (const [key, value] of Object.entries(state)) {
    if (key.startsWith("_")) continue;
    model[key] = value;
  }
  model._myst_buffers = rootEntry.buffers ?? [];
  model._myst_submodels = buildSubModels(rootId, widgetState);
  if (links.length > 0) model._myst_links = links;
  model._myst_root_id = rootId;
  if (state._anywidget_id) model._myst_anywidget_id = state._anywidget_id;
  return model;
}

// src/plugin.ts
var transformPlugin = () => async (tree, file) => {
  const sourcePath = file?.path ?? file?.history?.[0];
  if (!sourcePath || !sourcePath.endsWith(".ipynb")) return;
  if (!fs2.existsSync(sourcePath)) return;
  let notebook;
  try {
    notebook = JSON.parse(fs2.readFileSync(sourcePath, "utf8"));
  } catch {
    return;
  }
  const widgetState = notebook?.metadata?.widgets?.[WIDGET_STATE_MIME]?.state;
  if (!widgetState) return;
  const assetDir = ensureStaticAssetDir(sourcePath);
  const links = collectJsLinks(widgetState);
  let rewriteCount = 0;
  const manifestEntries = [];
  for (const { node } of findOutputsWithParent(tree)) {
    const data = node?.jupyter_data?.data;
    const viewMime = data?.[WIDGET_VIEW_MIME];
    if (!viewMime) continue;
    const view = parseViewMime(viewMime);
    const cellViewModelId = view?.model_id;
    if (!cellViewModelId) continue;
    const rootId = findAnywidgetDescendant(cellViewModelId, widgetState);
    if (!rootId) continue;
    const entry = widgetState[rootId];
    const state = entry.state || {};
    const esm = state._esm;
    const css = state._css;
    if (!esm) continue;
    const model = buildInitialModel(rootId, widgetState, links);
    if (css) {
      model._myst_css_text = css;
      model._myst_css_key = shortHash(css);
    }
    const assets = emitStaticWidgetAssets(assetDir, esm, css, model);
    rewriteOutputNodeToAnywidget(node, rootId, model, assets);
    manifestEntries.push({
      id: rootId,
      ...assets,
      buffers: model._myst_buffers?.length ?? 0,
      submodels: Object.keys(model._myst_submodels ?? {}).length
    });
    rewriteCount += 1;
  }
  if (links.length > 0) {
    suppressJslinkReprs(tree);
  }
  if (rewriteCount > 0) {
    writeManifest(assetDir, sourcePath, manifestEntries, links);
    const linkSummary = links.length > 0 ? `, ${links.length} jslink(s)` : "";
    console.log(
      `[anywidget-static-export] exported ${rewriteCount} widget(s)${linkSummary} in ${path2.basename(sourcePath)}`
    );
  }
};
var plugin = {
  name: "anywidget-static-export",
  transforms: [
    {
      name: "anywidget-from-notebook-outputs",
      stage: "project",
      plugin: transformPlugin
    }
  ]
};
var plugin_default = plugin;
export {
  plugin_default as default
};
