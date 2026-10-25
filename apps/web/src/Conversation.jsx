import React, { useEffect, useState } from 'react';
import { AudioLines, Check, ChevronRight, Circle, Terminal, X } from 'lucide-react';
import { api, statusText } from './api';

const toolNames = { memory_search: '检索记忆', memory_save: '保存记忆', shell: '执行命令', read_file: '读取文件', write_file: '写入文件', edit_file: '修改文件', list_dir: '查看目录', glob: '查找文件', grep: '搜索内容', update_plan: '更新计划', read_skill: '读取技能' };

function activity(events) {
  const items = [];
  let text = null;
  for (const event of events) {
    if (event.type === 'model_start') text = null;
    if (event.type === 'text') {
      if (!text) { text = { type: 'text', id: event.id, text: '' }; items.push(text); }
      text.text += event.data.delta;
    }
    if (event.type === 'tool_call') {
      text = null;
      items.push({ type: 'tool', id: event.id, ...event.data });
    }
    if (event.type === 'tool_result') {
      const call = items.find(item => item.type === 'tool' && !item.result && (event.data.id ? item.id === event.data.id : item.name === event.data.name));
      if (call) call.result = event.data;
    }
    if (event.type === 'done' && text?.text === event.data.text) items.splice(items.indexOf(text), 1);
  }
  return items;
}

