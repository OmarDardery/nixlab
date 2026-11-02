(() => {
  'use strict';
  const announcer = document.getElementById('announcer');
  document.querySelectorAll('[data-copy]').forEach(button => {
    button.addEventListener('click', async () => {
      const code = document.getElementById(button.dataset.copy);
      const initial = button.textContent;
      try {
        await navigator.clipboard.writeText(code.textContent.trim());
        button.textContent = 'Copied ✓';
        if (announcer) announcer.textContent = 'Command copied to clipboard.';
      } catch {
        const range = document.createRange();
        range.selectNodeContents(code);
        const selection = window.getSelection();
        selection.removeAllRanges(); selection.addRange(range);
        button.textContent = 'Selected — copy manually';
        if (announcer) announcer.textContent = 'Clipboard unavailable. Command selected for manual copying.';
      }
      window.setTimeout(() => { button.textContent = initial; }, 2500);
    });
  });
  const input = document.getElementById('docs-search');
  const results = document.getElementById('search-results');
  if (!input || !results) return;
  let index;
  let loading;
  async function search() {
    const query = input.value.trim().toLocaleLowerCase();
    results.replaceChildren(); results.hidden = !query;
    if (!query) return;
    try {
      loading ??= fetch(input.dataset.index).then(response => {
        if (!response.ok) throw new Error('Search index unavailable');
        return response.json();
      });
      index ??= await loading;
      if (input.value.trim().toLocaleLowerCase() !== query) return;
      const terms = query.split(/\s+/);
      const matches = index.map(item => {
        const title = item.title.toLocaleLowerCase();
        const text = item.text.toLocaleLowerCase();
        if (!terms.every(term => (title + ' ' + text).includes(term))) return null;
        const score = terms.reduce((sum, term) => sum + (title.includes(term) ? 20 : 0) + Math.min(text.split(term).length - 1, 8), 0)
          + (item.url.includes('/troubleshooting/') ? 5 : 0);
        return { ...item, score };
      }).filter(Boolean).sort((a, b) => b.score - a.score).slice(0, 8);
      for (const item of matches) {
        const link = document.createElement('a'); link.href = item.url; link.textContent = item.title; results.append(link);
      }
      if (!matches.length) results.textContent = 'No matches. Try “reboot”, “password” or “DHCP”.';
      if (announcer) announcer.textContent = `${matches.length} documentation results.`;
    } catch {
      loading = undefined;
      results.textContent = 'Search is unavailable. The documentation links below still work.';
    }
  }
  input.addEventListener('input', search);
  input.addEventListener('keydown', event => {
    if (event.key === 'Escape') { input.value = ''; results.hidden = true; input.blur(); }
    if (event.key === 'ArrowDown') { const first = results.querySelector('a'); if (first) { event.preventDefault(); first.focus(); } }
  });
  document.addEventListener('keydown', event => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLocaleLowerCase() === 'k') { event.preventDefault(); input.focus(); }
  });
})();
