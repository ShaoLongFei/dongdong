import React, { useEffect, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { api } from './api';

export default function Skills({ report }) {
  const [skills, setSkills] = useState(null);
  const [detail, setDetail] = useState(null);
  const [busy, setBusy] = useState(false);
  async function refresh() {
    setBusy(true);
    try {
      const data = await api('/skills');
      setSkills(data.skills);
      setDetail(null);
    } catch (error) { report(error); }
    finally { setBusy(false); }
  }
  useEffect(() => { refresh(); }, []);
  async function toggle(skill) {
    setBusy(true);
    try {
      await api(`/skills/${encodeURIComponent(skill.name)}`, {
        method: 'PUT', body: { enabled: !skill.enabled },
      });
      setSkills(items => items.map(item => item.name === skill.name
        ? { ...item, enabled: !skill.enabled } : item));
      if (detail?.name === skill.name) setDetail(null);
      report('技能设置已保存，下一任务生效');
    } catch (error) { report(error); }
    finally { setBusy(false); }
  }
  async function show(skill) {
    setBusy(true);
    try { setDetail(await api(`/skills/${encodeURIComponent(skill.name)}`)); }
    catch (error) { report(error); }
    finally { setBusy(false); }
  }
  return <section className="settings">
    <div><p className="eyebrow">LOCAL SKILLS</p><h2>本地技能</h2>
      <p className="muted">仅发现这台 Mac 的 ~/.dongdong/skills/&lt;名称&gt;/SKILL.md（自定义 DONGDONG_HOME 时以该目录为准）。启停对下一任务生效，任务运行时无法修改。</p>
      <p className="muted text-xs">将已有技能放入本地目录后刷新；此处不下载或编辑技能。损坏或超过 32KB 的文件不会列出。</p>
    </div>
    <button className="self-start" onClick={refresh} disabled={busy}><RefreshCw size={15} />刷新</button>
    {skills === null ? <p className="muted">正在读取技能…</p>
      : skills.length === 0 ? <p className="muted">尚未发现可用的本地技能。</p>
        : skills.map(skill => <article key={skill.name}>
          <h3>{skill.name}</h3><p className="muted">{skill.description || '暂无描述'}</p>
          <label><input type="checkbox" checked={skill.enabled} disabled={busy}
            onChange={() => toggle(skill)} />{skill.enabled ? '已启用' : '已禁用'}</label>
          <button disabled={busy || !skill.enabled} onClick={() => show(skill)}>查看正文</button>
        </article>)}
    {detail && <article><h3>{detail.name} · 正文</h3>
      <button onClick={() => setDetail(null)}>收起</button>
      <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{detail.content}</pre>
    </article>}
  </section>;
}
