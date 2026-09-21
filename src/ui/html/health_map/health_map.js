/* CORTEX PROJECT HEALTH, renderer.
 *
 * Receives a health-map payload from Python (src/core/project_health.py) over
 * QWebChannel and draws it: a layered dependency graph in the centre, a file
 * tree on the left, a detail pane on the right.
 *
 * The payload shape is:
 *   { project_name, project_root, generated_at, scan_ms, reused_files,
 *     from_cache, empty,
 *     summary: { files, symbols, broken, warning, affected, new, healthy,
 *                edges, languages },
 *     nodes:   [ { id, name, kind, path, lang, status, symbols, size, new, deep } ],
 *     symbols: [ { id, name, qualname, kind, file, line, status, parent_node } ],
 *     edges:   [ { from, to, kind } ],
 *     problems:[ { status, file, line, message, detail, node_id } ],
 *     notes:   [ str ] }
 *
 * Two rules this file never breaks:
 *  1. Rendering must never throw. A partial payload still draws something,
 *     because a blank panel with no explanation reads as "the IDE is broken".
 *  2. The graph is capped. A 20k-file repo must not produce 20k SVG nodes;
 *     we draw the problem subgraph plus the most connected files and say so.
 */
(function () {
  'use strict';

  var SVG_NS = 'http://www.w3.org/2000/svg';

  var NODE_W = 214;
  var NODE_H = 62;
  var GAP_X = 96;
  var GAP_Y = 20;
  var PAD = 34;
  var MAX_RENDER = 150;          // hard cap on drawn nodes
  var SEVERITY = { broken: 0, warning: 1, affected: 2, new: 3, healthy: 4 };

  var state = {
    payload: null,
    byId: {},                    // node id -> node
    symbolsByFile: {},           // rel path -> [symbol]
    problemsByFile: {},          // rel path -> [problem]
    outEdges: {},                // node id -> [node id]
    inEdges: {},                 // node id -> [node id]
    selected: null,
    query: '',
    filters: { broken: true, warning: true, affected: true, new: true, healthy: true },
    scale: 1,
    laid: [],                    // [{node, x, y}]
    truncated: 0,
    scanning: false,             // a background scan is in flight
    view: 'graph'                // 'graph' | 'dead' - centre panel tab
  };

  var bridge = null;
  var el = {};

  // ── small helpers ───────────────────────────────────────────────────

  function $(id) { return document.getElementById(id); }

  function svg(tag, attrs) {
    var n = document.createElementNS(SVG_NS, tag);
    if (attrs) { for (var k in attrs) { n.setAttribute(k, attrs[k]); } }
    return n;
  }

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function fmtBytes(n) {
    n = Number(n) || 0;
    if (n < 1024) return n + ' B';
    if (n < 1048576) return (n / 1024).toFixed(1) + ' KB';
    return (n / 1048576).toFixed(2) + ' MB';
  }

  function fmtClock(ts) {
    if (!ts) return '';
    var d = new Date(ts * 1000);
    function p(v) { return v < 10 ? '0' + v : '' + v; }
    return p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
  }

  function statusOf(node) {
    return (node && node.status) || 'healthy';
  }

  // ── event log ───────────────────────────────────────────────────────

  function logEvent(type, text, level) {
    var box = el.events;
    if (!box) return;
    var row = document.createElement('div');
    row.className = 'event' + (level ? ' ' + level : '');
    row.innerHTML = '<span class="time">' + fmtClock(Date.now() / 1000) + '</span>' +
      '<span class="etype">' + esc(type) + '</span>' +
      '<span>' + esc(text) + '</span>';
    box.appendChild(row);
    while (box.children.length > 60) { box.removeChild(box.firstChild); }
    box.parentNode.scrollTop = box.parentNode.scrollHeight;
  }

  var toastTimer = null;
  function showToast(level, message) {
    var t = el.toast;
    if (!t) return;
    t.className = 'toast show' + (level && level !== 'info' ? ' ' + level : '');
    t.textContent = message;
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { t.className = 'toast'; }, 4200);
  }

  function setLive(text, scanning) {
    if (el.liveText) el.liveText.textContent = text;
    if (el.liveBadge) el.liveBadge.classList.toggle('scanning', !!scanning);
  }

  // ── state states ────────────────────────────────────────────────────

  function showState(which, message) {
    var map = { loading: el.stateLoading, empty: el.stateEmpty, error: el.stateError };
    for (var k in map) { if (map[k]) map[k].hidden = (k !== which); }
    if (which === 'empty' && message && el.emptyText) el.emptyText.textContent = message;
    if (which === 'error' && message && el.errorText) el.errorText.textContent = message;
    if (el.graph) el.graph.style.display = which ? 'none' : '';
  }

  // ── indexing ────────────────────────────────────────────────────────

  function indexPayload(p) {
    state.byId = {};
    state.symbolsByFile = {};
    state.problemsByFile = {};
    state.outEdges = {};
    state.inEdges = {};

    var nodes = Array.isArray(p.nodes) ? p.nodes : [];
    nodes.forEach(function (n) {
      if (!n || !n.id) return;
      state.byId[n.id] = n;
      state.outEdges[n.id] = [];
      state.inEdges[n.id] = [];
    });

    (Array.isArray(p.symbols) ? p.symbols : []).forEach(function (s) {
      if (!s || !s.file) return;
      (state.symbolsByFile[s.file] = state.symbolsByFile[s.file] || []).push(s);
    });

    (Array.isArray(p.problems) ? p.problems : []).forEach(function (pr) {
      if (!pr || !pr.file) return;
      (state.problemsByFile[pr.file] = state.problemsByFile[pr.file] || []).push(pr);
    });

    (Array.isArray(p.edges) ? p.edges : []).forEach(function (e) {
      if (!e || !state.byId[e.from] || !state.byId[e.to]) return;
      if (e.from === e.to) return;
      state.outEdges[e.from].push(e.to);
      state.inEdges[e.to].push(e.from);
    });
  }

  // ── layout ──────────────────────────────────────────────────────────

  /* Column = dependency depth. Column 0 holds entry points (nothing in the
     project imports them), and each later column holds what the previous one
     depends on. That reads left-to-right as "callers -> callees", which is
     the direction a user traces a break through.

     Longest-path layering, not BFS-shortest: a file that is both a direct
     entry point and a deep dependency belongs in the deep column, otherwise
     the edges crossing back over the graph make it unreadable.

     Cycles are condensed first (Tarjan SCC): plain relaxation on a graph
     with an import cycle grows every cycle member's depth on every pass —
     that was the old "DEPTH 452" bug, hundreds of mostly-empty columns.
     All files in one cycle now share a single depth, and the longest path
     is computed on the cycle-free condensation, so depth == real import
     distance. */
  function computeLayers(ids) {
    var inSet = {};
    ids.forEach(function (id) { inSet[id] = true; });
    function kids(v) {
      return (state.outEdges[v] || []).filter(function (to) { return inSet[to]; });
    }

    // Iterative Tarjan — no recursion, so deep chains can't blow the stack.
    var index = {}, low = {}, onStack = {}, stack = [], counter = 0;
    var sccs = [], sccOf = {};
    ids.forEach(function (root) {
      if (index[root] !== undefined) return;
      index[root] = low[root] = counter++;
      stack.push(root); onStack[root] = true;
      var work = [[root, 0]];
      while (work.length) {
        var frame = work[work.length - 1];
        var v = frame[0];
        var children = kids(v);
        if (frame[1] < children.length) {
          var w = children[frame[1]++];
          if (index[w] === undefined) {
            index[w] = low[w] = counter++;
            stack.push(w); onStack[w] = true;
            work.push([w, 0]);
          } else if (onStack[w]) {
            low[v] = Math.min(low[v], index[w]);
          }
        } else {
          if (low[v] === index[v]) {
            var comp = [], m;
            do { m = stack.pop(); onStack[m] = false; comp.push(m); } while (m !== v);
            var sccId = sccs.length;
            sccs.push(comp);
            comp.forEach(function (x) { sccOf[x] = sccId; });
          }
          work.pop();
          if (work.length) {
            var p = work[work.length - 1][0];
            low[p] = Math.min(low[p], low[v]);
          }
        }
      }
    });

    // Tarjan emits SCCs in reverse topological order, so walking the list
    // backwards propagates depths along the condensation DAG exactly once.
    var sccDepth = sccs.map(function () { return 0; });
    for (var s = sccs.length - 1; s >= 0; s--) {
      sccs[s].forEach(function (v) {
        kids(v).forEach(function (to) {
          var t = sccOf[to];
          if (t !== s && sccDepth[t] < sccDepth[s] + 1) sccDepth[t] = sccDepth[s] + 1;
        });
      });
    }

    var depth = {};
    ids.forEach(function (id) { depth[id] = sccDepth[sccOf[id]]; });
    return depth;
  }

  /* Which nodes actually get drawn. Everything with a problem is always
     included, plus the files that touch them, then the most connected files
     until the cap. Dropping the least connected healthy leaves keeps the
     picture honest: the omitted nodes are the ones carrying no information
     about risk. */
  function pickRenderSet(nodes) {
    if (nodes.length <= MAX_RENDER) return { ids: nodes.map(function (n) { return n.id; }), dropped: 0 };

    var chosen = {};
    function take(id) {
      if (!chosen[id] && state.byId[id]) chosen[id] = true;
    }

    var ranked = nodes.slice().sort(function (a, b) {
      var s = (SEVERITY[statusOf(a)] || 9) - (SEVERITY[statusOf(b)] || 9);
      if (s !== 0) return s;
      return degree(b.id) - degree(a.id);
    });

    ranked.forEach(function (n) {
      if (Object.keys(chosen).length >= MAX_RENDER) return;
      if (statusOf(n) === 'healthy') return;
      take(n.id);
      (state.outEdges[n.id] || []).forEach(take);
      (state.inEdges[n.id] || []).forEach(take);
    });

    ranked.forEach(function (n) {
      if (Object.keys(chosen).length >= MAX_RENDER) return;
      take(n.id);
    });

    var ids = Object.keys(chosen);
    return { ids: ids, dropped: nodes.length - ids.length };
  }

  function degree(id) {
    return (state.outEdges[id] || []).length + (state.inEdges[id] || []).length;
  }

  function layout(nodes) {
    var picked = pickRenderSet(nodes);
    state.truncated = picked.dropped;

    var depth = computeLayers(picked.ids);
    var columns = {};
    picked.ids.forEach(function (id) {
      var d = depth[id] || 0;
      (columns[d] = columns[d] || []).push(id);
    });

    var laid = [];
    // Compact columns: x is driven by the column's INDEX among populated
    // depths, never by the raw depth value. A gap in depths (nothing
    // rendered at DEPTH 3..447) must not allocate 445 empty columns of
    // scroll space — the user should scroll past nodes, not past voids.
    var depthKeys = Object.keys(columns).map(Number).sort(function (a, b) { return a - b; });
    var colIndex = {};
    depthKeys.forEach(function (d, i) { colIndex[d] = i; });
    state.colIndex = colIndex;
    depthKeys.forEach(function (d) {
      var col = columns[d];
      // Problems first within a column, then alphabetical, so a rescan does
      // not shuffle the picture when only one file changed status.
      col.sort(function (a, b) {
        var na = state.byId[a], nb = state.byId[b];
        var s = (SEVERITY[statusOf(na)] || 9) - (SEVERITY[statusOf(nb)] || 9);
        if (s !== 0) return s;
        return String(na.path).localeCompare(String(nb.path));
      });
      col.forEach(function (id, i) {
        laid.push({
          node: state.byId[id],
          layer: d,
          x: PAD + colIndex[d] * (NODE_W + GAP_X),
          y: PAD + 22 + i * (NODE_H + GAP_Y)
        });
      });
    });

    state.laid = laid;
    return laid;
  }

  // ── graph rendering ─────────────────────────────────────────────────

  function edgePath(a, b) {
    var x1 = a.x + NODE_W, y1 = a.y + NODE_H / 2;
    var x2 = b.x, y2 = b.y + NODE_H / 2;

    if (x2 > x1 + 8) {
      var dx = Math.max(28, (x2 - x1) / 2);
      return 'M' + x1 + ' ' + y1 + ' C' + (x1 + dx) + ' ' + y1 + ',' + (x2 - dx) + ' ' + y2 + ',' + x2 + ' ' + y2;
    }
    // Back edge (cycle or same column): swing out below so it stays legible.
    // Distance measured in COLUMNS, not raw depth — with compacted columns a
    // depth gap must not fling the curve hundreds of pixels down.
    var ci = state.colIndex || {};
    var ca = ci[a.layer] !== undefined ? ci[a.layer] : a.layer;
    var cb = ci[b.layer] !== undefined ? ci[b.layer] : b.layer;
    var drop = 34 + Math.abs(ca - cb) * 10;
    return 'M' + x1 + ' ' + y1 +
      ' C' + (x1 + 60) + ' ' + (y1 + drop) + ',' + (x2 - 60) + ' ' + (y2 + drop) + ',' + x2 + ' ' + y2;
  }

  function renderGraph() {
    var g = el.graph;
    if (!g) return;
    while (g.firstChild) g.removeChild(g.firstChild);

    var laid = state.laid;
    if (!laid.length) return;

    var pos = {};
    laid.forEach(function (L) { pos[L.node.id] = L; });

    var maxX = 0, maxY = 0;
    laid.forEach(function (L) {
      maxX = Math.max(maxX, L.x + NODE_W);
      maxY = Math.max(maxY, L.y + NODE_H);
    });

    var defs = svg('defs');
    // Arrowheads per wire state. Selected-node tracing recolours both the
    // stroke and its arrowhead so the direction reads at a glance:
    // green = this file imports (current flows out), yellow = imported by
    // (current flows in), red = the connection touches a broken file.
    var MARKER_FILL = {
      edge: 'var(--edge)',
      impact: 'var(--edge-impact)',
      out: 'var(--healthy)',
      in: 'var(--warning)',
      bad: 'var(--broken)'
    };
    Object.keys(MARKER_FILL).forEach(function (kind) {
      var m = svg('marker', {
        id: 'arrow-' + kind, markerWidth: '9', markerHeight: '9',
        refX: '8', refY: '3.2', orient: 'auto', markerUnits: 'userSpaceOnUse'
      });
      m.appendChild(svg('path', { d: 'M0,0 L9,3.2 L0,6.4 Z', fill: MARKER_FILL[kind] }));
      defs.appendChild(m);
    });
    g.appendChild(defs);

    var root = svg('g', { id: 'viewport' });
    root.setAttribute('transform', 'scale(' + state.scale + ')');
    g.appendChild(root);

    // Layer headers — positioned by the same compact column index the nodes
    // use (state.colIndex), labelled with the real depth. If depths have
    // gaps the labels stay truthful ("DEPTH 4" next to "DEPTH 9") while the
    // empty columns take zero width.
    var layers = {};
    laid.forEach(function (L) { layers[L.layer] = true; });
    Object.keys(layers).map(Number).sort(function (a, b) { return a - b; }).forEach(function (d) {
      var ci = (state.colIndex && state.colIndex[d] !== undefined) ? state.colIndex[d] : d;
      var t = svg('text', {
        class: 'layer-label',
        x: PAD + ci * (NODE_W + GAP_X),
        y: 18
      });
      t.textContent = d === 0 ? 'ENTRY POINTS' : 'DEPTH ' + d;
      root.appendChild(t);
    });

    var edgeLayer = svg('g', { id: 'edges' });
    var nodeLayer = svg('g', { id: 'nodes' });
    root.appendChild(edgeLayer);
    root.appendChild(nodeLayer);

    var drawnEdges = 0;
    var p = state.payload || {};
    (Array.isArray(p.edges) ? p.edges : []).forEach(function (e) {
      var a = pos[e.from], b = pos[e.to];
      if (!a || !b || e.from === e.to) return;
      var target = state.byId[e.to];
      var isImpact = statusOf(target) === 'broken' || statusOf(state.byId[e.from]) === 'affected';
      var markerId = isImpact ? 'impact' : 'edge';
      var path = svg('path', {
        class: 'edge' + (isImpact ? ' impact' : ''),
        d: edgePath(a, b),
        'marker-end': 'url(#arrow-' + markerId + ')',
        'data-marker': markerId,
        'data-from': e.from,
        'data-to': e.to
      });
      edgeLayer.appendChild(path);
      drawnEdges++;
    });

    laid.forEach(function (L) {
      nodeLayer.appendChild(buildNode(L));
    });

    g.setAttribute('width', Math.ceil(maxX * state.scale + PAD));
    g.setAttribute('height', Math.ceil(maxY * state.scale + PAD));
    g.setAttribute('viewBox', '0 0 ' + (maxX + PAD) + ' ' + (maxY + PAD));

    if (el.graphHint) {
      var bits = [drawnEdges + ' edge' + (drawnEdges === 1 ? '' : 's')];
      if (state.truncated > 0) {
        bits.push('showing ' + laid.length + ' of ' + Object.keys(state.byId).length +
          ' files (' + state.truncated + ' healthy leaves omitted)');
      }
      el.graphHint.textContent = bits.join(' · ');
    }

    applyVisualFilter();
  }

  function buildNode(L) {
    var n = L.node;
    var st = statusOf(n);
    var g = svg('g', {
      class: 'node ' + st,
      transform: 'translate(' + L.x + ',' + L.y + ')',
      'data-id': n.id,
      tabindex: '0',
      role: 'button',
      'aria-label': n.path + ', ' + st
    });

    g.appendChild(svg('rect', { width: NODE_W, height: NODE_H, rx: 8, ry: 8 }));

    var nm = svg('text', { class: 'nm', x: 13, y: 22 });
    nm.textContent = truncate(n.name, 30);
    g.appendChild(nm);

    var sub = svg('text', { class: 'sub', x: 13, y: 38 });
    sub.textContent = truncate(dirOf(n.path), 34) + ' · ' + (n.symbols || 0) + ' sym';
    g.appendChild(sub);

    var stx = svg('text', { class: 'st', x: 13, y: 53 });
    stx.textContent = '● ' + st.toUpperCase();
    g.appendChild(stx);

    g.addEventListener('click', function () { select(n.id); });
    g.addEventListener('keydown', function (ev) {
      if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); select(n.id); }
    });
    return g;
  }

  function truncate(s, n) {
    s = String(s == null ? '' : s);
    return s.length > n ? s.slice(0, n - 1) + '…' : s;
  }

  function dirOf(path) {
    var p = String(path || '');
    var i = p.lastIndexOf('/');
    return i <= 0 ? (i === 0 ? '/' : '.') : p.slice(0, i);
  }

  // ── tree ────────────────────────────────────────────────────────────

  function renderTree() {
    var box = el.tree;
    if (!box) return;
    box.innerHTML = '';

    var nodes = (state.payload && state.payload.nodes) || [];
    if (!nodes.length) {
      box.innerHTML = '<div class="more">No files.</div>';
      return;
    }

    // Group by directory, problems first inside each group.
    var groups = {};
    nodes.forEach(function (n) {
      var d = dirOf(n.path);
      (groups[d] = groups[d] || []).push(n);
    });

    var dirs = Object.keys(groups).sort(function (a, b) {
      var sa = worst(groups[a]), sb = worst(groups[b]);
      if (sa !== sb) return sa - sb;
      return a.localeCompare(b);
    });

    dirs.forEach(function (d) {
      var items = groups[d].sort(function (a, b) {
        var s = (SEVERITY[statusOf(a)] || 9) - (SEVERITY[statusOf(b)] || 9);
        return s !== 0 ? s : String(a.name).localeCompare(String(b.name));
      });

      var head = document.createElement('div');
      head.className = 'tree-dir';
      head.textContent = '▾ ' + d + '  (' + items.length + ')';
      head.title = d;
      box.appendChild(head);

      var wrap = document.createElement('div');
      wrap.style.marginLeft = '9px';
      items.forEach(function (n) {
        var row = document.createElement('div');
        row.className = 'tree-file';
        row.setAttribute('role', 'treeitem');
        row.dataset.id = n.id;
        row.dataset.path = n.path;
        row.dataset.status = statusOf(n);
        row.title = n.path;
        row.innerHTML = '<i class="i-' + esc(statusOf(n)) + '"></i>' +
          '<span class="nm">' + esc(n.name) + '</span>' +
          '<span class="ct">' + (n.symbols || 0) + '</span>';
        row.addEventListener('click', function () { select(n.id); });
        wrap.appendChild(row);
      });
      box.appendChild(wrap);
    });

    applyVisualFilter();
  }

  function worst(list) {
    var m = 9;
    list.forEach(function (n) { m = Math.min(m, SEVERITY[statusOf(n)] == null ? 9 : SEVERITY[statusOf(n)]); });
    return m;
  }

  // ── filtering ───────────────────────────────────────────────────────

  function matches(n) {
    var st = statusOf(n);
    if (!state.filters[st]) return false;
    if (!state.query) return true;
    var q = state.query.toLowerCase();
    if (String(n.path).toLowerCase().indexOf(q) >= 0) return true;
    if (String(n.name).toLowerCase().indexOf(q) >= 0) return true;
    var syms = state.symbolsByFile[n.path] || [];
    for (var i = 0; i < syms.length; i++) {
      if (String(syms[i].name).toLowerCase().indexOf(q) >= 0) return true;
    }
    return false;
  }

  function applyVisualFilter() {
    var visible = {};
    var nodes = (state.payload && state.payload.nodes) || [];
    nodes.forEach(function (n) { if (matches(n)) visible[n.id] = true; });

    var filtering = !!state.query ||
      Object.keys(state.filters).some(function (k) { return !state.filters[k]; });

    Array.prototype.forEach.call(document.querySelectorAll('#nodes .node'), function (g) {
      var on = !filtering || !!visible[g.dataset.id];
      g.classList.toggle('dimmed', !on);
      if (g.dataset.id === state.selected) g.classList.add('selected');
    });

    Array.prototype.forEach.call(document.querySelectorAll('#edges .edge'), function (p) {
      var on = !filtering || (!!visible[p.dataset.from] && !!visible[p.dataset.to]);
      var from = p.dataset.from, to = p.dataset.to;
      var isOut = !!state.selected && from === state.selected;
      var isIn = !!state.selected && to === state.selected;
      var lit = isOut || isIn;
      // Electrical-wiring trace: out = green, in = yellow, and any wire that
      // touches a broken/affected file goes red — danger outranks direction.
      var bad = lit &&
        (statusOf(state.byId[to]) === 'broken' || statusOf(state.byId[from]) === 'affected');
      // While a node is selected everything it is NOT wired to fades back,
      // so its own bundle of wires is the only bright thing on the canvas.
      p.classList.toggle('dimmed', !on || (!!state.selected && !lit));
      p.classList.toggle('hl', lit);
      p.classList.toggle('wire-out', isOut && !bad);
      p.classList.toggle('wire-in', isIn && !bad);
      p.classList.toggle('wire-bad', bad);
      p.setAttribute('marker-end', 'url(#arrow-' +
        (bad ? 'bad' : isOut ? 'out' : isIn ? 'in' : p.dataset.marker) + ')');
    });

    // View-aware: applyVisualFilter also runs on search input, filter chips
    // and rescan pushes. Without the view check any of those re-showed the
    // wiring legend on top of the dead-code list after switchView hid it.
    if (el.wireLegend) {
      el.wireLegend.hidden = !state.selected || state.view !== 'graph';
    }

    Array.prototype.forEach.call(document.querySelectorAll('.tree-file'), function (row) {
      row.classList.toggle('dimmed', filtering && !visible[row.dataset.id]);
      row.classList.toggle('selected', row.dataset.id === state.selected);
    });
  }

  // ── selection + detail ──────────────────────────────────────────────

  function select(id) {
    state.selected = id;
    applyVisualFilter();
    renderDetail();
    if (el.btnFocus) el.btnFocus.disabled = !id;
  }

  function renderDetail() {
    var box = el.detail;
    if (!box) return;

    var n = state.byId[state.selected];
    if (!n) {
      box.innerHTML = '<div class="notes"><p>Pick a file in the graph or the list to see its ' +
        'problems, symbols and import relationships.</p></div>';
      return;
    }

    var st = statusOf(n);
    var h = '';

    h += '<div class="detail">';
    h += '<div class="title">' + esc(n.name) + '</div>';
    h += '<span class="badge ' + esc(st) + '">' + esc(st) + '</span>';
    h += '<div style="margin-top:9px"></div>';
    h += kv('Path', n.path);
    h += kv('Language', n.lang || 'unknown');
    h += kv('Deep analysis', n.deep ? 'yes' : 'no');
    h += kv('Symbols', n.symbols || 0);
    h += kv('Size', fmtBytes(n.size));
    if (n.new) h += kv('Since last scan', 'new file');
    h += '<div style="margin-top:9px"></div>';
    h += '<button class="btn" id="btnOpen" style="width:100%">Open in editor</button>';
    h += '</div>';

    var problems = state.problemsByFile[n.path] || [];
    if (problems.length) {
      h += '<div class="detail"><h3 style="margin:0 0 8px">Problems (' + problems.length + ')</h3>';
      problems.slice(0, 12).forEach(function (pr) {
        var cls = pr.status === 'warning' ? 'problem warning' : 'problem';
        h += '<div class="' + cls + '" data-line="' + (pr.line || 0) + '">';
        h += '<div class="msg">' + esc(pr.message) + '</div>';
        h += '<div class="loc">' + esc(pr.file) + (pr.line ? ':' + pr.line : '') + '</div>';
        if (pr.detail) h += '<div class="detail-txt">' + esc(pr.detail) + '</div>';
        h += '</div>';
      });
      if (problems.length > 12) h += '<div class="more">+ ' + (problems.length - 12) + ' more</div>';
      h += '</div>';
    }

    var importers = (state.inEdges[n.id] || []).map(function (i) { return state.byId[i]; }).filter(Boolean);
    var imports = (state.outEdges[n.id] || []).map(function (i) { return state.byId[i]; }).filter(Boolean);

    if (importers.length) {
      h += '<div class="detail"><h3 style="margin:0 0 6px">Imported by (' + importers.length + ')</h3>';
      importers.slice(0, 25).forEach(function (m) { h += linkRow(m, '→', 'in'); });
      if (importers.length > 25) h += '<div class="more">+ ' + (importers.length - 25) + ' more</div>';
      h += '</div>';
    }

    if (imports.length) {
      h += '<div class="detail"><h3 style="margin:0 0 6px">Imports (' + imports.length + ')</h3>';
      imports.slice(0, 25).forEach(function (m) { h += linkRow(m, '←', 'out'); });
      if (imports.length > 25) h += '<div class="more">+ ' + (imports.length - 25) + ' more</div>';
      h += '</div>';
    }

    var syms = state.symbolsByFile[n.path] || [];
    if (syms.length) {
      h += '<div class="detail"><h3 style="margin:0 0 6px">Symbols (' + syms.length + ')</h3>';
      syms.slice(0, 40).forEach(function (s) {
        h += '<div class="sym" data-line="' + (s.line || 0) + '">' +
          '<span class="kind">' + esc(s.kind || 'def') + '</span>' +
          '<span class="nm">' + esc(s.name) + '</span>' +
          '<span class="ln">' + (s.line || '') + '</span></div>';
      });
      if (syms.length > 40) h += '<div class="more">+ ' + (syms.length - 40) + ' more</div>';
      h += '</div>';
    }

    box.innerHTML = h;

    var open = $('btnOpen');
    if (open) open.addEventListener('click', function () { openFile(n.path, 0); });

    Array.prototype.forEach.call(box.querySelectorAll('.problem, .sym'), function (row) {
      row.style.cursor = 'pointer';
      row.addEventListener('click', function () {
        openFile(n.path, parseInt(row.dataset.line, 10) || 0);
      });
    });

    Array.prototype.forEach.call(box.querySelectorAll('.link-row'), function (row) {
      row.addEventListener('click', function () { select(row.dataset.id); });
    });
  }

  function kv(label, value) {
    return '<div class="kv"><span>' + esc(label) + '</span><strong>' + esc(value) + '</strong></div>';
  }

  function linkRow(m, arrow, wire) {
    return '<div class="link-row' + (wire ? ' w-' + wire : '') + '" data-id="' + esc(m.id) + '" title="' + esc(m.path) + '">' +
      '<i class="i-' + esc(statusOf(m)) + '"></i>' +
      '<span class="nm">' + esc(m.name) + '</span>' +
      '<span class="arrow">' + arrow + '</span></div>';
  }

  function openFile(path, line) {
    if (!path) return;
    if (bridge && typeof bridge.openFile === 'function') {
      try { bridge.openFile(String(path), Number(line) || 0); return; } catch (e) { /* fall through */ }
    }
    showToast('warn', 'Editor bridge unavailable; cannot open ' + path);
  }

  // ── dead-code view ─────────────────────────────────────────────────────

  function switchView(view) {
    state.view = view;
    var graph = view === 'graph';
    // Hide the whole shell (not just #map) so the dead-code list is the only
    // item occupying the centre grid cell while it is showing.
    if (el.mapWrap) el.mapWrap.hidden = !graph;
    if (el.map) el.map.hidden = !graph;
    if (el.deadPanel) el.deadPanel.hidden = graph;
    if (el.tabGraph) el.tabGraph.setAttribute('aria-selected', graph ? 'true' : 'false');
    if (el.tabDead) el.tabDead.setAttribute('aria-selected', graph ? 'false' : 'true');
    // Fit/Focus only act on the graph canvas.
    if (el.btnFit) el.btnFit.style.visibility = graph ? '' : 'hidden';
    if (el.btnFocus) el.btnFocus.style.visibility = graph ? '' : 'hidden';
    // The wiring legend belongs to the canvas; never float it over the
    // dead-code list. Re-entering the graph restores it if a node is selected.
    if (el.wireLegend) el.wireLegend.hidden = !graph || !state.selected;
  }

  function deadMatches(text) {
    if (!state.query) return true;
    return text.toLowerCase().indexOf(state.query.toLowerCase()) !== -1;
  }

  /* Rows carry their target in data-* and rely on ONE delegated listener on
     #deadGroups (bound in bind()). 700+ rows used to each own two closures;
     delegation keeps every re-render allocation-free of listeners. */
  function deadRow(label, sub, file, line, kindClass) {
    var row = document.createElement('div');
    row.className = 'dead-row' + (kindClass ? ' ' + kindClass : '');
    row.setAttribute('role', 'button');
    row.tabIndex = 0;
    row.title = 'Open ' + file + (line ? ' at line ' + line : '');
    row.dataset.file = file;
    row.dataset.line = String(line || 0);
    row.innerHTML =
      '<span class="dead-name">' + esc(label) + '</span>' +
      '<span class="dead-loc">' + esc(sub) + '</span>';
    return row;
  }

  function deadRowTarget(node) {
    return node && node.closest ? node.closest('.dead-row') : null;
  }

  function openDeadRow(row) {
    if (row) openFile(row.dataset.file, Number(row.dataset.line) || 0);
  }

  function deadSection(title, hint, rowsBuilder) {
    var sec = document.createElement('section');
    sec.className = 'dead-group';
    var head = document.createElement('div');
    head.className = 'dead-head';
    var rows = rowsBuilder();
    head.innerHTML = '<h3>' + esc(title) + ' <span class="dead-count">' + rows.length + '</span></h3>' +
      (hint ? '<p>' + esc(hint) + '</p>' : '');
    sec.appendChild(head);
    if (!rows.length) {
      var none = document.createElement('div');
      none.className = 'more';
      none.textContent = state.query ? 'Nothing matches this search.' : 'None found. ';
      sec.appendChild(none);
    } else {
      rows.forEach(function (r) { sec.appendChild(r); });
    }
    return sec;
  }

  function renderDeadCode() {
    if (!el.deadGroups) return;
    var dc = (state.payload && state.payload.dead_code) || {};
    var counts = dc.counts || {};
    var orphans = Array.isArray(dc.orphan_files) ? dc.orphan_files : [];
    var unused = Array.isArray(dc.unused_symbols) ? dc.unused_symbols : [];

    // Badge on the tab: total findings, using the uncapped counts.
    var total = (counts.unused_symbols || unused.length) + (counts.orphan_files || orphans.length);
    if (el.deadBadge) {
      el.deadBadge.textContent = total > 999 ? '999+' : String(total);
      el.deadBadge.hidden = total === 0;
    }

    var intro = [];
    intro.push('Heuristic. The usage census reads EVERY text file in the project (Python, JS, HTML, CSS, JSON, spec files), so names mentioned anywhere - including inside importlib strings and PyInstaller hooks - count as used. Test symbols (test_*/Test*/pytestmark), framework overrides (Qt events, http.server, unittest) and dispatch-by-name methods (ast.NodeVisitor visit_*, HTMLParser handle_*) are exempt. A symbol is listed only when its name appears in no other file beyond its own definition lines. Review before deleting.');
    if (counts.unused_capped) intro.push('Unused-symbol list capped at ' + unused.length + ' for performance.');
    if (counts.orphans_capped) intro.push('Orphan-file list capped at ' + orphans.length + '.');
    if (el.deadIntro) el.deadIntro.textContent = intro.join(' ');

    el.deadGroups.innerHTML = '';

    el.deadGroups.appendChild(deadSection(
      'Orphan files',
      'Python modules no other file imports. Entry points (main/test/conftest, __main__ guards) are excluded.',
      function () {
        return orphans
          .filter(function (o) { return deadMatches(o.file + ' ' + (o.reason || '')); })
          .map(function (o) {
            return deadRow(o.file.split('/').pop(), o.file, o.file, 0, 'orphan');
          });
      }
    ));

    function symRows(want) {
      return unused
        .filter(function (u) { return want(u.kind); })
        .filter(function (u) { return deadMatches(u.file + ' ' + u.qualname); })
        .map(function (u) {
          return deadRow(u.qualname, u.file + ':' + (u.line || 0), u.file, u.line || 0, 'sym-' + u.kind);
        });
    }

    el.deadGroups.appendChild(deadSection(
      'Unused functions, classes & methods',
      'Defined but never referenced anywhere in the project by name.',
      function () { return symRows(function (k) { return k !== 'variable'; }); }
    ));

    el.deadGroups.appendChild(deadSection(
      'Unused module-level variables',
      'Top-level assignments whose name is never mentioned again in any Python file.',
      function () { return symRows(function (k) { return k === 'variable'; }); }
    ));
  }

  // ── whole-panel render ─────────────────────────────────────────────────

  function render(p) {
    state.payload = p || {};
    state.scanning = false;
    indexPayload(state.payload);

    var s = state.payload.summary || {};
    if (el.projName) {
      var name = state.payload.project_name || 'no project';
      el.projName.textContent = name;
      el.projName.title = state.payload.project_root || name;
    }

    setText(el.stBroken, s.broken);
    setText(el.stWarning, s.warning);
    setText(el.stAffected, s.affected);
    setText(el.stNew, s.new);
    setText(el.stHealthy, s.healthy);
    setText(el.stFiles, s.files);
    setText(el.stUnused, s.unused_symbols);
    setText(el.stOrphans, s.orphan_files);
    renderMix(s);

    if (el.scanMeta) {
      var bits = [];
      if (state.payload.scan_ms != null) bits.push(Math.round(state.payload.scan_ms) + ' ms');
      if (state.payload.from_cache) bits.push('cached');
      else if (state.payload.reused_files) bits.push(state.payload.reused_files + ' reused');
      if (state.payload.generated_at) bits.push(fmtClock(state.payload.generated_at));
      el.scanMeta.textContent = bits.join(' · ');
    }

    var notes = Array.isArray(state.payload.notes) ? state.payload.notes : [];
    if (el.notes) {
      el.notes.innerHTML = notes.length
        ? notes.map(function (n) { return '<li>' + esc(n) + '</li>'; }).join('')
        : '<li>No notes.</li>';
    }

    var nodes = Array.isArray(state.payload.nodes) ? state.payload.nodes : [];
    if (state.payload.empty || !nodes.length) {
      showState('empty', notes.filter(function (n) {
        return /no recognised source files/i.test(n);
      })[0] || 'This project has no recognised source files. Open a folder containing code and the health map will build itself.');
      if (el.tree) el.tree.innerHTML = '<div class="more">No files.</div>';
      if (el.graphHint) el.graphHint.textContent = '';
      renderDeadCode();
      return;
    }

    showState(null);
    layout(nodes);
    renderGraph();
    renderTree();
    renderDetail();
    renderDeadCode();
  }

  function setText(node, v) { if (node) node.textContent = String(v == null ? 0 : v); }

  // Status distribution bar: one proportional segment per status, healthy
  // included - an all-green bar IS the "everything is fine" chart. Zero-count
  // statuses get no segment, so the bar never lies about a mix.
  function renderMix(s) {
    if (!el.mixBar) return;
    var order = ['broken', 'warning', 'affected', 'new', 'healthy'];
    var total = order.reduce(function (n, k) { return n + (Number(s[k]) || 0); }, 0);
    el.mixBar.innerHTML = '';
    if (el.mixLegend) el.mixLegend.innerHTML = '';
    var empty = !total;
    el.mixBar.hidden = empty;
    if (el.mixLegend) el.mixLegend.hidden = empty;
    if (empty) return;
    order.forEach(function (k) {
      var n = Number(s[k]) || 0;
      if (!n) return;
      var pct = Math.round((n / total) * 100);
      var seg = document.createElement('i');
      seg.className = k;
      seg.style.flex = String(n);
      seg.title = k + ': ' + n + ' file' + (n === 1 ? '' : 's') + ' (' + pct + '%)';
      el.mixBar.appendChild(seg);
      if (el.mixLegend) {
        var li = document.createElement('span');
        li.className = k;
        li.title = seg.title;
        li.innerHTML = '<i></i>' + k + ' <b>' + n + '</b>';
        el.mixLegend.appendChild(li);
      }
    });
  }

  // ── zoom / scroll ───────────────────────────────────────────────────

  function applyScale() {
    var vp = document.getElementById('viewport');
    if (vp) vp.setAttribute('transform', 'scale(' + state.scale + ')');
    var g = el.graph;
    if (g && g.viewBox && g.viewBox.baseVal && g.viewBox.baseVal.width) {
      var vb = g.viewBox.baseVal;
      g.setAttribute('width', Math.ceil(vb.width * state.scale));
      g.setAttribute('height', Math.ceil(vb.height * state.scale));
    }
  }

  function fit() {
    state.scale = 1;
    applyScale();
    if (el.map) el.map.scrollTo({ top: 0, left: 0, behavior: 'smooth' });
  }

  function focusSelection() {
    if (!state.selected) return;
    var L = null;
    for (var i = 0; i < state.laid.length; i++) {
      if (state.laid[i].node.id === state.selected) { L = state.laid[i]; break; }
    }
    if (!L || !el.map) {
      // Selected file was omitted from the capped graph; show it in the tree.
      var row = document.querySelector('.tree-file[data-id="' + CSS.escape(state.selected) + '"]');
      if (row) row.scrollIntoView({ block: 'center', behavior: 'smooth' });
      return;
    }
    var x = (L.x + NODE_W / 2) * state.scale;
    var y = (L.y + NODE_H / 2) * state.scale;
    el.map.scrollTo({
      left: Math.max(0, x - el.map.clientWidth / 2),
      top: Math.max(0, y - el.map.clientHeight / 2),
      behavior: 'smooth'
    });
  }

  // ── bridge ──────────────────────────────────────────────────────────

  function rescan(force) {
    if (!bridge || typeof bridge.rescan !== 'function') {
      showToast('warn', 'Scanner bridge unavailable.');
      return;
    }
    setLive('Scanning', true);
    if (el.btnRescan) el.btnRescan.disabled = true;
    logEvent('SCAN', force ? 'full rescan requested' : 'incremental rescan requested', 'info');
    try {
      bridge.rescan(!!force, function (raw) {
        var p = parse(raw);
        // The Python side scans on a worker thread and answers the slot with
        // {pending:true}; the real payload arrives over dataChanged. Keep the
        // scanning indicator up and let that push finish the job.
        if (p && p.pending) {
          state.scanning = true;
          logEvent('SCAN', 'running in background', 'info');
          return;
        }
        if (el.btnRescan) el.btnRescan.disabled = false;
        setLive('Idle', false);
        if (!p) { showToast('error', 'Rescan returned no data.'); return; }
        render(p);
        var sm = p.summary || {};
        logEvent('DONE', (sm.files || 0) + ' files · ' + (sm.broken || 0) + ' broken · ' +
          (sm.warning || 0) + ' warning · ' + Math.round(p.scan_ms || 0) + ' ms',
          (sm.broken || 0) ? 'error' : 'ok');
        showToast((sm.broken || 0) ? 'error' : 'ok',
          (sm.broken || 0)
            ? sm.broken + ' broken file' + (sm.broken === 1 ? '' : 's') + ', ' + (sm.affected || 0) + ' affected'
            : 'No broken files.');
      });
    } catch (e) {
      if (el.btnRescan) el.btnRescan.disabled = false;
      setLive('Idle', false);
      logEvent('ERROR', String(e && e.message || e), 'error');
      showToast('error', 'Rescan failed.');
    }
  }

  function parse(raw) {
    if (!raw) return null;
    if (typeof raw === 'object') return raw;
    try { return JSON.parse(raw); } catch (e) {
      logEvent('ERROR', 'Unparseable payload from Python: ' + e.message, 'error');
      return null;
    }
  }

  function connectChannel() {
    if (typeof QWebChannel === 'undefined' || !qt || !qt.webChannelTransport) {
      logEvent('BRIDGE', 'QWebChannel transport missing; waiting for a state push', 'warn');
      return;
    }
    new QWebChannel(qt.webChannelTransport, function (channel) {
      bridge = channel.objects.healthBridge || channel.objects.bridge || null;
      window.__healthDebug.bridge = !!bridge;
      if (!bridge) {
        logEvent('BRIDGE', 'No health bridge object registered', 'error');
        return;
      }
      logEvent('BRIDGE', 'connected', 'ok');

      if (bridge.dataChanged && bridge.dataChanged.connect) {
        bridge.dataChanged.connect(function (raw) {
          var p = parse(raw);
          if (!p || p.pending) return;
          render(p);
          // This push is how a background scan reports completion, so it owns
          // clearing the scanning indicator and re-enabling the button.
          setLive('Idle', false);
          if (el.btnRescan) el.btnRescan.disabled = false;
          var sm = p.summary || {};
          logEvent('DONE', (sm.files || 0) + ' files · ' + (sm.broken || 0) + ' broken · ' +
            (sm.affected || 0) + ' affected · ' + Math.round(p.scan_ms || 0) + ' ms',
            (sm.broken || 0) ? 'error' : 'ok');
        });
      }
      if (bridge.toastRequested && bridge.toastRequested.connect) {
        bridge.toastRequested.connect(showToast);
      }
      if (bridge.scanStarted && bridge.scanStarted.connect) {
        bridge.scanStarted.connect(function () {
          state.scanning = true;
          setLive('Scanning', true);
          if (el.btnRescan) el.btnRescan.disabled = true;
        });
      }

      // Pull the initial payload ourselves rather than relying only on the
      // Python-side timed push: if the push lands first, receiveHealthState
      // has already rendered and this is a harmless re-render of the same data.
      if (typeof bridge.loadInitialData === 'function') {
        try {
          bridge.loadInitialData(function (raw) {
            var p = parse(raw);
            if (!p) {
              showState('error', 'The scanner returned no data for this project.');
              setLive('No data', false);
              return;
            }
            if (p.pending) {
              // No in-session payload to paint; a background scan is running
              // and will arrive over dataChanged. Stay on the loading state.
              state.scanning = true;
              logEvent('SCAN', 'building map in background', 'info');
              setLive('Scanning', true);
              return;
            }
            render(p);
            setLive('Idle', false);
            var sm = p.summary || {};
            logEvent('SCAN', (sm.files || 0) + ' files · ' + (sm.edges || 0) + ' edges · ' +
              Math.round(p.scan_ms || 0) + ' ms', (sm.broken || 0) ? 'error' : 'ok');
            if (sm.broken) {
              logEvent('BREAK', sm.broken + ' broken, ' + (sm.affected || 0) + ' affected downstream', 'error');
            }
          });
        } catch (e) {
          logEvent('ERROR', 'loadInitialData failed: ' + (e && e.message || e), 'error');
        }
      }
    });
  }

  /* Entry point called by Python: window.receiveHealthState(payloadJson).
     Defined on window before this IIFE runs (see the inline stub in the
     HTML), so a push that arrives during script load is stashed and picked
     up here rather than lost. */
  window.receiveHealthState = function (raw) {
    var p = parse(raw);
    if (!p) {
      window.__healthDebug.pendingState = raw;
      return;
    }
    if (p.pending) { state.scanning = true; setLive('Scanning', true); return; }
    render(p);
    // A non-pending payload only ever arrives when a scan has finished (the
    // dataChanged push) or on the initial load push, so this path - which is
    // proven to run, unlike the bridge.dataChanged JS subscription below -
    // owns clearing the scanning UI. When the subscription was the only place
    // that re-enabled the button and it never fired (it misses signals
    // emitted from the scan worker thread on some Qt builds), the Rescan
    // button stayed disabled forever and only an IDE restart brought it back.
    state.scanning = false;
    if (el.btnRescan) el.btnRescan.disabled = false;
    setLive('Idle', false);
    var sm = p.summary || {};
    logEvent('PUSH', (sm.files || 0) + ' files · ' + (sm.broken || 0) + ' broken · ' +
      (sm.affected || 0) + ' affected', (sm.broken || 0) ? 'error' : 'ok');
  };

  window.showToast = showToast;

  // ── boot ────────────────────────────────────────────────────────────

  function bind() {
    ['projName', 'scanMeta', 'liveBadge', 'liveText', 'search', 'filters', 'tree',
      'graph', 'map', 'events', 'detail', 'notes', 'toast', 'graphHint',
      'stateLoading', 'stateEmpty', 'stateError', 'emptyText', 'errorText',
      'stBroken', 'stWarning', 'stAffected', 'stNew', 'stHealthy', 'stFiles',
      'stUnused', 'stOrphans', 'mixBar', 'mixLegend',
      'btnRescan', 'btnFit', 'btnFocus', 'btnExport',
      'tabGraph', 'tabDead', 'deadPanel', 'deadGroups', 'deadBadge', 'deadIntro',
      'mapWrap', 'wireLegend'].forEach(function (id) { el[id] = $(id); });

    if (el.btnRescan) el.btnRescan.addEventListener('click', function () { rescan(true); });
    if (el.btnExport) el.btnExport.addEventListener('click', function () {
      // Python owns the save dialog and the file write; it toasts the result.
      if (bridge && typeof bridge.exportReport === 'function') bridge.exportReport();
    });
    if (el.btnFit) el.btnFit.addEventListener('click', fit);
    if (el.btnFocus) el.btnFocus.addEventListener('click', focusSelection);
    if (el.tabGraph) el.tabGraph.addEventListener('click', function () { switchView('graph'); });
    if (el.tabDead) el.tabDead.addEventListener('click', function () { switchView('dead'); });

    // Single delegated pair for the whole dead-code list (see deadRow).
    if (el.deadGroups) {
      el.deadGroups.addEventListener('click', function (ev) {
        openDeadRow(deadRowTarget(ev.target));
      });
      el.deadGroups.addEventListener('keydown', function (ev) {
        if (ev.key !== 'Enter' && ev.key !== ' ') return;
        var row = deadRowTarget(ev.target);
        if (row) { ev.preventDefault(); openDeadRow(row); }
      });
    }

    if (el.search) {
      // The dead lists rebuild hundreds of rows; coalesce keystrokes so a
      // fast typist triggers one render per pause, not one per key.
      var deadTimer = 0;
      el.search.addEventListener('input', function () {
        state.query = el.search.value.trim();
        applyVisualFilter();
        clearTimeout(deadTimer);
        deadTimer = setTimeout(renderDeadCode, 120);
      });
      el.search.addEventListener('keydown', function (ev) {
        if (ev.key === 'Escape') { el.search.value = ''; state.query = ''; applyVisualFilter(); renderDeadCode(); }
      });
    }

    if (el.filters) {
      el.filters.addEventListener('click', function (ev) {
        var chip = ev.target.closest ? ev.target.closest('.chip') : null;
        if (!chip || !chip.dataset.status) return;
        var on = chip.getAttribute('aria-pressed') !== 'true';
        chip.setAttribute('aria-pressed', on ? 'true' : 'false');
        state.filters[chip.dataset.status] = on;
        applyVisualFilter();
      });
    }

    if (el.map) {
      el.map.addEventListener('click', function (ev) {
        // Clicking empty canvas clears the selection; clicking a node does not
        // bubble here because the node handler runs first and we check target.
        if (ev.target === el.map || ev.target === el.graph) { select(null); }
      });
      el.map.addEventListener('wheel', function (ev) {
        if (!ev.ctrlKey) return;          // plain wheel stays a scroll
        ev.preventDefault();
        var next = state.scale * (ev.deltaY < 0 ? 1.12 : 0.89);
        state.scale = Math.min(2.4, Math.max(0.35, next));
        applyScale();
      }, { passive: false });
    }

    document.addEventListener('keydown', function (ev) {
      if (ev.key === 'Escape') select(null);
      if ((ev.ctrlKey || ev.metaKey) && ev.key === 'f') {
        ev.preventDefault();
        if (el.search) el.search.focus();
      }
    });
  }

  function boot() {
    bind();
    window.__healthDebug.loaded = true;
    showState('loading');
    setLive('Connecting', true);
    logEvent('BOOT', 'health map renderer ready', 'info');
    connectChannel();

    // A push that beat the script to the page.
    var pending = window.__healthDebug.pendingState;
    if (pending) {
      window.__healthDebug.pendingState = null;
      window.receiveHealthState(pending);
    }

    // Nothing arrived (no bridge, no push): stop showing an infinite spinner.
    // A scan that is genuinely still running re-arms the check instead of
    // being declared dead - a large repo can take well over 12s to parse, and
    // telling the user it failed while the worker thread is mid-scan is a lie
    // they would then act on by closing the panel.
    (function watchdog() {
      setTimeout(function () {
        if (state.payload) return;
        if (state.scanning) { watchdog(); return; }
        showState('error', 'No health data reached this panel. Close and reopen it, or check the log for a scanner error.');
        setLive('No data', false);
      }, 12000);
    })();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
