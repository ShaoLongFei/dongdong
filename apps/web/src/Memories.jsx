import React, { useEffect, useRef, useState } from 'react';
import { Brain, Trash2 } from 'lucide-react';
import { api } from './api';

export default function Memories({ report }) {
  const [projects, setProjects] = useState([]);
  const [scope, setScope] = useState('');
  const [data, setData] = useState(null);
  const [text, setText] = useState('');
  const [pending, setPending] = useState(false);
  const generation = useRef(0);
  useEffect(() => { api('/projects').then(result => setProjects(result.projects)).catch(report); }, []);
  async function load(project, epoch) {
    try {
      const result = await api(`/memories?project_id=${encodeURIComponent(project)}`);
      if (generation.current === epoch) setData(result);
    } catch (error) { if (generation.current === epoch) report(error); }
  }
  useEffect(() => {
    const epoch = ++generation.current;
    setData(null); setText('');
    load(scope, epoch);
    return () => { generation.current++; };
  }, [scope]);
  async function mutate(method, body) {
    const project = scope;
    const epoch = generation.current;
    setPending(true);
    try {
      await api('/memories', { method, body: { ...body, project_id: project || null } });
      if (generation.current === epoch) { setText(''); await load(project, epoch); }
    } catch (error) { if (generation.current === epoch) report(error); }
    finally { setPending(false); }
  }
  return <section className="settings memories"><div><p className="eyebrow">MEMORY</p><h2>让经验，留在合适的地方。</h2><p className="muted">全局记忆适用于所有项目；项目记忆仅在该项目中使用。</p></div>
    <label>记忆范围<select value={scope} onChange={e => setScope(e.target.value)}><option value="">全局记忆</option>{projects.map(project => <option key={project.id} value={project.id}>{project.name}</option>)}</select></label>
    {!data ? <p className="muted" role="status">正在读取记忆…</p> : !data.enabled ? <article><Brain size={22} /><h3>记忆尚未启用</h3><p className="muted">请为服务进程设置 OPENAI_API_KEY 环境变量并重启服务。</p></article> : <>
      <form onSubmit={e => { e.preventDefault(); if (text.trim() && !pending) mutate('POST', { text: text.trim() }); }}><label>添加到{scope ? '当前项目' : '全局'}<textarea aria-label="新记忆" rows={3} value={text} onChange={e => setText(e.target.value)} placeholder="记录值得保留的偏好、约定或事实…" /></label><button className="primary" disabled={pending || !text.trim()}>保存记忆</button></form>
      <div className="memory-list">{data.memories.length ? data.memories.map(memory => <article className="memory-card" key={memory.id}><p>{memory.memory}</p><button aria-label="删除这条记忆" disabled={pending} onClick={() => mutate('DELETE', { memory_ids: [memory.id] })}><Trash2 size={14} />删除</button></article>) : <p className="muted">这里还没有记忆。</p>}</div>
    </>}
  </section>;
}
