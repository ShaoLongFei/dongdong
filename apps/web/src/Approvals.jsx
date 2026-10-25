import React, { useEffect, useRef, useState } from 'react';
import { api } from './api';

const approvalKey = item => JSON.stringify([item.run_id, item.id]);

export default function Approvals({ report, onResolved, runIds = [] }) {
  const [items, setItems] = useState([]);
  const [busy, setBusy] = useState(new Set());
  const callbacks = useRef({ report, onResolved });
  callbacks.current = { report, onResolved };

  useEffect(() => {
    let active = true;
    let timer;
    async function refresh() {
      try {
        const result = await api('/approvals');
        if (active) setItems(result.approvals);
      } catch (error) {
        if (active) callbacks.current.report(error);
      } finally {
        if (active) timer = setTimeout(refresh, 1000);
      }
    }
    refresh();
    return () => { active = false; clearTimeout(timer); };
  }, []);

  async function decide(item, approved) {
    const key = approvalKey(item);
    setBusy(previous => new Set(previous).add(key));
    try {
      await api(`/approvals/${encodeURIComponent(item.run_id)}/${encodeURIComponent(item.id)}`, {
        method: 'POST', body: { approved },
      });
      setItems(previous => previous.filter(entry => approvalKey(entry) !== key));
      callbacks.current.onResolved?.();
    } catch (error) { callbacks.current.report(error); }
    finally {
      setBusy(previous => { const next = new Set(previous); next.delete(key); return next; });
    }
  }

  const visible = items.filter(item => runIds.includes(item.run_id));
  if (!visible.length) return null;
  return <section className="approvals" aria-label="待审批操作">
    <h2>操作需要你的批准</h2>
    {visible.map(item => <article className="approval-card" key={approvalKey(item)}>
      <h3>{item.name}</h3>
      <p className="muted text-xs">任务 {item.run_id}</p>
      <pre>{JSON.stringify(item.arguments, null, 2)}</pre>
      <div className="approval-actions">
        <button className="primary" disabled={busy.has(approvalKey(item))} onClick={() => decide(item, true)}>允许一次</button>
        <button disabled={busy.has(approvalKey(item))} onClick={() => decide(item, false)}>拒绝</button>
      </div>
    </article>)}
  </section>;
}
