// 왈가 — 최소 바닐라 JS: 추천·신고·삭제(fetch) + 답글 대상 지정
const CSRF = document.querySelector('meta[name=csrf]').content;

async function post(url, data) {
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'X-CSRFToken': CSRF, 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams(data),
  });
  return r.json().catch(() => ({ ok: false }));
}

// 사이드 위젯 페이지 넘김 (디시 우측 위젯 방식)
document.querySelectorAll('.widget[data-pages]').forEach(w => {
  const pages = [...w.querySelectorAll('.wpage')];
  const num = w.querySelector('.wnum');
  let i = 0;
  const show = () => { pages.forEach((p, k) => { p.hidden = k !== i; }); num.textContent = (i + 1) + '/' + pages.length; };
  w.querySelector('.wprev').onclick = () => { i = (i - 1 + pages.length) % pages.length; show(); };
  w.querySelector('.wnext').onclick = () => { i = (i + 1) % pages.length; show(); };
});

document.addEventListener('click', async e => {
  const b = e.target.closest('[data-rec],[data-report],[data-del],[data-reply],[data-reply-cancel]');
  if (!b) return;
  const d = b.dataset;

  if (d.rec) {
    const j = await post('/rec', { target: d.rec, target_id: d.id });
    if (j.ok) { b.querySelector('b').textContent = j.rec_cnt; b.classList.add('on'); }
    else alert(j.msg || '실패');

  } else if (d.report) {
    const reason = prompt('신고 사유 (선택)');
    if (reason === null) return;
    const j = await post('/report', { target: d.report, target_id: d.id, reason });
    alert(j.ok ? '신고 접수됨' : (j.msg || '실패'));

  } else if (d.del) {
    let pw = '';
    if (d.anon === '1') { pw = prompt('비밀번호 4자리'); if (pw === null) return; }
    if (!confirm('삭제할까요?')) return;
    const j = await post(d.del, { pw });
    if (j.ok) location.href = j.to || location.href; else alert(j.msg || '실패');

  } else if (d.reply !== undefined) {
    const f = document.querySelector('.cform');
    f.parent_id.value = d.reply;
    f.querySelector('.replyto span').textContent = '↳ ' + d.nick + '님에게 답글';
    f.querySelector('.replyto').classList.add('on');
    f.scrollIntoView({ block: 'center' });
    f.content.focus();

  } else if (d.replyCancel !== undefined) {
    const f = document.querySelector('.cform');
    f.parent_id.value = '';
    f.querySelector('.replyto').classList.remove('on');
  }
});