function RunMessage({ run, liveEvents, fallback }) {
  const [saved, setSaved] = useState([]);
  const [error, setError] = useState('');
  const [now, setNow] = useState(Date.now());
  const events = liveEvents ?? saved;
  const terminal = [...events].reverse().find(e => ['done', 'error', 'stopped'].includes(e.type));
  const status = terminal ? ({ done: 'done', error: 'error', stopped: 'stopped' })[terminal.type] : run.status;
  const running = status === 'running';
  const [open, setOpen] = useState(running);
  useEffect(() => { setOpen(running); }, [running]);
  useEffect(() => {
    if (liveEvents !== undefined) return;
    let active = true;
    api(`/runs/${run.id}?include_events=true`).then(data => { if (active) { setSaved(data.events); setError(''); } }).catch(err => { if (active) setError(err.message); });
    return () => { active = false; };
  }, [run.id, run.status, liveEvents !== undefined]);
  useEffect(() => {
    if (!running) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [running]);
  const plan = [...events].reverse().find(e => e.type === 'plan')?.data.steps || [];
  const taskItems = run.items || [];
  const items = activity(events);
  const pendingTool = [...items].reverse().find(item => item.type === 'tool' && !item.result);
  const pendingApproval = [...events].reverse().find(e => e.type === 'approval' && !events.some(result => result.type === 'approval_result' && result.data.id === e.data.id));
  const warnings = events.filter(e => ['mcp_error', 'memory_error'].includes(e.type));
  const current = plan.find(step => step.status === 'in_progress');
  const next = plan.find(step => step.status === 'pending');
  const done = plan.filter(step => step.status === 'completed').length;
  const elapsed = Math.max(0, Math.floor(((running ? now : Date.parse(run.ended_at) || now) - Date.parse(run.started_at)) / 1000));
  const label = running ? (pendingApproval ? '等待你的批准' : pendingTool ? toolNames[pendingTool.name] || pendingTool.name : events.at(-1)?.type === 'text' ? '正在生成回复' : '正在等待模型响应') : statusText[status];
  const answer = events.find(e => e.type === 'done')?.data.text || (!running ? fallback : '');
  const failure = terminal?.data.message || run.error;
  return <article className="message assistant">
    <div className="message-label"><Terminal size={14} />咚咚<span className="badge">任务执行</span></div>
    <details className="execution" open={open} onToggle={e => setOpen(e.currentTarget.open)}>
      <summary><ChevronRight size={14} className="execution-chevron" /><span className={`dot ${running ? 'working' : status === 'done' ? 'online' : ''}`} /><span role="status">{label}{plan.length ? ` · ${done}/${plan.length} 步` : ''}</span><span className="muted ml-auto">{elapsed} 秒</span></summary>
      <div className="execution-body">
        {taskItems.length > 0 && <div className="task-items" aria-label="任务拆解">{taskItems.map(item => <div className="task-item" key={item.id}><span className={`dot ${item.status === 'running' ? 'working' : item.status === 'done' ? 'online' : ''}`} /><span>{item.instruction}</span><small>{statusText[item.status] || item.status} · {item.attempts} 次尝试{item.error ? ` · ${item.error}` : ''}</small></div>)}</div>}
        {plan.length > 0 && <ol className="plan">{plan.map((step, index) => <li key={index} className={step.status}>{step.status === 'completed' ? <Check size={14} /> : step.status === 'in_progress' && running ? <span className="dot working" /> : <Circle size={12} />}<span>{step.step}</span><small>{step.status === 'completed' ? '已完成' : step.status === 'in_progress' ? (running ? '进行中' : '未完成') : '待执行'}</small></li>)}</ol>}
        {running && <p className="execution-hint">{current ? `当前：${current.step}` : '正在处理任务，请稍候。'}{next && <><br />接下来：{next.step}</>}</p>}
        {warnings.map(event => <p className="execution-failure" key={event.id}>{event.type === 'memory_error' ? '记忆服务不可用' : '外部工具不可用'}：{event.data.message}</p>)}
        {items.map(item => item.type === 'text' ? <div className="message-text execution-text" key={item.id}>{item.text}</div> : <details className="tool-card" key={item.id}>
          <summary><Terminal size={13} />{toolNames[item.name] || item.name}{item.result ? (item.result.ok ? <Check size={13} /> : <X size={13} />) : <span className={`dot ${running ? 'working' : ''}`} />}<span className="muted ml-auto">{item.result ? `${item.result.ms} ms` : running ? '执行中' : '已中断'}</span></summary>
          <pre>{item.arguments}</pre>{item.result && <pre>{item.result.result}</pre>}
        </details>)}
        {error && <p role="alert">执行记录读取失败：{error}</p>}
        {!items.length && !running && !error && <p className="muted">没有保存更多执行细节。</p>}
      </div>
    </details>
    {answer && <div className="message-text final-answer">{answer}</div>}
    {!running && status !== 'done' && <p role="status" className="execution-failure">{statusText[status]}{failure ? `：${failure}` : ''}。未完成的步骤已保留在执行记录中。</p>}
  </article>;
}

export default function Conversation({ detail, watch, events }) {
  const messages = detail?.messages || [];
  const runs = [...(detail?.runs || [])].reverse();
  const groups = [];
  for (const message of messages.filter(m => !m.source || m.source === 'agent')) {
    if (message.role === 'user' || !groups.length) groups.push([]);
    groups[groups.length - 1].push(message);
  }
  let legacyIndex = 0;
  const hidden = new Set();
  const attached = new Map();
  for (const group of groups) {
    const user = group[0];
    const run = runs.find(r => r.user_message_id === user.id) || (detail.channel !== 'linx-voice' && user.role === 'user' ? runs.filter(r => !r.user_message_id)[legacyIndex++] : null);
    const fallback = [...group].reverse().find(m => m.role === 'assistant' && !m.tool_calls?.length)?.content || '';
    if (run) {
      attached.set(user.id, { run, fallback });
      group.slice(1).forEach(message => hidden.add(message.id));
    }
  }
  return messages.filter(message => !hidden.has(message.id)).map(message => {
    const linked = attached.get(message.id);
    const voice = message.source === 'voice';
    return <React.Fragment key={message.id}>
      <article className={`message ${message.role}${voice ? ' voice-message' : ''}`}>
        <div className="message-label">{voice ? <AudioLines size={14} /> : null}{message.role === 'user' ? '你' : '咚咚'}<span className="badge">{voice ? (message.role === 'user' ? '语音转写' : '语音回复') : message.role === 'user' ? (linked ? '任务指令' : '文字') : '回复'}</span>{voice && <time className="muted ml-auto">{new Date(message.created_at).toLocaleTimeString()}</time>}</div>
        <div className="message-text">{message.content}</div>
      </article>
      {linked && <RunMessage key={linked.run.id} run={linked.run} liveEvents={watch === linked.run.id ? events : undefined} fallback={linked.fallback} />}
    </React.Fragment>;
  });
}
