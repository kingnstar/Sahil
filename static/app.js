(() => {
  const token = document.querySelector('meta[name="csrf-token"]')?.content || '';
  async function send(url, data) {
    const response = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': token}, body: JSON.stringify(data)});
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.message || 'The change could not be saved.');
    return body;
  }
  document.querySelectorAll('.cell-input').forEach(input => {
    let timer;
    input.addEventListener('change', async () => {
      const row = input.closest('tr');
      const values = {};
      row.querySelectorAll('.cell-input').forEach(cell => values[cell.dataset.fieldId] = cell.value);
      const status = document.querySelector('.save-status');
      try {
        const result = await send(`/api/records/${row.dataset.rowId}`, {values, expected_version: Number(row.dataset.version)});
        row.dataset.version = result.version;
        if (status) {status.textContent = 'Saved'; status.className = 'save-status success-text';}
      } catch (error) {
        if (status) {status.textContent = error.message; status.className = 'save-status error-text';}
      }
    });
  });
  document.querySelectorAll('.delete-row').forEach(button => button.addEventListener('click', async () => {
    if (!confirm('Move this row to the recycle bin?')) return;
    try { await send(`/api/records/${button.dataset.rowId}/delete`, {}); button.closest('tr').remove(); }
    catch (error) { alert(error.message); }
  }));
  document.querySelector('#delete-selected')?.addEventListener('click', async () => {
    const ids = [...document.querySelectorAll('.row-select:checked')].map(box => Number(box.value));
    if (!ids.length) return;
    if (!confirm(`Move ${ids.length} selected row(s) to the recycle bin?`)) return;
    try {
      const sheetId = location.pathname.split('/').pop();
      await send(`/api/sheets/${sheetId}/rows/delete`, {record_ids: ids, confirmed: true});
      ids.forEach(id => document.querySelector(`#row-${id}`)?.remove());
    } catch (error) { alert(error.message); }
  });
  document.querySelectorAll('.move-column').forEach(button => button.addEventListener('click', async () => {
    try { await send(`/api/columns/${button.dataset.fieldId}/move`, {position: Number(button.dataset.position)}); location.reload(); }
    catch (error) { alert(error.message); }
  }));
  document.querySelector('#paste-rows')?.addEventListener('click', async () => {
    const panel = document.querySelector('#paste-panel');
    const text = document.querySelector('#paste-data').value;
    const fieldIds = JSON.parse(panel.dataset.fieldIds);
    const lines = text.split(/\r?\n/).filter(line => line.length > 0);
    if (lines.some(line => line.split('\t').length > fieldIds.length)) {
      alert('A pasted row contains more cells than this sheet has visible columns.');
      return;
    }
    const rows = lines.map(line => {
      const cells = line.split('\t');
      return Object.fromEntries(fieldIds.map((id, index) => [String(id), cells[index] ?? '']));
    });
    if (!rows.length) return;
    const confirmed = rows.length <= 1000 || confirm(`Add ${rows.length} rows?`);
    if (!confirmed) return;
    try {
      await send(`/api/sheets/${panel.dataset.sheetId}/paste`, {rows, confirmed: true});
      location.reload();
    } catch (error) { alert(error.message); }
  });
  document.querySelectorAll('.toggle-column').forEach(button => button.addEventListener('click', async () => {
    try { await send(`/api/columns/${button.dataset.fieldId}/visibility`, {hidden: button.dataset.hidden !== 'true'}); location.reload(); }
    catch (error) { alert(error.message); }
  }));
  document.querySelectorAll('.delete-column').forEach(button => button.addEventListener('click', async () => {
    if (!confirm('Delete this column and its values?')) return;
    try { await send(`/api/columns/${button.dataset.fieldId}/delete`, {}); location.reload(); }
    catch (error) { alert(error.message); }
  }));
  document.querySelectorAll('.restore-row').forEach(button => button.addEventListener('click', async () => {
    try { await send(`/api/recycle/${button.dataset.id}/restore`, {}); location.reload(); }
    catch (error) { alert(error.message); }
  }));
  document.querySelectorAll('.delete-forever').forEach(button => button.addEventListener('click', async () => {
    if (!confirm('Permanently delete this row? This cannot be undone.')) return;
    try { await send(`/api/recycle/${button.dataset.id}/delete`, {}); location.reload(); }
    catch (error) { alert(error.message); }
  }));
  document.querySelectorAll('form[data-confirm]').forEach(form => form.addEventListener('submit', event => {
    if (!confirm(form.dataset.confirm)) event.preventDefault();
  }));
})();
