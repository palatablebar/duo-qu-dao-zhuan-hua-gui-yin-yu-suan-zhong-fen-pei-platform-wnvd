import { api } from '../api.js';
import { ENUMS, can, mergeTone } from '../config.js';
import { drawer, errorBox, errorMessage, esc, field, fmtPct, fmtTime, kv, table, toast } from '../ui.js';

const state = { status: '', batchId: '', linkQuery: '' };

function statStrip(summary) {
  const latest = summary.latest;
  return `<div class="stat-strip">
    <div class="stat"><span class="k">已受理</span><span class="v">${summary.accepted_total}</span><span class="hint">累计合并事件</span></div>
    <div class="stat"><span class="k">冲突</span><span class="v">${summary.conflict_total}</span><span class="hint">设备已绑定其他会员，保留先到</span></div>
    <div class="stat"><span class="k">失败</span><span class="v">${summary.failed_total}</span><span class="hint">事件号 / 设备号非法</span></div>
    <div class="stat"><span class="k">设备关联</span><span class="v">${summary.links_total}</span><span class="hint">${summary.batches_total} 个合并批次${latest ? ` · 最近 ${fmtTime(latest.created_at)}` : ''}</span></div>
  </div>`;
}

function scaleBlock(live) {
  const before = live.before || {};
  const after = live.after || {};
  const merged = live.merged_users || 0;
  return `<div class="ledger">
    <div class="ledger-row" style="grid-template-columns:128px 1fr 1fr">
      <div class="ledger-name"><b>身份规模</b><span>合并前 → 合并后</span></div>
      <div>
        <div class="pair"><span>合并前</span><div class="pair-track"><div class="pair-fill" style="width:100%"></div></div><b>${before.users || 0} 用户</b></div>
        <div class="pair" style="margin-top:6px"><span>合并后</span><div class="pair-track"><div class="pair-fill markov" style="width:${before.users ? Math.round(((after.users || 0) / before.users) * 100) : 0}%"></div></div><b>${after.users || 0} 用户</b></div>
      </div>
      <div class="ledger-delta">
        <span class="delta up">合并 ${merged} 个匿名身份</span>
        <div class="mono muted">触点 ${before.touchpoints || 0} → ${after.touchpoints || 0}</div>
        <div class="mono muted">转化率 ${fmtPct(before.conversion_rate)} → ${fmtPct(after.conversion_rate)}</div>
      </div>
    </div>
  </div>`;
}

function channelShares(live) {
  const rows = live.channels?.after || [];
  const beforeByCode = Object.fromEntries((live.channels?.before || []).map((row) => [row.channel_code, row]));
  if (!rows.length) return '<p class="muted">暂无渠道数据。</p>';
  return `<div class="ledger">${rows
    .map((row) => {
      const before = beforeByCode[row.channel_code] || {};
      return `<div class="ledger-row">
        <div class="ledger-name"><b>${esc(row.channel_name)}</b><span>${esc(row.channel_type_label)} · ${row.touchpoints} 触点 / ${row.conversions} 转化</span></div>
        <div class="ledger-pair">
          <div class="pair"><span>合并前</span><div class="pair-track"><div class="pair-fill" style="width:${Math.round((before.markov_share || 0) * 100)}%"></div></div><b>${fmtPct(before.markov_share)}</b></div>
          <div class="pair"><span>合并后</span><div class="pair-track"><div class="pair-fill markov" style="width:${Math.round((row.markov_share || 0) * 100)}%"></div></div><b>${fmtPct(row.markov_share)}</b></div>
        </div>
        <div class="ledger-delta">
          <span class="delta ${(row.markov_share || 0) - (before.markov_share || 0) >= 0 ? 'up' : 'down'}">${(((row.markov_share || 0) - (before.markov_share || 0)) * 100).toFixed(1)}pp</span>
          <div class="mono muted">last-click ${fmtPct(row.last_click_share)}</div>
        </div>
      </div>`;
    })
    .join('')}</div>`;
}

function batchEvents(detail) {
  const rows = detail.events || [];
  return `<div class="static-table">${table({
    rows,
    columns: [
      { key: 'event_key', label: '事件号', className: 'mono' },
      { key: 'device_id', label: '设备', className: 'mono', render: (row) => esc(row.device_id || '—') },
      { key: 'member_id', label: '会员', className: 'mono', render: (row) => esc(row.member_id || '—') },
      { key: 'status', label: '结果', render: (row) => `<span class="badge ${mergeTone(row.status)}">${esc(row.status_label)}</span>` },
      { key: 'reason_label', label: '原因', render: (row) => esc(row.reason_label || ENUMS.mergeReason[row.reason] || '') },
      { key: 'kept_member_id', label: '保留关联', className: 'mono', render: (row) => esc(row.kept_member_id || '—') },
      { key: 'touchpoint_count', label: '设备触点', className: 'num' },
    ],
    empty: { title: '这个批次没有事件', hint: '换一个状态筛选。' },
  })}</div>`;
}

