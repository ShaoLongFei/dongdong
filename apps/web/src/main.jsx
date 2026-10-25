import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import * as Tabs from '@radix-ui/react-tabs';
import { ArrowUp, AudioLines, ChevronRight, Circle, MessageSquare, Mic, MicOff, Plus, Settings2, Square, Workflow, BookOpen, Plug } from 'lucide-react';
import { api, statusText } from './api';
import Settings from './Settings';
import Conversation from './Conversation';
import Skills from './Skills';
import MCP from './MCP';
import Approvals from './Approvals';
import Sidebar from './Sidebar';
import Memories from './Memories';
import './style.css';

export function App() {
  const [projects, setProjects] = useState([]);
  const [revision, setRevision] = useState(0);
  const [selected, setSelected] = useState('');
  const [detail, setDetail] = useState(null);
  const [state, setState] = useState(null);
  const [tab, setTab] = useState('chat');
  const [input, setInput] = useState('');
  const [notice, setNotice] = useState('');
  const [sending, setSending] = useState(false);
  const [editing, setEditing] = useState(false);
  const [voicePending, setVoicePending] = useState(false);
  const [events, setEvents] = useState([]);
  const [watch, setWatch] = useState('');
  const [runDetail, setRunDetail] = useState(null);
  const end = useRef(null);
  const selection = useRef('');
  const detailRequest = useRef(0);
  const watching = useRef('');
  watching.current = watch;
  const report = error => setNotice(error instanceof Error ? error.message : error);
  async function refresh() {
    try {
      const health = await api('/state');
      setState(health);
      if (health.active_run && health.task_session && !selection.current) { choose(health.task_session); return; }
      if (health.voice_session && !selection.current) { choose(health.voice_session); return; }
      if (selection.current && !watching.current) await load(selection.current);
    } catch (error) { report(error); }
  }
  async function load(id, resume = true) {
    const request = ++detailRequest.current;
    const data = await api(`/sessions/${id}`);
    if (selection.current !== id || request !== detailRequest.current) return;
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
  useEffect(() => { end.current?.scrollIntoView({ block: 'end' }); }, [events.length, detail?.messages.length, selected, tab]);
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
  async function changed() {
    setRevision(value => value + 1);
    api('/projects').then(data => setProjects(data.projects)).catch(report);
    await refresh();
  }
  useEffect(() => { api('/projects').then(data => setProjects(data.projects)).catch(report); }, []);
  useEffect(() => { setRevision(value => value + 1); }, [state?.task_session, state?.voice_session, state?.active_run]);
  async function updateSession(body) {
    if (editing) return;
    setEditing(true);
    const id = selection.current;
    try {
      await api(`/sessions/${id}`, { method: 'PATCH', body }); await changed();
      if ('project_id' in body) setNotice('已移动对话：下一轮任务使用新项目目录和记忆范围，既有记忆不会移动。');
    }
    catch (error) { report(error); }
    finally { setEditing(false); }
  }
  async function newSession(project_id = state?.current_project || null) {
    try {
      const session = await api('/sessions', { method: 'POST', body: { project_id } });
      choose(session.id); setTab('chat'); changed();
    } catch (error) { report(error); }
  }
  async function toggleVoice() {
    if (voicePending) return;
    setVoicePending(true);
    try {
      const result = await api('/voice', { method: 'PUT', body: { enabled: !state?.voice_enabled } });
      setState(previous => ({ ...previous, voice_enabled: result.enabled }));
      await refresh();
    } catch (error) { report(error); }
    finally { setVoicePending(false); }
  }
  async function send(event) {
    event.preventDefault();
    if (!input.trim() || sending || detail?.archived_at) return;
    setSending(true); setNotice('');
    try {
      let sid = selected;
      if (!sid) {
        const session = await api('/sessions', { method: 'POST', body: { title: input.trim().slice(0, 24), project_id: state?.current_project || null } });
        sid = session.id; selection.current = sid; setSelected(sid);
      }
      const result = await api(`/sessions/${sid}/messages`, { method: 'POST', body: { text: input } });
      if (selection.current !== sid) { refresh(); return; }
      setInput(''); setRunDetail(null); setEvents([]); setWatch(result.run_id);
      await load(sid, false); changed();
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
  const busy = sending || editing || Boolean(watch) || state?.busy;
  const messages = detail?.messages || [];
  return <Tabs.Root value={tab} onValueChange={setTab} className="app">
    <Sidebar selected={selected} onChoose={id => { choose(id); setTab('chat'); }} onNew={newSession} state={state} report={report} onChanged={changed} revision={revision}>
      <Tabs.List className="navigation" aria-label="工作台页面">
        <Tabs.Trigger value="chat"><MessageSquare size={16} />对话</Tabs.Trigger>
        <Tabs.Trigger value="runs"><Workflow size={16} />运行记录</Tabs.Trigger>
        <Tabs.Trigger value="skills"><BookOpen size={16} />技能</Tabs.Trigger>
        <Tabs.Trigger value="mcp"><Plug size={16} />MCP</Tabs.Trigger>
        <Tabs.Trigger value="memories"><BookOpen size={16} />记忆</Tabs.Trigger>
        <Tabs.Trigger value="settings"><Settings2 size={16} />设置</Tabs.Trigger>
      </Tabs.List>
    </Sidebar>
    <main className="main">
      <header className="topbar"><span className="muted">工作台</span><ChevronRight size={12} /><strong>{tab === 'chat' ? detail?.title || '新对话' : ({ runs: '运行记录', skills: '技能', mcp: 'MCP', memories: '记忆', settings: '设置' })[tab]}</strong><button className="voice-toggle ml-auto" aria-pressed={Boolean(state?.voice_enabled)} disabled={!state || voicePending} onClick={toggleVoice} title="关闭会停止麦克风、播报和语音连接">{state?.voice_enabled ? <Mic size={14} /> : <MicOff size={14} />}{voicePending ? '切换中…' : state?.voice_enabled ? '关闭语音' : '开启语音'}</button><span className="badge"><span className={`dot ${busy ? 'working' : 'online'}`} />{busy ? '任务执行中' : '就绪'}</span></header>
      <Approvals report={report} onResolved={refresh} />
      {state?.voice_error && <div className="notice" role="alert">语音启动失败：{state.voice_error}</div>}
      {notice && <div role="status" className="notice">{notice}<button aria-label="关闭提示" onClick={() => setNotice('')}>×</button></div>}
      {state && !state.model_ready && tab !== 'settings' && <button className="setup-banner" onClick={() => setTab('settings')}>{state.error} · 前往设置 <ChevronRight size={14} /></button>}
      <Tabs.Content value="chat" className="chat-page">
        <div className="voice-preview"><AudioLines size={14} /><span>语音 · {({ connected: '已连接', connecting: '连接中', disconnected: '重连中', disabled: '未开启', error: '启动失败', starting: '启动中' })[state?.voice?.connection] || '未开启'}</span>{state?.voice?.last_stt && <span>你说：{state.voice.last_stt}</span>}{state?.voice?.last_tts && <span className="muted">咚咚：{state.voice.last_tts}</span>}{state?.voice_session && <button className="voice-history" onClick={() => choose(state.voice_session)}>查看语音对话 →</button>}</div>
        {state?.task_session && state.task_session !== selected && <button className="task-banner" onClick={() => choose(state.task_session)}><Workflow size={15} /><span className={`dot ${state.active_run ? 'working' : 'online'}`} />{state.active_run ? '任务正在执行 · 查看当前步骤和接下来的安排' : '查看最近任务的结果和执行过程'}<ChevronRight size={14} /></button>}
        {detail && <div className="session-toolbar"><span title={detail.project_path || '默认工作区'}>{detail.project_name || '未分类'} · {detail.project_path || '默认工作区'}</span><label>移动到<select aria-label="移动会话到项目" disabled={busy || Boolean(detail.archived_at)} value={detail.project_id || ''} onChange={e => updateSession({ project_id: e.target.value || null })}><option value="">未分类</option>{projects.map(project => <option key={project.id} value={project.id}>{project.name}</option>)}</select></label><button disabled={busy} onClick={() => updateSession({ archived: !detail.archived_at })}>{detail.archived_at ? '恢复对话' : '归档对话'}</button></div>}
        <div className="conversation">
          {!messages.length && !watch ? <div className="welcome"><span className="welcome-icon"><AudioLines size={34} /></span><p className="eyebrow">YOUR LOCAL COMPANION</p><h1>想做什么，交给咚咚。</h1><p className="muted">读代码、改文件、跑命令。一起把想法变成进展。</p><div className="suggestions">{['读一下工作区的 README，概括项目现状', '列出项目文件，帮我理解代码结构', '检查当前 git 状态，给出下一步建议'].map((text, i) => <button key={text} onClick={() => setInput(text)}><span className="muted">0{i + 1}</span>{text}<ArrowUp size={14} /></button>)}</div></div> : <div className="messages">
            <Conversation detail={detail} watch={watch} events={events} />
          </div>}
          <div ref={end} />
        </div>
        {detail?.archived_at ? <div className="archive-notice">此对话已归档，恢复后可以继续交流。</div> : <form className="composer" onSubmit={send}><textarea aria-label="给咚咚的消息" placeholder="告诉咚咚，你想完成什么…" value={input} onChange={e => setInput(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); e.currentTarget.form.requestSubmit(); } }} rows={3} /><div className="composer-bottom"><span className="workspace" title={(detail ? detail.project_path : state?.workspace)}><Circle size={11} />{((detail ? detail.project_path : state?.workspace))?.split('/').pop() || '本地工作区'}</span><span className="muted text-xs ml-auto">Shift + Enter 换行</span>{watch || state?.active_run ? <button type="button" className="send stop" onClick={stop} aria-label="停止任务"><Square size={15} /></button> : <button className="send" disabled={busy || !state?.model_ready || !input.trim()} aria-label="发送消息"><ArrowUp size={19} /></button>}</div></form>}
        <p className="footnote">咚咚可以修改文件和执行命令，请留意工具的执行结果。</p>
      </Tabs.Content>
      <Tabs.Content value="runs" className="page-scroll"><p className="eyebrow">ACTIVITY</p><h2>每一步，都有迹可循。</h2><p className="muted mb-6">当前会话的执行记录 · {detail?.runs.length || 0} 次运行</p><div className="run-list">{detail?.runs.map(run => <button key={run.id} className="run-row" disabled={run.status === 'running'} onClick={() => inspect(run)}><Workflow size={17} /><div><strong>{run.id}</strong><span className="muted text-xs">{new Date(run.started_at).toLocaleString()}</span></div><span className={`badge ${run.status}`}>{statusText[run.status]}</span><ChevronRight size={14} /></button>) || <p className="muted">开启对话后，运行记录会保存在这里。</p>}</div>{runDetail && <section className="run-inspector"><h3>运行 {runDetail.id}</h3><p>{runDetail.error}</p><p className="muted">用量：{JSON.stringify(runDetail.usage)}</p>{runDetail.events.map(event => <details className="tool-card" key={event.id}><summary>{event.type} {event.data.name}</summary><pre>{JSON.stringify(event.data, null, 2)}</pre></details>)}</section>}</Tabs.Content>
      <Tabs.Content value="skills" className="page-scroll"><Skills report={report} /></Tabs.Content>
      <Tabs.Content value="mcp" className="page-scroll"><MCP report={report} /></Tabs.Content>
      <Tabs.Content value="memories" className="page-scroll"><Memories report={report} /></Tabs.Content>
      <Tabs.Content value="settings" className="page-scroll"><Settings onSaved={refresh} report={report} /></Tabs.Content>
    </main>
  </Tabs.Root>;
}

if (typeof document !== 'undefined') createRoot(document.getElementById('root')).render(<App />);
