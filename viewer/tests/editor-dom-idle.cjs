/* Test-only completion protocol for the editor's asynchronous request chain.
 * Observe real WebCrypto + fetch + response.json, including requests started by
 * an earlier response (save -> history). Drain their continuations before the
 * DOM is inspected. The timeout is a failure bound, never a readiness delay. */
const assert = require("node:assert/strict");
const { webcrypto } = require("node:crypto");
const { setImmediate: nextTurn } = require("node:timers/promises");

function installEditorAsync(window, fetch, { beforeCrypto } = {}) {
  const pending = new Map();
  function track(label, invoke) {
    const result = Promise.resolve().then(invoke);
    const tracked = result.finally(() => pending.delete(tracked));
    pending.set(tracked, label);
    return tracked;
  }
  const subtle = new Proxy(webcrypto.subtle, {
    get(target, name) {
      const method = Reflect.get(target, name, target);
      if (typeof method !== "function") return method;
      return (...args) => track(`crypto.${String(name)}`, async () => {
        if (beforeCrypto) await beforeCrypto(name);
        return Reflect.apply(method, target, args);
      });
    },
  });
  Object.defineProperty(window, "crypto", { value: { subtle } });
  window.fetch = (...args) => track("fetch", () => fetch(...args)).then((response) => ({
    ...response,
    json: () => track("response.json", () => response.json()),
  }));

  async function idle() {
    let timeout;
    const deadline = new Promise((_resolve, reject) => {
      timeout = setTimeout(() => reject(Error(`Editor did not finish: ${[...pending.values()].join(", ")}`)), 10000);
    });
    const drain = async () => {
      do {
        await Promise.allSettled([...pending.keys()]);
        // Promise continuations can issue another request or render the result.
        // setImmediate is an event-loop boundary, not a guessed crypto duration.
        await nextTurn();
      } while (pending.size);
      assert.equal(window.document.querySelector('[data-act="load"]')?.disabled, false,
        "The request chain finished without the editor leaving its busy state");
    };
    try { await Promise.race([drain(), deadline]); } finally { clearTimeout(timeout); }
  }
  return { idle, pending: () => [...pending.values()] };
}

module.exports = { installEditorAsync };