function openBatch(view, ctx, batch) {
  const body = `${kv([
    ['受理人', esc(batch.operator_email)],
    ['来源', esc(batch.source === 'seed' ? '初始化导入' : '手动提交')],
    ['时间', fmtTime(batch.created_at)],
    ['事件数', `${batch.total_events}（重试跳过 ${batch.duplicate_count}）`],
    ['受理 / 冲突 / 失败', `${batch.accepted_count} / ${batch.conflict_count} / ${batch.failed_count}`],
    ['合并前', `${(batch.before || {}).users || 0} 用户 / ${(batch.before || {}).touchpoints || 0} 触点 / 转化率 ${fmtPct((batch.before || {}).conversion_rate)}`],
    ['合并后', `${(batch.after || {}).users || 0} 用户 / ${(batch.after || {}).touchpoints || 0} 触点 / 转化率 ${fmtPct((batch.after || {}).conversion_rate)}`],
    ['备注', esc(batch.note || '—')],
  ])}
  <div class="section-title" style="margin-top:12px"><h2 style="font-size:0.95rem">渠道占比（合并后）</h2><span class="meta">markov</span></div>
  ${channelShares({ channels: { after: batch.channels || [], before: batch.before_channels || [] }, before: batch.before || {} })}
  <div class="section-title" style="margin-top:12px"><h2 style="font-size:0.95rem">逐个事件</h2><span class="meta">按到达顺序处理</span></div>
  ${batchEvents(batch)}`;
  const { root, close } = drawer({
    title: `合并批次 #${batch.id}`,
    subtitle: `${esc(batch.batch_key || 'manual')} · ${batch.total_events} 个事件`,
    body,
    footer: `<button class="btn ghost" data-close-foot>关闭</button>`,
  });
  root.querySelector('[data-close-foot]')?.addEventListener('click', close);
}

function linksSection(links) {
  return `<div class="filter-bar" id="link-filter" style="margin-bottom:10px">
      ${field({ label: '按设备 / 会员查找', name: 'q', value: state.linkQuery, placeholder: 'DEV00005 或 U00005' })}
    </div>
    <div class="static-table">${table({
      rows: links.items,
      columns: [
        { key: 'device_id', label: '设备号', className: 'mono' },
        { key: 'member_id', label: '关联会员', className: 'mono' },
        { key: 'first_event_key', label: '建立关联的事件', className: 'mono' },
        { key: 'established_at', label: '建立时间', render: (row) => fmtTime(row.established_at) },
      ],
      empty: { title: '还没有设备关联', hint: '提交合并事件后，设备与会员的关联会出现在这里。' },
    })}</div>`;
}

function openCreate(view, ctx) {
  const { root, close } = drawer({
    title: '提交身份合并事件',
    subtitle: '每行一个 JSON：event_key / device_id / member_id / arrived_at',
    body: `<form id="merge-form" style="display:grid;gap:12px">
      <div class="field">
        <label for="events">合并事件（JSON 数组）</label>
        <textarea name="events" id="events" rows="9" style="width:100%;border:0;border-bottom:1px solid var(--rule-strong);background:transparent;padding:8px 0;font-family:var(--font-mono);font-size:0.75rem">[
  {"event_key": "manual-1", "device_id": "DEV00005", "member_id": "U00005", "arrived_at": "2026-10-16T10:09:00"}
]</textarea>
      </div>
      ${field({ label: '备注', name: 'note', placeholder: '例如：匿名浏览 → 登录后的设备关联' })}
      <div class="form-error" id="merge-error"></div>
      <button class="btn accent" type="submit">提交合并</button>
    </form>`,
  });
  root.querySelector('#merge-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.target;
    const button = form.querySelector('button[type=submit]');
    let events;
    try {
      events = JSON.parse(form.events.value);
      if (!Array.isArray(events) || !events.length) throw new Error('需要至少一个事件');
    } catch (err) {
      root.querySelector('#merge-error').textContent = `JSON 解析失败：${err.message}`;
      return;
    }
    button.disabled = true;
    button.textContent = '提交中…';
    try {
      const result = await api.post('/api/identity-merges', {
        events,
        note: form.note.value.trim() || null,
      });
      toast(`受理 ${result.accepted_count} · 冲突 ${result.conflict_count} · 失败 ${result.failed_count} · 跳过 ${result.duplicate_count}`);
      close();
      await identityPage.render(view, ctx);
    } catch (err) {
      root.querySelector('#merge-error').textContent = errorMessage(err);
      button.disabled = false;
      button.textContent = '提交合并';
    }
  });
}

