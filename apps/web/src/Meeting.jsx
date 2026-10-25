import React, { useEffect, useRef, useState } from 'react';
import { Check, Code2, FileText, LoaderCircle, Map, RefreshCw, Square, X } from 'lucide-react';
import { api } from './api';

const phases = { discussion: '讨论中', generating: '整理方案中', ready: '待确认', executing: '实施中', result: '实施结果', interrupted: '已中断' };

export default function Meeting({ sid, workspace, report, onRun, onChange, readonly = false }) {
  const [meeting, setMeeting] = useState(null);
  const [directory, setDirectory] = useState(workspace || '.');
  const [pending, setPending] = useState(false);
  const [historical, setHistorical] = useState(null);
  const [loaded, setLoaded] = useState('');
  const [result, setResult] = useState(null);
  const [activity, setActivity] = useState(null);
  const generation = useRef(0);
  const alive = useRef(true);
  const reported = useRef('');
  const proposal = historical || meeting?.proposal;
  const current = !historical && proposal?.id === meeting?.current;
  const root = `/meetings/${sid}`;

  async function refresh(epoch = generation.current) {
    try {
      const data = await api(root);
      if (!alive.current || epoch !== generation.current) return;
      setMeeting(data);
      if (data?.run_id && ['generating', 'executing'].includes(data.phase)) onRun(data.run_id);
      if (data?.notice && reported.current !== data.notice) { reported.current = data.notice; report(data.notice); }
      if (data?.run_id) {
        const run = await api(`/runs/${data.run_id}?include_events=true`);
        if (alive.current && epoch === generation.current) {
          setResult(run.events.filter(e => e.type === 'meeting_result').at(-1)?.data || null);
          setActivity(run.events.filter(e => e.type === 'meeting_activity').at(-1)?.data || null);
        }
      }
    } catch (error) { if (alive.current && epoch === generation.current) report(error); }
  }
  useEffect(() => {
    alive.current = true;
    const epoch = ++generation.current;
    setHistorical(null); setLoaded(''); setResult(null); setActivity(null); setMeeting(null);
    refresh(epoch);
    const timer = setInterval(() => refresh(epoch), 2000);
    return () => { alive.current = false; ++generation.current; clearInterval(timer); };
  }, [sid]);
  useEffect(() => { setDirectory(workspace || '.'); }, [workspace]);

  async function perform(fn) {
    if (pending || readonly) return;
    setPending(true);
    try { await fn(); await refresh(); onChange(); }
    catch (error) { report(error); }
    finally { setPending(false); }
  }
  async function mode(enabled) {
    if (!sid) { report('请先开启一个新对话，再进入代码修改模式'); return; }
    await perform(() => api(root, { method: 'PUT', body: { enabled, workspace: directory } }));
  }
  async function action(name) {
    await perform(async () => {
      const data = await api(`${root}/actions`, { method: 'POST', body: { action: name, proposal_id: proposal?.id || '', hash: proposal?.hash || '' } });
      if (data.run_id) onRun(data.run_id);
      if (data.message) report(data.message);
      setHistorical(null);
    });
  }
  async function shown(item) {
    if (!current || !item || readonly) return;
    try {
      const artifact = await fetch(`/api${root}/artifacts/${item.id}/map.html`, { cache: 'no-store' });
      if (!artifact.ok || !artifact.headers.get('content-type')?.includes('text/html')) throw new Error('地图未成功加载，不能确认方案');
      await api(`${root}/proposals/${item.id}/shown`, { method: 'POST', body: { hash: item.hash } });
      setLoaded(item.id);
    } catch (error) { report(error); }
  }
  async function history(id) {
    try {
      setHistorical(id === meeting.current ? null : await api(`${root}/proposals/${id}`));
      setLoaded('');
    } catch (error) { report(error); }
  }

  if (!meeting?.enabled) return <section className="meeting-entry">
    <div><Code2 size={17} /><strong>代码修改模式</strong><span className="muted">讨论 → 方案与地图 → 确认 → 实施</span></div>
    <label>项目目录<input aria-label="会议项目目录" disabled={readonly} value={directory} onChange={e => setDirectory(e.target.value)} placeholder="工作区内的项目目录" /></label>
    <button disabled={pending || !sid || readonly} onClick={() => mode(true)}>进入代码修改模式</button>
    {meeting?.history?.length > 0 && <span className="muted">重新进入可回看 {meeting.history.length} 版方案</span>}
  </section>;
  const running = ['generating', 'executing'].includes(meeting.phase);
  const blocked = meeting.dirty || proposal?.payload.open_questions?.length > 0;
  return <section className="meeting-panel" aria-label="会议代码修改模式">
    <div className="meeting-heading"><Code2 size={19} /><strong>代码修改模式</strong><span className="badge">{phases[meeting.phase]}</span><button className="ml-auto" disabled={pending || running || readonly} onClick={() => mode(false)}><X size={14} />退出模式</button></div>
    <p className="muted text-xs">{meeting.workspace} · 说“咚咚，整理一下方案”生成或更新地图；说“按这个方案实施”确认当前版本。</p>
    <div className="meeting-actions"><button disabled={pending || running || readonly} onClick={() => action('generate')}>{running ? <LoaderCircle size={15} className="spin" /> : <RefreshCw size={15} />}整理方案</button>{running && !readonly && <button onClick={() => perform(() => api(`/runs/${meeting.run_id}/cancel`, { method: 'POST' }))}><Square size={14} />停止任务</button>}<span className="muted">讨论已保存 {meeting.seq} 条{meeting.dirty ? ' · 有尚未纳入方案的新意见' : ''}</span></div>
    {(meeting.error || meeting.notice) && <p className="notice" role="status">{meeting.error || meeting.notice}</p>}
    <details className="meeting-discussion" open={!proposal}><summary>会议讨论 · {meeting.discussion.length} 条</summary><ol>{meeting.discussion.map(row => <li key={row.id}><small>{row.source === 'voice' ? '语音' : '文字'} · {new Date(row.created_at).toLocaleTimeString()}</small><p>{row.text}</p></li>)}</ol></details>
    {proposal && <>
      <div className="meeting-heading"><Map size={17} /><strong>方案 v{proposal.version}</strong><span className="muted">{current ? '当前方案' : '历史版本 · 仅供回看'}</span><select aria-label="方案版本" value={proposal.id} onChange={e => history(e.target.value)}>{meeting.history.map(item => <option key={item.id} value={item.id}>方案 v{item.version}</option>)}</select></div>
      <h3>{proposal.payload.summary}</h3><p className="meeting-copy">{proposal.payload.solution}</p>
      <details><summary>预期行为、修改范围与验证</summary><p className="meeting-copy">{proposal.payload.expected}</p><ul>{proposal.payload.files.map(name => <li key={name}><code>{name}</code></li>)}</ul><p className="meeting-copy">{proposal.payload.constraints_text}</p><ul>{proposal.payload.verification.map(item => <li key={item}>{item}</li>)}</ul></details>
      {!!proposal.payload.open_questions?.length && <p className="notice">待决问题：{proposal.payload.open_questions.join('；')}</p>}
      {!!proposal.payload.limitations?.length && <p className="muted">覆盖缺口：{proposal.payload.limitations.join('；')}</p>}
      <iframe key={proposal.id} title={`方案 v${proposal.version} 架构与改动地图`} sandbox="allow-scripts" referrerPolicy="no-referrer" src={`/api${root}/artifacts/${proposal.id}/map.html`} onLoad={() => shown(proposal)} />
      {current && <div className="meeting-confirm"><span>{readonly ? '对话已归档；恢复后可继续实施' : blocked ? '请处理新意见或待决问题，再重新整理方案' : loaded !== proposal.id ? '正在加载地图，加载后可确认' : `确认后直接实施方案 v${proposal.version}`}</span><button disabled={readonly || pending || blocked || loaded !== proposal.id || running || proposal.status !== 'ready' || meeting.phase !== 'ready'} onClick={() => action('confirm')}><Check size={15} />确认此方案并实施</button>{meeting.phase === 'interrupted' && ['claimed', 'running'].includes(proposal.status) && <button disabled={readonly || pending || blocked || loaded !== proposal.id} onClick={() => action('resume')}>恢复已确认方案</button>}</div>}
    </>}
    {activity && running && <iframe title="当前实施阶段地图" sandbox="allow-scripts" src={`/api${root}/artifacts/${activity.artifact}/map.html?stage=${activity.sequence}`} />}
    {result && <div className="meeting-result"><h3><FileText size={17} />实施与验证记录</h3><p className="muted">{result.verification}</p><pre>{result.changes || '没有已跟踪文件差异；请结合工具记录核对新增文件'}</pre>{result.checks.map((check, index) => <details key={index}><summary>{check.status === 'passed' ? '✓' : '×'} {check.command}</summary><pre>{check.summary}</pre></details>)}<a href={`/api${root}/artifacts/${result.artifact}/diff.txt`} target="_blank" rel="noreferrer">查看实际 diff</a><iframe title="实施阶段地图与活动回放" sandbox="allow-scripts" src={`/api${root}/artifacts/${result.artifact}/map.html`} /></div>}
    {!!meeting.controls.length && <details><summary>召唤与确认记录</summary>{meeting.controls.map(item => <p key={item.id}><small>{new Date(item.created_at).toLocaleTimeString()} · {item.source}</small> {item.text} — {item.result}</p>)}</details>}
  </section>;
}
