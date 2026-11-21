// In-place page updates. `cccMorph.morph(element, html)` makes `element`'s children match `html`
// by changing only what differs. Nodes that did not change are never replaced, so a page that is
// updated in the background keeps what the person is doing on it:
//
//  * the focused field, its caret and everything typed into any field (a field's value is never
//    written by a morph; only its other attributes are);
//  * scroll positions (the page's and every scrollable box inside it);
//  * which <details> are open;
//  * event handlers on nodes that stay.
//
// Rules the pages follow so that this holds:
//  * give a repeated block a stable `data-key` (or an id) so it is matched by identity, not by position;
//  * attach handlers by assigning `onclick`/`oninput` (an assignment replaces itself), or delegate from a
//    stable ancestor, so running the binder after every morph never stacks listeners;
//  * `data-morph-skip` marks a box whose children the page maintains itself (for example the chat feed).
window.cccMorph = (function () {
  "use strict";
  const FIELDS = new Set(["INPUT", "TEXTAREA", "SELECT"]);

  const keyOf = (node) => (node.nodeType === 1 ? node.getAttribute("data-key") || (node.id ? "#" + node.id : null) : null);
  const sameKind = (a, b) => a.nodeType === b.nodeType && (a.nodeType !== 1 || a.tagName === b.tagName);

  function keepAttribute(live, name) {
    if (live.tagName === "DETAILS" && name === "open") return true;
    if (FIELDS.has(live.tagName) && (name === "value" || name === "checked" || name === "selected")) return true;
    if (name === "data-morph-keep") return true;
    return false;
  }

  function syncAttributes(live, next) {
    for (const attribute of Array.from(live.attributes)) {
      if (!next.hasAttribute(attribute.name) && !keepAttribute(live, attribute.name)) live.removeAttribute(attribute.name);
    }
    for (const attribute of Array.from(next.attributes)) {
      if (live.getAttribute(attribute.name) !== attribute.value && !keepAttribute(live, attribute.name)) {
        live.setAttribute(attribute.name, attribute.value);
      }
    }
  }

  function morphNode(live, next) {
    if (live.nodeType !== 1) {
      if (live.nodeValue !== next.nodeValue) live.nodeValue = next.nodeValue;
      return;
    }
    syncAttributes(live, next);
    if (live.tagName === "TEXTAREA" || live.tagName === "INPUT" || live.hasAttribute("data-morph-skip")) return;
    morphChildren(live, next);
  }

  function morphChildren(live, next) {
    const keyed = new Map();
    for (const node of Array.from(live.childNodes)) {
      const key = keyOf(node);
      if (key && !keyed.has(key)) keyed.set(key, node);
    }
    const used = new Set();
    let cursor = live.firstChild;
    for (const wanted of Array.from(next.childNodes)) {
      while (cursor && used.has(cursor)) cursor = cursor.nextSibling;
      const key = keyOf(wanted);
      let match = null;
      if (key) {
        match = keyed.get(key) || null;
        if (match && used.has(match)) match = null;
      } else {
        let candidate = cursor;
        while (candidate && (used.has(candidate) || keyOf(candidate))) candidate = candidate.nextSibling;
        if (candidate && sameKind(candidate, wanted)) match = candidate;
      }
      if (match) {
        used.add(match);
        if (match === cursor) cursor = cursor.nextSibling;
        else live.insertBefore(match, cursor);
        morphNode(match, wanted);
      } else {
        const fresh = wanted.cloneNode(true);
        used.add(fresh);
        live.insertBefore(fresh, cursor);
      }
    }
    const active = document.activeElement;
    for (const node of Array.from(live.childNodes)) {
      if (used.has(node)) continue;
      // A field the person is typing in is never taken away from them mid-keystroke.
      if (active && node.nodeType === 1 && node.contains(active)) continue;
      node.remove();
    }
  }

  function parse(html, localize) {
    const holder = document.createElement("div");
    holder.innerHTML = html;
    if (localize) localize(holder);
    return holder;
  }

  return {
    morph(element, html, localize) {
      morphChildren(element, parse(html, localize));
    },
    parse,
  };
})();
