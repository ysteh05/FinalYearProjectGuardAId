(function (root) {
  "use strict";
  class RefreshCoordinator {
    constructor(resources, load, commit, changed = () => {}) {
      this.resources = resources; this.load = load; this.commit = commit;
      this.changed = changed; this.generation = 0; this.running = null;
      this.pending = false; this.status = {};
    }
    invalidate() {
      this.generation++;
      if (this.running) this.pending = true;
    }
    request(automatic = false) {
      if (this.running) {
        if (!automatic) this.pending = true;
        return this.running;
      }
      // Start in a microtask so even synchronous failures cannot retain a lock.
      this.running = Promise.resolve().then(async () => {
        do {
          this.pending = false;
          const generation = this.generation;
          await Promise.all(this.resources.map(async key => {
            try {
              const value = await this.load(key);
              if (generation !== this.generation) return;
              this.commit(key, value);
              this.status[key] = { lastSuccess: Date.now(), error: null };
            } catch (error) {
              if (generation !== this.generation) return;
              this.status[key] = { ...this.status[key], error: error.message || "Unavailable" };
            }
            this.changed(this.status);
          }));
        } while (this.pending);
      }).finally(() => { this.running = null; });
      return this.running;
    }
  }
  function draftPayload(base, values) {
    if (!base) throw new Error("Load configuration before editing.");
    return Object.assign(structuredClone(base), values);
  }
  const exports = { RefreshCoordinator, draftPayload };
  if (typeof module !== "undefined" && module.exports) module.exports = exports;
  else root.GuardAidRefresh = exports;
})(typeof window !== "undefined" ? window : globalThis);
