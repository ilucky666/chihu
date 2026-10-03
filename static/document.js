(() => {
  const form = document.querySelector('form[data-autosave]');
  if (!form) return;
  const body = form.querySelector('textarea[name="body"]');
  const revision = form.querySelector('input[name="revision"]');
  const state = document.getElementById('save-state');
  const recover = document.getElementById('recover-draft');
  const key = `eatful-draft-${form.dataset.autosave}`;
  const oldDraft = localStorage.getItem(key);
  if (oldDraft && oldDraft !== body.value) {
    recover.hidden = false;
    recover.onclick = () => { body.value = oldDraft; recover.hidden = true; body.dispatchEvent(new Event('input')); };
  } else if (oldDraft === body.value) localStorage.removeItem(key);
  let timer;
  let busy = false;
  async function save() {
    if (busy) return;
    busy = true;
    const sent = body.value;
    const payload = new FormData(form);
    state.textContent = '正在保存…';
    try {
      const response = await fetch(form.action, { method: 'POST', body: payload, headers: { Accept: 'application/json' }, credentials: 'same-origin' });
      const result = await response.json();
      if (response.status === 409) {
        state.textContent = '版本冲突：草稿已保存在本机，请打开冲突比较并手动合并。';
        localStorage.setItem(key, body.value);
        return;
      }
      if (!response.ok) throw new Error('save failed');
      revision.value = result.revision;
      if (body.value === sent) { localStorage.removeItem(key); state.textContent = `已保存 · 版本 ${result.revision}`; }
      else { state.textContent = '有新改动，等待保存'; timer = setTimeout(save, 900); }
    } catch {
      localStorage.setItem(key, body.value);
      state.textContent = '未保存，草稿已保存在本机；联网后请重试';
    } finally { busy = false; }
  }
  body.addEventListener('input', () => {
    localStorage.setItem(key, body.value);
    state.textContent = '未保存';
    clearTimeout(timer);
    timer = setTimeout(save, 1200);
  });
  form.addEventListener('submit', () => localStorage.removeItem(key));
})();
