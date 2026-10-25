import React, { useEffect, useState } from 'react';
import { Save } from 'lucide-react';
import { api } from './api';

export default function Settings({ onSaved, report }) {
  const [form, setForm] = useState(null);
  const [saving, setSaving] = useState(false);
  useEffect(() => { api('/settings').then(setForm).catch(report); }, []);
  if (!form) return <p className="muted">正在读取设置…</p>;
  async function save(event) {
    event.preventDefault();
    setSaving(true);
    try {
      const { has_key, ...values } = form;
      const result = await api('/settings', { method: 'PUT', body: values });
      setForm({ ...form, api_key: '', has_key: has_key || Boolean(form.api_key) });
      report(result.error || '设置已保存');
      onSaved();
    } catch (error) { report(error); }
    finally { setSaving(false); }
  }
  const field = (key, label, type = 'text', extra = {}) => <label key={key}>
    {label}<input type={type} value={form[key] ?? ''} {...extra}
      onChange={e => setForm({ ...form, [key]: type === 'number' ? Number(e.target.value) : e.target.value })} />
  </label>;
  return <form className="settings" onSubmit={save}>
    <div><p className="eyebrow">PREFERENCES</p><h2>让咚咚适应你的工作方式</h2><p className="muted">模型与工作区配置保存在这台 Mac 上。</p></div>
    {field('workspace', '工作区 · 文件工具的访问范围', 'text', { required: true })}
    <label>语音编程后端<select className="backend-select" value={form.code_backend || 'cli'} onChange={e => setForm({ ...form, code_backend: e.target.value })}><option value="builtin">内置 agent · 与 Web 共享任务和模型</option><option value="cli">CLI · 使用配置中的 codex / claude</option></select></label>
    <div className="field-grid">{field('base_url', '模型 API 地址', 'url', { required: true })}{field('model', '模型名称', 'text', { required: true })}</div>
    {field('api_key', 'API Key', 'password', { autoComplete: 'new-password', placeholder: form.has_key ? '已配置 · 留空保留现有密钥' : '输入密钥，或在环境变量中配置' })}
    <div className="field-grid">{field('temperature', 'Temperature', 'number', { min: 0, max: 2, step: 0.1 })}{field('max_tokens', '最大输出 tokens', 'number', { min: 1, max: 65536 })}</div>
    <p className="muted text-xs">发送消息时，对话和工具结果会发送至所配置的模型服务。灵矽语音启用后，音频会发送至灵矽平台。</p>
    <button className="primary self-start" disabled={saving}><Save size={15} />{saving ? '保存中…' : '保存设置'}</button>
  </form>;
}
