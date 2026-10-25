import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import * as Tabs from '@radix-ui/react-tabs';
import { ArrowUp, AudioLines, Check, ChevronRight, Circle, MessageSquare, Plus, Settings2, Square, Terminal, Workflow } from 'lucide-react';
import { api, statusText } from './api';
import Settings from './Settings';
import './style.css';

function App() {
  const [sessions, setSessions] = useState([]);
  const [selected, setSelected] = useState('');
  const [detail, setDetail] = useState(null);
  const [state, setState] = useState(null);
  const [tab, setTab] = useState('chat');
  const [input, setInput] = useState('');
  const [notice, setNotice] = useState('');
  const [sending, setSending] = useState(false);
  const [events, setEvents] = useState([]);
  const [watch, setWatch] = useState('');
  const [runDetail, setRunDetail] = useState(null);
  const end = useRef(null);
  const selection = useRef('');
  const report = error => setNotice(error instanceof Error ? error.message : error);
  async function refresh() {
    try {
      const [health, list] = await Promise.all([api('/state'), api('/sessions')]);
      setState(health); setSessions(list.sessions);
      if (!selection.current && list.sessions.length) choose(list.sessions[0].id);
    } catch (error) { report(error); }
  }
  async function load(id, resume = true) {
    const data = await api(`/sessions/${id}`);
    if (selection.current !== id) return;
    setDetail(data);
    const running = data.runs.find(run => run.status === 'running');
    if (running && resume) setWatch(running.id);
  }
  function choose(id) {
    selection.current = id; setSelected(id); setDetail(null); setEvents([]); setWatch(''); setRunDetail(null);
    load(id).catch(report);
  }
  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 3000);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => { end.current?.scrollIntoView({ block: 'end' }); }, [events, detail, tab]);
  useEffect(() => {
    if (!watch) return;
    setEvents([]);
    const source = new EventSource(`/api/runs/${watch}/events`);
    let active = true;
    const seen = new Set();
    source.onmessage = event => {
      if (!active) return;
      const item = JSON.parse(event.data);
      if (seen.has(item.id)) return;
      seen.add(item.id);
      setEvents(previous => [...previous, item]);
    };
    source.addEventListener('end', () => {
      if (!active) return;
      source.close(); setWatch('');
      load(selection.current).catch(report); refresh();
    });
    source.onerror = () => {
      if (!active) return;
      source.close(); setWatch(''); report('实时连接中断，正在重新读取任务');
      load(selection.current).catch(report); refresh();
    };
    return () => { active = false; source.close(); };
  }, [watch]);
  async function newSession() {
    try {
      const session = await api('/sessions', { method: 'POST' });
      choose(session.id); setTab('chat'); refresh();
    } catch (error) { report(error); }
  }
  async function send(event) {
    event.preventDefault();
    if (!input.trim() || sending) return;
    setSending(true); setNotice('');
    try {
      let sid = selected;
      if (!sid) {
        const session = await api('/sessions', { method: 'POST', body: { title: input.trim().slice(0, 24) } });
        sid = session.id; selection.current = sid; setSelected(sid);
      }
      const result = await api(`/sessions/${sid}/messages`, { method: 'POST', body: { text: input } });
      if (selection.current !== sid) { refresh(); return; }
      setInput(''); setRunDetail(null); setEvents([]); setWatch(result.run_id);
      await load(sid, false); refresh();
    } catch (error) { report(error); }
    finally { setSending(false); }
  }
  async function stop() {
    try { await api(`/runs/${watch || state?.active_run}/cancel`, { method: 'POST' }); }
    catch (error) { report(error); }
  }
  async function inspect(run) {
    const session = selected;
    try {
      const response = await fetch(`/api/runs/${run.id}/events`);
      if (!response.ok) throw new Error('无法读取运行记录');
      // 历史运行的流会自行结束；活动运行由对话页的 EventSource 消费。
      const text = await response.text();
      if (selection.current !== session) return;
      setRunDetail({ ...run, events: text.split('\n\n').filter(x => x.startsWith('data: ')).map(x => JSON.parse(x.slice(6))) });
    } catch (error) { report(error); }
  }
  const busy = sending || Boolean(watch) || state?.busy;
  const live = events.filter(e => e.type === 'text').map(e => e.data.delta).join('');
  const calls = events.filter(e => ['tool_call', 'tool_result', 'error'].includes(e.type));
  const messages = detail?.messages || [];
  return <Tabs.Root value={tab} onValueChange={setTab} className="app">
    <aside className="sidebar">
      <a className="brand" href="/" aria-label="咚咚首页"><span className="brand-icon"><AudioLines size={22} /></span><strong>咚咚<span className="muted text-xs font-normal ml-2">工作台</span></strong></a>
      <button className="new-session" onClick={newSession}><Plus size={17} />开启新对话<span className="ml-auto muted">↗</span></button>
      <Tabs.List className="navigation" aria-label="工作台页面">
        <Tabs.Trigger value="chat"><MessageSquare size={16} />对话</Tabs.Trigger>
        <Tabs.Trigger value="runs"><Workflow size={16} />运行记录</Tabs.Trigger>
        <Tabs.Trigger value="settings"><Settings2 size={16} />设置</Tabs.Trigger>
      </Tabs.List>
      <div className="session-label">最近对话<span>{sessions.length}</span></div>
      <div className="sessions">{sessions.map(session => <button key={session.id} className={selected === session.id ? 'selected' : ''} onClick={() => { choose(session.id); setTab('chat'); }}><MessageSquare size={13} /><span>{session.title}</span><ChevronRight size={12} /></button>)}</div>
      <div className="sidebar-footer"><span className={`dot ${state ? 'online' : ''}`} /><span>本机工作区</span><span className="ml-auto text-xs muted">v{state?.version || '0.1.0'}</span></div>
    </aside>
    <main className="main">
      <header className="topbar"><span className="muted">工作台</span><ChevronRight size={12} /><strong>{tab === 'chat' ? detail?.title || '新对话' : tab === 'runs' ? '运行记录' : '设置'}</strong><span className="badge ml-auto"><span className={`dot ${busy ? 'working' : 'online'}`} />{busy ? '任务执行中' : '就绪'}</span></header>
      {notice && <div role="status" className="notice">{notice}<button aria-label="关闭提示" onClick={() => setNotice('')}>×</button></div>}
      {state && !state.model_ready && tab !== 'settings' && <button className="setup-banner" onClick={() => setTab('settings')}>{state.error} · 前往设置 <ChevronRight size={14} /></button>}
      <Tabs.Content value="chat" className="chat-page">
        <div className="conversation">
          {!messages.length && !watch ? <div className="welcome"><span className="welcome-icon"><AudioLines size={34} /></span><p className="eyebrow">YOUR LOCAL COMPANION</p><h1>想做什么，交给咚咚。</h1><p className="muted">读代码、改文件、跑命令。一起把想法变成进展。</p><div className="suggestions">{['读一下工作区的 README，概括项目现状', '列出项目文件，帮我理解代码结构', '检查当前 git 状态，给出下一步建议'].map((text, i) => <button key={text} onClick={() => setInput(text)}><span className="muted">0{i + 1}</span>{text}<ArrowUp size={14} /></button>)}</div></div> : <div className="messages">
            {messages.map(message => <article className={`message ${message.role}`} key={message.id}><div className="message-label">{message.role === 'user' ? '你' : message.role === 'tool' ? <><Terminal size={13} />工具结果</> : <><AudioLines size={14} />咚咚</>}</div><div className="message-text">{message.content}</div>{message.tool_calls?.map(call => <details className="tool-card" key={call.id}><summary><Terminal size={13} />{call.function.name}</summary><pre>{call.function.arguments}</pre></details>)}</article>)}
            {watch && <article className="message assistant"><div className="message-label"><AudioLines size={14} />咚咚<span className="dot working" /></div><div className="message-text">{live || '正在处理…'}</div>{calls.map(event => <details className="tool-card" key={event.id}><summary><Terminal size={13} />{event.data.name || '运行错误'}{event.type === 'tool_result' && <Check size={13} />}</summary><pre>{event.data.arguments || event.data.result || event.data.message}</pre></details>)}</article>}
          </div>}
          <div ref={end} />
        </div>
        <form className="composer" onSubmit={send}><textarea aria-label="给咚咚的消息" placeholder="告诉咚咚，你想完成什么…" value={input} onChange={e => setInput(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); e.currentTarget.form.requestSubmit(); } }} rows={3} /><div className="composer-bottom"><span className="workspace" title={state?.workspace}><Circle size={11} />{state?.workspace?.split('/').pop() || '本地工作区'}</span><span className="muted text-xs ml-auto">Shift + Enter 换行</span>{watch || state?.active_run ? <button type="button" className="send stop" onClick={stop} aria-label="停止任务"><Square size={15} /></button> : <button className="send" disabled={busy || !state?.model_ready || !input.trim()} aria-label="发送消息"><ArrowUp size={19} /></button>}</div></form>
        <p className="footnote">咚咚可以修改文件和执行命令，请留意工具的执行结果。</p>
      </Tabs.Content>
      <Tabs.Content value="runs" className="page-scroll"><p className="eyebrow">ACTIVITY</p><h2>每一步，都有迹可循。</h2><p className="muted mb-6">当前会话的执行记录 · {detail?.runs.length || 0} 次运行</p><div className="run-list">{detail?.runs.map(run => <button key={run.id} className="run-row" disabled={run.status === 'running'} onClick={() => inspect(run)}><Workflow size={17} /><div><strong>{run.id}</strong><span className="muted text-xs">{new Date(run.started_at).toLocaleString()}</span></div><span className={`badge ${run.status}`}>{statusText[run.status]}</span><ChevronRight size={14} /></button>) || <p className="muted">开启对话后，运行记录会保存在这里。</p>}</div>{runDetail && <section className="run-inspector"><h3>运行 {runDetail.id}</h3><p>{runDetail.error}</p><p className="muted">用量：{JSON.stringify(runDetail.usage)}</p>{runDetail.events.map(event => <details className="tool-card" key={event.id}><summary>{event.type} {event.data.name}</summary><pre>{JSON.stringify(event.data, null, 2)}</pre></details>)}</section>}</Tabs.Content>
      <Tabs.Content value="settings" className="page-scroll"><Settings onSaved={refresh} report={report} /></Tabs.Content>
    </main>
  </Tabs.Root>;
}

createRoot(document.getElementById('root')).render(<App />);
