import React, { useEffect, useRef, useState } from 'react';
import { AudioLines, Archive, ChevronRight, Folder, MessageSquare, Plus } from 'lucide-react';
import { api } from './api';

export default function Sidebar({ selected, onChoose, onNew, state, report, onChanged, revision, children }) {
  const [projects, setProjects] = useState([]);
  const [groups, setGroups] = useState({});
  const [archived, setArchived] = useState(false);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState('');
  const [path, setPath] = useState('');
  const [pending, setPending] = useState(false);
  const [loading, setLoading] = useState({});
  const inFlight = useRef(new Set());
  const generation = useRef(0);
  const scope = useRef(archived);
  scope.current = archived;
  async function page(id, append = false, epoch = generation.current) {
    const key = `${epoch}:${id}`;
    if (inFlight.current.has(key)) return;
    inFlight.current.add(key); setLoading(previous => ({ ...previous, [id]: true }));
    const offset = append ? (groups[id]?.sessions.length || 0) : 0;
    try {
      const data = await api(`/sessions?project_id=${encodeURIComponent(id)}&archived=${scope.current}&limit=20&offset=${offset}`);
      if (epoch !== generation.current) return;
      setGroups(previous => ({ ...previous, [id]: { ...data, sessions: append ? [...new Map([...(previous[id]?.sessions || []), ...data.sessions].map(session => [session.id, session])).values()] : data.sessions } }));
    } catch (error) { if (epoch === generation.current) report(error); }
    finally { inFlight.current.delete(key); if (epoch === generation.current) setLoading(previous => ({ ...previous, [id]: false })); }
  }
  useEffect(() => {
    const epoch = ++generation.current;
    setGroups({}); setLoading({});
    api('/projects').then(data => {
      if (epoch !== generation.current) return;
      setProjects(data.projects);
      for (const id of ['', ...data.projects.map(project => project.id)]) page(id, false, epoch);
    }).catch(report);
    return () => { generation.current++; };
  }, [archived, revision]);
  async function mutate(url, method, body) {
    setPending(true);
    try { await api(url, { method, body }); onChanged(); return true; }
    catch (error) { report(error); return false; }
    finally { setPending(false); }
  }
  async function create(event) {
    event.preventDefault();
    if (await mutate('/projects', 'POST', { name: name.trim(), path: path.trim() })) {
      setCreating(false); setName(''); setPath('');
    }
  }
  const busy = state?.busy || pending;
  return <aside className="sidebar">
    <a className="brand" href="/" aria-label="咚咚首页"><span className="brand-icon"><AudioLines size={22} /></span><strong>咚咚<span className="muted text-xs font-normal ml-2">工作台</span></strong></a>
    <button className="new-session" disabled={busy} onClick={() => onNew(state?.current_project || null)}><Plus size={17} /><span>开启新对话</span></button>
    {children}
    <div className="session-label"><span>{archived ? '已归档' : '项目对话'}</span><button aria-label="创建项目" disabled={busy} onClick={() => setCreating(!creating)}><Plus size={14} /></button></div>
    {creating && <form className="project-form" onSubmit={create}><input aria-label="项目名称" placeholder="项目名称" value={name} onChange={e => setName(e.target.value)} required /><input aria-label="项目绝对路径" placeholder="本机目录绝对路径" value={path} onChange={e => setPath(e.target.value)} required /><button className="primary" disabled={busy}>创建项目</button></form>}
    <div className="sessions project-groups">{[{ id: '', name: '未分类' }, ...projects].map(project => <section key={project.id} className="project-group">
      <details open><summary><Folder size={13} /><span title={project.path}>{project.name}</span>{(state?.current_project || '') === project.id && <span className="project-current">语音</span>}</summary>
        <div className="project-actions">
          <button disabled={busy} title="显式切换语音执行项目" onClick={() => mutate('/projects/current', 'PUT', { project_id: project.id || null })}>{(state?.current_project || '') === project.id ? '当前语音项目' : '设为语音项目'}</button>
          <button disabled={busy} onClick={() => onNew(project.id || null)}>新对话</button>
          {project.id && <button disabled={busy} onClick={() => { const next = window.prompt('项目新名称', project.name); if (next?.trim()) mutate(`/projects/${project.id}`, 'PATCH', { name: next.trim() }); }}>重命名</button>}
        </div>
        {groups[project.id]?.sessions.map(session => <button key={session.id} className={selected === session.id ? 'selected' : ''} onClick={() => onChoose(session.id)}><MessageSquare size={13} /><span>{session.title}</span><ChevronRight size={12} /></button>)}
        {!groups[project.id] ? <p className="group-empty">加载中…</p> : !groups[project.id].sessions.length && <p className="group-empty">{archived ? '没有归档对话' : '还没有对话'}</p>}
        {groups[project.id]?.has_more && <button className="load-more" disabled={loading[project.id]} onClick={() => page(project.id, true)}>加载更多</button>}
      </details>
    </section>)}</div>
    <button className="archive-toggle" aria-pressed={archived} onClick={() => setArchived(!archived)}><Archive size={15} /><span>{archived ? '返回项目对话' : '查看归档'}</span></button>
    <div className="sidebar-footer"><span className={`dot ${state ? 'online' : ''}`} /><span>本机工作区</span><span className="ml-auto text-xs muted">v{state?.version || '0.1.0'}</span></div>
  </aside>;
}
