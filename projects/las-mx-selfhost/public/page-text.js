window.renderPageText = (container, page) => {
  container.replaceChildren();
  container.className = 'formatted-page-text';
  if (!page?.blocks) {
    const pre=document.createElement('pre');pre.textContent=page?.text||'No extracted text available.';
    container.append(pre);return;
  }
  for (const block of page.blocks) {
    if (block.type === 'table') {
      const wrap=document.createElement('div');wrap.className='text-table-wrap';
      const table=document.createElement('table');table.className='source-text-table';
      const head=document.createElement('thead'),tr=document.createElement('tr');
      block.headers.forEach(label=>{const th=document.createElement('th');th.scope='col';th.textContent=label;tr.append(th)});
      head.append(tr);table.append(head);
      const body=document.createElement('tbody');
      block.rows.forEach(row=>{const tr=document.createElement('tr');row.forEach(value=>{const td=document.createElement('td');td.textContent=value||'—';tr.append(td)});body.append(tr)});
      table.append(body);wrap.append(table);container.append(wrap);
    } else {
      const el=document.createElement(block.type==='heading'?'h4':'p');
      el.textContent=block.text;
      if(block.partial)el.className='transcription-note';
      container.append(el);
    }
  }
};
