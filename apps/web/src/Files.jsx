import React, { useEffect, useMemo, useState } from 'react';
import { ChevronLeft, ChevronRight, FileText, Folder, RefreshCw } from 'lucide-react';
import { api } from './api';

function sizeText(size) {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function parentPath(path) {
  if (!path || path === '.') return '.';
  const parts = path.split('/').filter(Boolean);
  parts.pop();
  return parts.length ? parts.join('/') : '.';
}

export default function Files({ projectId, projects = [], currentProject, report }) {
  const [selectedProject, setSelectedProject] = useState(projectId || currentProject || '');
  const [path, setPath] = useState('.');
  const [entries, setEntries] = useState([]);
  const [preview, setPreview] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    setSelectedProject(projectId || currentProject || '');
    setPath('.');
    setPreview(null);
  }, [projectId, currentProject]);

  const project = useMemo(() => projects.find(item => item.id === selectedProject), [projects, selectedProject]);

  async function loadDirectory(nextPath = path) {
    if (!selectedProject) return;
    setLoading(true);
    setError('');
    setPreview(null);
    try {
      const data = await api(`/projects/${encodeURIComponent(selectedProject)}/files?path=${encodeURIComponent(nextPath)}`);
      setEntries(data.files || []);
      setPath(data.path || nextPath);
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : '无法读取目录';
      setError(message);
      report?.(reason);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    setEntries([]);
    setPreview(null);
    if (selectedProject) loadDirectory('.');
  }, [selectedProject]);

  async function openEntry(entry) {
    if (entry.type === 'dir') {
      await loadDirectory(entry.relative_path);
      return;
    }
    setLoading(true);
    setError('');
    try {
      const data = await api(`/projects/${encodeURIComponent(selectedProject)}/files/content?path=${encodeURIComponent(entry.relative_path)}`);
      setPreview({ ...data, name: entry.name });
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : '无法读取文件';
      setError(message);
      setPreview({ name: entry.name, error: message });
      report?.(reason);
    } finally {
      setLoading(false);
    }
  }

  return <section className="files-panel">
    <div className="files-heading">
      <div><p className="eyebrow">PROJECT FILES</p><h2>文件</h2><p className="muted">浏览项目目录并预览 UTF-8 文本文件。</p></div>
      <label className="files-project-select">项目<select aria-label="选择文件项目" value={selectedProject} onChange={event => setSelectedProject(event.target.value)}>
        <option value="">请选择项目</option>
        {projects.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
      </select></label>
    </div>
    {!selectedProject ? <div className="files-empty"><Folder size={22} /><p>当前没有可浏览的项目。</p><span className="muted">默认工作区未开放文件浏览，请先选择一个项目。</span></div> : <>
      <div className="files-toolbar">
        <button disabled={path === '.' || loading} onClick={() => loadDirectory(parentPath(path))}><ChevronLeft size={15} />返回上级</button>
        <span className="muted files-path" title={project?.path || path}>{project?.name || '项目'} / {path === '.' ? '根目录' : path}</span>
        <button className="files-refresh" disabled={loading} onClick={() => loadDirectory()} title="刷新目录" aria-label="刷新目录"><RefreshCw size={14} /></button>
      </div>
      {error && <div className="files-error" role="alert">{error}</div>}
      <div className="files-browser">
        <div className="files-list" aria-label="项目文件列表">
          {loading && !entries.length ? <p className="muted">读取中…</p> : !entries.length ? <p className="muted">此目录为空。</p> : entries.map(entry => <button className="file-row" key={entry.relative_path} onClick={() => openEntry(entry)}>
            {entry.type === 'dir' ? <Folder size={17} /> : <FileText size={17} />}<span>{entry.name}</span><small className="muted">{entry.type === 'file' ? sizeText(entry.size) : '目录'}</small><ChevronRight size={14} />
          </button>)}
        </div>
        <div className="file-preview">
          {!preview ? <p className="muted">点击文件查看内容。</p> : preview.error ? <div className="files-empty"><FileText size={20} /><p>{preview.name}</p><span className="muted">{preview.error}</span></div> : <><div className="file-preview-head"><strong>{preview.name}</strong><span className="muted">{preview.mime || 'text/plain'} · {sizeText(preview.size || 0)}</span></div><pre>{preview.text}</pre></>}
        </div>
      </div>
    </>}
  </section>;
}
