import React, { useEffect, useState } from 'react';
import { api } from './api';

const blank = { name: '', transport: 'stdio', enabled: true, command: '', args: '[]', url: '', env: '', headers: '' };

export default function MCP({ report }) {
  const [servers, setServers] = useState([]);
  const [form, setForm] = useState(null);
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  async function refresh() { setServers((await api('/mcp')).servers); }
  useEffect(() => { refresh().catch(report); }, []);
  async function action(work) {
    setBusy(true);
    try { await work(); } catch (error) { report(error); }
    finally { setBusy(false); }
  }
  function edit(server) {
    setEditing(true);
    setResult(null);
    setForm({ ...blank, ...server, args: JSON.stringify(server.args), env: '', headers: '' });
  }
  async function save(event) {
    event.preventDefault();
    await action(async () => {
      const { name, env, headers, env_keys, header_keys, ...values } = form;
      values.args = JSON.parse(values.args);
      if (env.trim()) values.env = JSON.parse(env);
      if (headers.trim()) values.headers = JSON.parse(headers);
      await api(`/mcp/${encodeURIComponent(name)}`, { method: 'PUT', body: values });
      setForm(null);
      await refresh();
      report('MCP 服务已保存');
    });
  }
  const field = (key, label, extra = {}) => <label>{label}<input value={form[key]} {...extra}
    onChange={event => setForm({ ...form, [key]: event.target.value })} /></label>;
  return <div className="settings">
    <div><p className="eyebrow">MCP</p><h2>外部工具</h2><p className="muted">保存配置后，可手动测试；启用的服务会在任务开始时连接。凭据请放在环境变量或请求头中，避免写进 URL 或参数。</p></div>
    <button className="primary self-start" disabled={busy} onClick={() => { setEditing(false); setForm({ ...blank }); setResult(null); }}>增加服务</button>
    {servers.map(server => <section key={server.name}>
      <h3>{server.name} · {server.transport} · {server.enabled ? '启用' : '停用'}</h3>
      <p className="muted">环境变量：{server.env_keys.join(', ') || '无'}；请求头：{server.header_keys.join(', ') || '无'}</p>
      <div className="field-grid">
        <button disabled={busy} onClick={() => edit(server)}>编辑</button>
        <button disabled={busy} onClick={() => action(async () => setResult({ name: server.name, ...await api(`/mcp/${encodeURIComponent(server.name)}/test`, { method: 'POST' }) }))}>测试连接与工具</button>
        <button disabled={busy} onClick={() => action(async () => { await api(`/mcp/${encodeURIComponent(server.name)}`, { method: 'DELETE' }); await refresh(); if (form?.name === server.name) setForm(null); })}>删除</button>
      </div>
    </section>)}
    {result && <section role="status"><h3>{result.name} 测试结果</h3>{result.error && <p>{result.error}</p>}
      {result.tools.length ? <ul>{result.tools.map(tool => <li key={tool.name}>{tool.name}：{tool.description}</li>)}</ul> : <p className="muted">没有发现工具</p>}
    </section>}
    {form && <form className="settings" onSubmit={save}>
      <h3>{editing ? '编辑服务' : '增加服务'}</h3>
      {field('name', '服务名', { required: true, disabled: editing, pattern: '[A-Za-z0-9_-]{1,32}', maxLength: 32 })}
      <label>传输方式<select className="backend-select" value={form.transport} onChange={event => setForm({ ...form, transport: event.target.value })}>
        <option value="stdio">stdio · 本机命令</option><option value="streamable_http">Streamable HTTP</option><option value="sse">SSE</option>
      </select></label>
      <label><input type="checkbox" checked={form.enabled} onChange={event => setForm({ ...form, enabled: event.target.checked })} />启用</label>
      {form.transport === 'stdio' ? <>{field('command', '命令', { required: true })}{field('args', '参数 · JSON 字符串数组', { required: true })}</> : field('url', '服务 URL', { required: true, type: 'url' })}
      {['env', 'headers'].map(key => <label key={key}>{key === 'env' ? '环境变量' : '请求头'} · JSON 对象
        <textarea value={form[key]} autoComplete="off" spellCheck={false} placeholder={`留空保留；{} 清空。已有键：${(form[`${key === 'env' ? 'env' : 'header'}_keys`] || []).join(', ') || '无'}`}
          onChange={event => setForm({ ...form, [key]: event.target.value })} />
      </label>)}
      <div className="field-grid"><button className="primary" disabled={busy}>{busy ? '处理中…' : '保存配置'}</button><button type="button" disabled={busy} onClick={() => setForm(null)}>取消</button></div>
    </form>}
  </div>;
}
