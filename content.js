/*
 * mdclip content script — convert, copy, confirm.
 *
 * Runs in the extension's ISOLATED WORLD, which is why this works on sites
 * whose Content-Security-Policy blocks bookmarklets and injected <script>
 * tags: an isolated-world script is not subject to the page's CSP. That is the
 * practical reason this is an extension and not a bookmarklet.
 */
(function () {
  "use strict";

  function toast(message, ok) {
    // Shadow DOM so the page's own CSS cannot restyle or hide the confirmation
    // -- plenty of sites set aggressive `div { }` rules that would otherwise
    // make this invisible, and a silent copy is indistinguishable from failure.
    var host = document.createElement("div");
    host.style.cssText = "all:initial;position:fixed;z-index:2147483647;top:16px;right:16px;";
    var root = host.attachShadow({ mode: "closed" });
    var box = document.createElement("div");
    box.textContent = message;
    box.style.cssText = [
      "font:600 13px/1.45 ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif",
      "padding:10px 14px",
      "border-radius:10px",
      "color:#fff",
      "background:" + (ok ? "#15803d" : "#b91c1c"),
      "box-shadow:0 6px 24px rgba(0,0,0,.28)",
      "max-width:320px",
    ].join(";");
    root.appendChild(box);
    document.documentElement.appendChild(host);
    setTimeout(function () { host.remove(); }, ok ? 2600 : 5000);
  }

  function copy(text) {
    // navigator.clipboard is the modern path but throws when the document is
    // not focused -- which happens if the click landed on the toolbar rather
    // than the page. execCommand still works there, so keep both.
    return new Promise(function (resolve, reject) {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(resolve, function () { fallback(); });
      } else {
        fallback();
      }
      function fallback() {
        try {
          var ta = document.createElement("textarea");
          ta.value = text;
          ta.setAttribute("readonly", "");
          ta.style.cssText = "position:fixed;top:-1000px;left:-1000px;opacity:0;";
          document.body.appendChild(ta);
          ta.select();
          ta.setSelectionRange(0, text.length);
          var ok = document.execCommand("copy");
          ta.remove();
          ok ? resolve() : reject(new Error("clipboard write refused"));
        } catch (e) { reject(e); }
      }
    });
  }

  try {
    if (!window.MDCLIP || !window.MDCLIP.convert) {
      toast("mdclip: converter failed to load", false);
      return;
    }
    var mode = window.__MDCLIP_MODE === "article" ? "article" : "page";
    var md = window.MDCLIP.convert(document, { mode: mode });
    if (!md || md.trim().length < 2) {
      toast("mdclip: nothing readable found on this page", false);
      return;
    }

    copy(md).then(function () {
      var chars = md.length;
      // A rough token estimate is genuinely useful here: the whole point is
      // pasting into a chat, and a long page can blow the context window. ~4
      // chars per token is the usual English approximation.
      var tokens = Math.round(chars / 4);
      var size = chars > 1000 ? (chars / 1000).toFixed(1) + "k" : String(chars);
      toast("Copied " + (mode === "article" ? "article" : "page") +
            " · " + size + " chars · ~" + tokens.toLocaleString() + " tokens", true);
    }, function (err) {
      toast("mdclip: could not write to clipboard (" + (err.message || err) + ")", false);
    });
  } catch (e) {
    toast("mdclip error: " + (e && e.message ? e.message : e), false);
  }
})();
