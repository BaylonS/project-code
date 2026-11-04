// Search the selected document's text index without changing the source page.
window.installReaderSearch = ({pages, show}) => {
  const $ = id => document.getElementById(id);
  const normalize = text => String(text).normalize('NFKC').toLowerCase().replace(/[^a-z0-9]/g, '');
  const indexed = pages.map(page => ({page, key: normalize(page.title + ' ' + page.text)}));
  let matches = [], selected = -1, limit = 20;
  function render() {
    $('searchresults').replaceChildren();
    matches.slice(0, limit).forEach((page, i) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = `Page ${page.page} · ${page.title || 'Reference page'}`;
      button.setAttribute('aria-current', String(i === selected));
      button.onclick = () => open(i);
      $('searchresults').append(button);
    });
    $('searchcount').textContent = !$('docsearch').value.trim()
      ? 'Search within this PDF by fault, ATA, reference, or words.'
      : matches.length ? `${matches.length} matching pages${selected >= 0 ? ` · result ${selected + 1} of ${matches.length}` : ''}`
      : 'No matching text. Try fewer words or another reference.';
    $('matchprevious').disabled = $('matchnext').disabled = matches.length === 0;
    $('searchmore').hidden = matches.length <= limit;
    $('clearsearch').hidden = !$('docsearch').value;
  }
  function open(i) {
    if (!matches.length) return;
    selected = (i + matches.length) % matches.length;
    limit = Math.max(limit, selected + 1);
    show(matches[selected].page);
    render();
  }
  function search() {
    const terms = $('docsearch').value.trim().split(/\s+/).map(normalize).filter(Boolean);
    matches = terms.length ? indexed.filter(row => terms.every(term => row.key.includes(term))).map(row => row.page) : [];
    selected = -1; limit = 20; render();
  }
  $('docsearch').addEventListener('input', search);
  $('searchform').onsubmit = event => {event.preventDefault(); open(selected + 1);};
  $('matchprevious').onclick = () => open(selected < 0 ? matches.length - 1 : selected - 1);
  $('matchnext').onclick = () => open(selected + 1);
  $('searchmore').onclick = () => {limit += 20; render();};
  $('clearsearch').onclick = () => {$('docsearch').value = ''; search(); $('docsearch').focus();};
  search();
};
