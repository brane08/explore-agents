/* Minimal progressive enhancement standing in for htmx (vendoring htmx was
 * not possible in this environment). Reads the same hx-*/ /* sse-* attributes
 * the templates carry, so swapping in htmx.min.js later needs no template
 * change: forms with hx-post swap into hx-target; sse-connect divs stream
 * events via EventSource and append each fragment. */
(function () {
  "use strict";

  function wireSSE(root) {
    root.querySelectorAll("[sse-connect]").forEach(function (el) {
      if (el.dataset.sseWired) return;
      el.dataset.sseWired = "1";
      var source = new EventSource(el.getAttribute("sse-connect"));
      var types = (el.getAttribute("sse-swap") || "message").split(",");
      types.forEach(function (type) {
        source.addEventListener(type.trim(), function (ev) {
          el.insertAdjacentHTML("beforeend", ev.data);
          if (type.trim() === "terminal") source.close();
        });
      });
      source.onerror = function () { source.close(); };
    });
  }

  function wireForms(root) {
    root.querySelectorAll("form[hx-post]").forEach(function (form) {
      if (form.dataset.hxWired) return;
      form.dataset.hxWired = "1";
      form.addEventListener("submit", function (ev) {
        ev.preventDefault();
        fetch(form.getAttribute("hx-post"), {
          method: "POST",
          body: new FormData(form),
        }).then(function (r) { return r.text(); }).then(function (html) {
          var target = document.querySelector(form.getAttribute("hx-target"));
          if (target) {
            target.innerHTML = html;
            wireSSE(target);
          }
        });
      });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    wireForms(document);
    wireSSE(document);
  });
})();
