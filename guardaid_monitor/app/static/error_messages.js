(function (root) {
  "use strict";

  const genericFailure = "The request could not be completed. Please try again.";
  const networkFailure = "Unable to reach the GuardAId server. Check the connection and try again.";
  const timeoutFailure = "The request timed out. Please try again.";
  const permissionFailure = "You do not have permission to do that. Ask an administrator if you need access.";
  const notFoundFailure = "This item is no longer available. Refresh and try again.";
  const conflictFailure = "This information changed while you were working. Review the latest details and try again.";
  const validationFailure = "Some of the details are not valid. Check them and try again.";
  const rateLimitFailure = "Too many requests. Wait a moment and try again.";
  const serverFailure = "GuardAId is temporarily unavailable. Please try again shortly.";
  const incompleteFailure = "GuardAId returned an incomplete response. Please try again.";

  // Fragments that only occur in exception text, transport dumps, parser
  // output or absolute paths. A user-facing sentence never needs them.
  const unsafeFragments = [
    "exception", "stack trace", "traceback", "bad state", "dev.flutter",
    "pigeon", "socket", "platform", "clientexception", "checkpoint",
    "file://", "http://", "https://", "package:", ".dart",
    "asynchronous suspension", "at character", "at offset", "null check",
    "json", "typeerror", "nosuchmethod", "future<", "error:", "stacktrace",
    "aborterror", "failed to fetch", "networkerror", "load failed",
  ];
  const statusPattern = /\b[1-5]\d{2}\b/;
  const sentenceEnd = /[.!?]$/;
  const wordStart = /^[A-Za-z0-9]/;
  const machineCodePattern = /^[a-z][a-z0-9_]{2,63}$/;

  function isMachineCode(value) {
    return typeof value === "string" && machineCodePattern.test(value.trim());
  }

  function isSafeUserMessage(value) {
    return isSafeText(value, true);
  }

  function readableDetail(value) {
    if (!isSafeText(value, false)) return null;
    const text = value.trim();
    return sentenceEnd.test(text) ? text : `${text}.`;
  }

  function isSafeText(value, requireSentenceEnd) {
    if (typeof value !== "string") return false;
    const text = value.trim();
    if (text.length < 4 || text.length > 200) return false;
    if (/[\r\n]/.test(text)) return false;
    const lower = text.toLowerCase();
    if (unsafeFragments.some(fragment => lower.includes(fragment))) return false;
    if (statusPattern.test(text)) return false;
    if (/[{}\[\]"]/.test(text)) return false;
    if (text.includes("_") || text.includes("/") || text.includes("\\")) return false;
    if (!wordStart.test(text)) return false;
    if (requireSentenceEnd && !sentenceEnd.test(text)) return false;
    return true;
  }

  const fieldLabels = {
    weapon: "Weapon settings",
    violence: "Violence settings",
    vlm: "VLM settings",
    nRequired: "Weapon N",
    mWindow: "Weapon M window",
    sampleFps: "Weapon sample rate",
    segmentSeconds: "Violence segment",
    strideSeconds: "Violence stride",
    detectorInputFrames: "Detector input frames",
    inputFps: "VLM input rate",
    maxOutputTokens: "VLM output limit",
    configVersion: "Configuration version",
    displayName: "Display name",
    email: "Email address",
    name: "Name",
    location: "Location",
  };
  const groupLabels = new Set(["Weapon settings", "Violence settings", "VLM settings"]);

  function fieldLabel(loc) {
    if (!Array.isArray(loc)) return null;
    for (let index = loc.length - 1; index >= 0; index -= 1) {
      const part = loc[index];
      if (typeof part === "string" && fieldLabels[part]) {
        return { label: fieldLabels[part], group: groupLabels.has(fieldLabels[part]) };
      }
    }
    return null;
  }

  function validationMessage(detail) {
    const collected = [];
    for (const item of detail) {
      if (!item || typeof item !== "object") continue;
      if (typeof item.msg !== "string") continue;
      const business = item.type === "value_error" || item.msg.trimStart().toLowerCase().startsWith("value error,");
      let message = item.msg.replace(/^Value error,\s*/, "").trim();
      if (!message || message.length > 180) continue;
      const lower = message.toLowerCase();
      if (unsafeFragments.some(fragment => lower.includes(fragment))) continue;
      if (/[{}\[\]]/.test(message)) continue;
      collected.push({ message, loc: item.loc, business });
    }
    if (!collected.length) return null;
    const chosen = collected.find(item => item.business) || collected[0];
    let message = chosen.message;
    const field = fieldLabel(chosen.loc);
    const lower = message.toLowerCase();
    if (field && !field.group && !lower.startsWith(field.label.toLowerCase())) {
      message = `${field.label}: ${message}`;
    }
    return message;
  }

  function statusMessage(status, detail, fallback) {
    const safeDetail = readableDetail(detail);
    if (status === 401) return "Your session has expired. Sign in again.";
    if (status === 429) return rateLimitFailure;
    if (status >= 500) return serverFailure;
    if (safeDetail) return safeDetail;
    if (status === 403) return permissionFailure;
    if (status === 404) return notFoundFailure;
    if (status === 409) return conflictFailure;
    if (status === 422) return validationFailure;
    return fallback || genericFailure;
  }

  function describeHttpFailure(status, statusText, detail, fallback) {
    let text = null;
    let code = null;
    if (typeof detail === "string") {
      text = detail;
    } else if (Array.isArray(detail)) {
      text = validationMessage(detail);
    } else if (detail && typeof detail === "object") {
      if (typeof detail.message === "string") text = detail.message;
      if (typeof detail.code === "string" && detail.code.trim()) code = detail.code.trim();
    }
    if (text && isMachineCode(text)) {
      code = code || text.trim();
      text = null;
    }
    return { message: statusMessage(status, text, fallback), code };
  }

  function describeError(error, fallback) {
    if (!error) return fallback || genericFailure;
    if (error.name === "AbortError") return timeoutFailure;
    if (typeof error.status === "number") {
      return statusMessage(error.status, error.detail, fallback);
    }
    const message = typeof error.message === "string" ? error.message : "";
    if (/failed to fetch|networkerror|load failed|network request failed/i.test(message)) return networkFailure;
    if (error.name === "TypeError" && !message) return networkFailure;
    if (isSafeUserMessage(message)) return message;
    return fallback || genericFailure;
  }

  function describeJobFailure(rawError, status, progressPercent) {
    const text = String(rawError || "").toLowerCase();
    const failed = String(status || "").toLowerCase() === "failed";
    const starting = !(Number(progressPercent) > 0);
    if (/camera not found/.test(text)) {
      return "This camera is no longer available. Refresh the page and try again.";
    }
    if (/(checkpoint|model|weights|detector|cuda|ollama|torch|runtimeerror)/.test(text)) {
      return starting
        ? "Analysis could not start because the detection model is unavailable. Ask an administrator to check the model setup."
        : "Analysis stopped because the detection model became unavailable. Ask an administrator to check the model setup.";
    }
    if (!failed) {
      return "Analysis needs attention. Ask an administrator to check the server log.";
    }
    return starting
      ? "Analysis could not start. Ask an administrator to check the server log, then try again."
      : "Analysis stopped because GuardAId hit an unexpected problem. Ask an administrator to check the server log, then try again.";
  }

  const exports = {
    genericFailure, networkFailure, timeoutFailure, permissionFailure,
    notFoundFailure, conflictFailure, validationFailure, rateLimitFailure,
    serverFailure, incompleteFailure,
    isMachineCode, isSafeUserMessage, describeHttpFailure, describeError, describeJobFailure,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = exports;
  else root.GuardAidErrors = exports;
})(typeof window !== "undefined" ? window : globalThis);