export const identityPage = {
  async render(view, ctx) {
    state.batchId = ctx.query.batch_id || state.batchId;
    let summary;
    let batches;
    let links;
    try {
      summary = await api.get('/api/identity-merges/summary');
      batches = await api.get('/api/identity-merges', { limit: 30 });
      links = await api.get('/api/identity-merges/links', { q: state.linkQuery, limit: 20 });
    } catch (err) {
      view.innerHTML = errorBox(errorMessage(err));
      view.querySelector('#retry-btn')?.addEventListener('click', () => identityPage.render(view, ctx));
      return;
    }
    const live = summary.live || { before: {}, after: {}, channels: { before: [], after: [] } };
    view.innerHTML = `
      <section class="spread">
        <div>
          <div class="kicker">身份关联 / identity merge</div>
          <div class="figure-block">
            <div class="figure-label">合并后的设备关联</div>
            <div class="figure coral">${summary.links_total}</div>
            <div class="figure-note">匿名浏览设备与登录会员关联后，下一次归因按合并后的身份重建完整路径；历史明细保留原始 user_id 与 device_id，仍可回查。同一合并事件重试不会新增关联或重复触点。</div>
          </div>
          ${statStrip(summary)}
          <div style="margin-top:22px">
            <div class="section-title"><h2>合并前后身份规模</h2><span class="meta">users / touchpoints</span></div>
            ${scaleBlock(live)}
          </div>
        </div>
        <aside>
          <div class="section-title"><h2>当前口径</h2><span class="meta">before → after</span></div>
          <p class="policy-note">同一设备先后关联两个会员时按事件到达顺序处理：先到的事件建立关联，后到的记 1 条冲突并单独标出，不覆盖先到关联。缺失或非法的 event_key / 设备号 / 会员号记失败，不参与关联。</p>
          ${can(ctx.user.role, 'identity:write') ? '<button class="btn accent" id="new-merge" style="margin-top:14px">提交合并事件</button>' : '<p class="mono muted" style="margin-top:14px">只读角色可以查看，不能提交合并</p>'}
        </aside>
      </section>

      <section>
        <div class="section-title"><h2>渠道占比</h2><span class="meta">合并前后 markov 占比对照</span></div>
        ${channelShares(live)}
      </section>

      <section>
        <div class="section-title"><h2>合并批次</h2><span class="meta">${batches.total} 次受理 · 点行看逐事件</span></div>
        <div class="filter-bar" id="merge-filters">
          ${field({ label: '结果筛选', name: 'status', value: state.status, options: [{ value: '', label: '全部事件' }, ...Object.entries(ENUMS.mergeStatus).map(([value, label]) => ({ value, label }))] })}
          <div class="spacer"></div>
          <span class="mono muted">重试幂等：同一 event_key 只受理一次</span>
        </div>
        <div id="batch-table">${table({
          rows: batches.items,
          columns: [
            { key: 'id', label: '批次', className: 'mono', render: (row) => `#${row.id}` },
            { key: 'created_at', label: '受理时间', render: (row) => fmtTime(row.created_at) },
            { key: 'operator_email', label: '受理人', className: 'mono' },
            { key: 'total_events', label: '事件', className: 'num' },
            { key: 'accepted_count', label: '受理', className: 'num', render: (row) => `<span class="badge ok">${row.accepted_count}</span>` },
            { key: 'conflict_count', label: '冲突', className: 'num', render: (row) => `<span class="badge ${row.conflict_count ? 'warn' : ''}">${row.conflict_count}</span>` },
            { key: 'failed_count', label: '失败', className: 'num', render: (row) => `<span class="badge ${row.failed_count ? 'danger' : ''}">${row.failed_count}</span>` },
            { key: 'duplicate_count', label: '重试跳过', className: 'num' },
            { key: 'scale', label: '用户数 前 → 后', className: 'num', render: (row) => `${(row.before || {}).users || 0} → ${(row.after || {}).users || 0}` },
            { key: 'touch', label: '触点 前 → 后', className: 'num', render: (row) => `${(row.before || {}).touchpoints || 0} → ${(row.after || {}).touchpoints || 0}` },
          ],
          empty: { title: '还没有身份合并批次', hint: '提交合并事件后，这里会显示每次受理的结果。' },
        })}</div>
      </section>

      <section>
        <div class="section-title"><h2>设备关联</h2><span class="meta">${links.total} 条 · 先到先得，冲突不覆盖</span></div>
        <div id="links-wrap">${linksSection(links)}</div>
      </section>`;
    const linkFilter = view.querySelector('#link-filter');
    const applyLinkQuery = () => {
      state.linkQuery = linkFilter.querySelector('[name=q]').value.trim();
      identityPage.render(view, ctx);
    };
    linkFilter?.addEventListener('change', applyLinkQuery);
    linkFilter?.querySelector('[name=q]')?.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') {
        event.preventDefault();
        applyLinkQuery();
      }
    });
    const filters = view.querySelector('#merge-filters');
    filters?.addEventListener('change', () => {
      state.status = filters.querySelector('[name=status]').value;
      identityPage.render(view, ctx);
    });
    view.querySelectorAll('#batch-table table.data tbody tr').forEach((row) => {
      row.addEventListener('click', async () => {
        const batch = batches.items.find((item) => String(item.id) === row.dataset.id);
        if (!batch) return;
        let detail;
        try {
          detail = await api.get(`/api/identity-merges/${batch.id}`, { status: state.status || undefined });
        } catch (err) {
          toast(errorMessage(err), 'err');
          return;
        }
        openBatch(view, ctx, detail);
      });
    });
    view.querySelector('#new-merge')?.addEventListener('click', () => openCreate(view, ctx));
  },
};

export default identityPage;
