/* ===== Upbit Watch docs — interactions ===== */
(function () {
  'use strict';

  /* ---- Copy-to-clipboard on code blocks ---- */
  document.querySelectorAll('.code').forEach(function (block) {
    var btn = block.querySelector('.copy-btn');
    var pre = block.querySelector('pre');
    if (!btn || !pre) return;
    btn.addEventListener('click', function () {
      var text = pre.innerText;
      var done = function () {
        var orig = 'Copy';
        btn.textContent = 'Copied ✓';
        btn.classList.add('copied');
        setTimeout(function () { btn.textContent = orig; btn.classList.remove('copied'); }, 1600);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done).catch(fallback);
      } else { fallback(); }
      function fallback() {
        var ta = document.createElement('textarea');
        ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
        document.body.appendChild(ta); ta.select();
        try { document.execCommand('copy'); done(); } catch (e) {}
        document.body.removeChild(ta);
      }
    });
  });

  /* ---- Scrollspy: highlight active nav link ---- */
  var navLinks = Array.prototype.slice.call(document.querySelectorAll('.nav a'));
  var sections = navLinks
    .map(function (a) { return document.querySelector(a.getAttribute('href')); })
    .filter(Boolean);

  function linkFor(id) {
    return navLinks.find(function (a) { return a.getAttribute('href') === '#' + id; });
  }

  if ('IntersectionObserver' in window && sections.length) {
    var visible = {};
    var obs = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) { visible[e.target.id] = e.isIntersecting ? e.intersectionRatio : 0; });
      // pick the section with the greatest visibility
      var bestId = null, best = 0;
      Object.keys(visible).forEach(function (id) {
        if (visible[id] > best) { best = visible[id]; bestId = id; }
      });
      if (bestId) {
        navLinks.forEach(function (a) { a.classList.remove('active'); });
        var link = linkFor(bestId);
        if (link) link.classList.add('active');
      }
    }, { rootMargin: '-80px 0px -55% 0px', threshold: [0, 0.25, 0.5, 1] });
    sections.forEach(function (s) { obs.observe(s); });
  }

  /* close mobile nav after clicking a link + smooth scroll handled by CSS */
  navLinks.forEach(function (a) {
    a.addEventListener('click', function () { document.body.classList.remove('nav-open'); });
  });

  /* ---- Mobile nav toggle ---- */
  var hamburger = document.getElementById('hamburger');
  var backdrop = document.getElementById('backdrop');
  if (hamburger) hamburger.addEventListener('click', function () { document.body.classList.toggle('nav-open'); });
  if (backdrop) backdrop.addEventListener('click', function () { document.body.classList.remove('nav-open'); });

  /* ---- Section filter (client-side search) ---- */
  var search = document.getElementById('search');
  var docSections = Array.prototype.slice.call(document.querySelectorAll('section.doc'));
  var dividers = Array.prototype.slice.call(document.querySelectorAll('.section-divider'));
  var noResults = document.getElementById('noResults');

  function haystack(sec) {
    return ((sec.getAttribute('data-title') || '') + ' ' + sec.textContent).toLowerCase();
  }

  if (search) {
    search.addEventListener('input', function () {
      var q = search.value.trim().toLowerCase();
      var searching = q.length > 0;
      document.body.classList.toggle('searching', searching);

      var anyVisible = false;
      docSections.forEach(function (sec) {
        var match = !searching || haystack(sec).indexOf(q) !== -1;
        sec.style.display = match ? '' : 'none';
        if (match) anyVisible = true;
        // dim non-matching nav entries
        var link = linkFor(sec.id);
        if (link) link.style.display = match ? '' : 'none';
      });

      // hide dividers while searching to avoid stray lines
      dividers.forEach(function (d) { d.style.display = searching ? 'none' : ''; });

      if (noResults) noResults.style.display = (searching && !anyVisible) ? 'block' : 'none';
    });

    // Escape clears the filter
    search.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') { search.value = ''; search.dispatchEvent(new Event('input')); search.blur(); }
    });
  }

  /* ---- "/" focuses the search box ---- */
  document.addEventListener('keydown', function (e) {
    if (e.key === '/' && document.activeElement !== search &&
        !/^(INPUT|TEXTAREA)$/.test(document.activeElement.tagName)) {
      e.preventDefault();
      if (search) search.focus();
    }
  });
})();
