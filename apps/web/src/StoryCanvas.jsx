import React, { useEffect, useMemo, useState } from 'react';
import { BookOpen, Download, FileImage, FileText, Film, LoaderCircle, Pause, Play, RotateCcw, Sparkles, Square } from 'lucide-react';
import { api } from './api';

function prepareSvg(markup) {
  if (!markup || typeof DOMParser === 'undefined') return markup;
  try {
    const document = new DOMParser().parseFromString(markup, 'image/svg+xml');
    document.querySelectorAll('g[data-start-ms][data-duration-ms]').forEach(group => {
      const start = Number(group.getAttribute('data-start-ms'));
      const duration = Number(group.getAttribute('data-duration-ms'));
      if (Number.isFinite(start) && Number.isFinite(duration)) {
        group.style.setProperty('--story-start-ms', `${Math.max(0, start)}ms`);
        group.style.setProperty('--story-duration-ms', `${Math.max(100, duration)}ms`);
      }
    });
    return new XMLSerializer().serializeToString(document.documentElement);
  } catch {
    return markup;
  }
}

function Scene({ scene }) {
  const [playing, setPlaying] = useState(true);
  const markup = useMemo(() => prepareSvg(scene?.svg || ''), [scene]);
  if (!scene) return <div className="story-empty">等待你讲下一段故事…</div>;
  return <article className="story-scene">
    <div className="story-scene-head"><span>第 {scene.index || '?'} 幕</span><span className="muted">{scene.storyText}</span><button onClick={() => setPlaying(value => !value)} aria-label={playing ? '暂停动画' : '播放动画'}>{playing ? <Pause size={14} /> : <Play size={14} />}</button></div>
    <div className={`story-paper ${playing ? 'is-playing' : 'is-paused'}`} dangerouslySetInnerHTML={{ __html: markup }} />
  </article>;
}

export default function StoryCanvas({ state, onModeChange }) {
  const [scenes, setScenes] = useState([]);
  const [stories, setStories] = useState([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState('');
  const [selectedStoryId, setSelectedStoryId] = useState('');
  const [selectedStoryStatus, setSelectedStoryStatus] = useState('');
  const [error, setError] = useState('');
  const [exportJob, setExportJob] = useState(null);
  const [exportError, setExportError] = useState('');
  const activeStoryId = state?.status === 'recording' ? state.storyId || selectedStoryId : selectedStoryId || state?.storyId || '';

  const loadStories = async () => {
    setHistoryLoading(true);
    setHistoryError('');
    try {
      const result = await api('/stories');
      setStories(Array.isArray(result) ? result : result.stories || []);
    } catch (requestError) {
      setHistoryError(requestError.message);
    } finally {
      setHistoryLoading(false);
    }
  };

  useEffect(() => { loadStories(); }, []);

  useEffect(() => {
    const isLiveStory = Boolean(state?.storyId && activeStoryId === state.storyId);
    if (isLiveStory) {
      setScenes([]); setExportJob(null); setExportError('');
      setSelectedStoryId(state.storyId);
      setSelectedStoryStatus(state.status || '');
    }
    setError('');
    const streamStoryId = activeStoryId;
    if (!streamStoryId) return undefined;
    let cursor = 0; let source; let retry;
    const connect = () => {
      source = new EventSource(`/api/story/events?story_id=${encodeURIComponent(streamStoryId)}&since=${cursor}`);
      source.onmessage = event => {
        const item = JSON.parse(event.data); cursor = Math.max(cursor, item.id || cursor);
        if (item.type === 'scene') setScenes(previous => previous.some(scene => scene.sceneId === item.data.scene.sceneId) ? previous : [...previous, { ...item.data.scene, index: previous.length + 1 }]);
        if (item.type === 'error') setError(item.data?.message || '这一幕生成失败');
      };
      source.onerror = () => { source.close(); retry = window.setTimeout(connect, 1000); };
    };
    connect();
    return () => { source?.close(); window.clearTimeout(retry); };
  }, [activeStoryId, state?.storyId]);

  async function loadStory(storyId) {
    if (!storyId || storyId === activeStoryId) return;
    setError('');
    setHistoryError('');
    try {
      const result = await api(`/stories/${encodeURIComponent(storyId)}`);
      setSelectedStoryId(storyId);
      setSelectedStoryStatus(result.manifest?.status || 'done');
      setScenes([]);
      setExportJob(null);
      setExportError('');
      await api(`/stories/${encodeURIComponent(storyId)}/replay`, { method: 'POST' });
    } catch (requestError) {
      setHistoryError(requestError.message);
    }
  }
  const recording = state?.status === 'recording';
  const ready = !recording && ['ready', 'done'].includes(selectedStoryStatus || state?.status) && Boolean(activeStoryId) && scenes.length > 0;

  useEffect(() => {
    if (!exportJob?.jobId || !activeStoryId || ['done', 'error', 'failed', 'missing'].includes(exportJob.status)) return undefined;
    let cancelled = false;
    let timer;
    const poll = async () => {
      try {
        const result = await api(`/stories/${encodeURIComponent(activeStoryId)}/exports/${encodeURIComponent(exportJob.jobId)}`);
        if (cancelled) return;
        setExportJob(previous => ({ ...previous, ...result, status: result.status || previous.status }));
        if (!['done', 'error', 'failed', 'missing'].includes(result.status)) timer = window.setTimeout(poll, 800);
      } catch (pollError) {
        if (!cancelled) setExportJob(previous => ({ ...previous, status: 'error', error: pollError.message }));
      }
    };
    poll();
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [exportJob?.jobId, exportJob?.status, activeStoryId]);

  async function exportStory(format) {
    if (!ready || exportJob?.status === 'running') return;
    setExportError('');
    setExportJob({ format, status: 'starting' });
    try {
      const result = await api(`/stories/${encodeURIComponent(activeStoryId)}/exports`, { method: 'POST', body: { format } });
      setExportJob({ ...result, format, jobId: result.job_id || result.jobId, status: result.status || 'running' });
    } catch (requestError) {
      setExportJob(null);
      setExportError(requestError.message);
    }
  }

  async function replayStory() {
    if (!activeStoryId) return;
    setError('');
    try {
      await api(`/stories/${encodeURIComponent(activeStoryId)}/replay`, { method: 'POST' });
    } catch (replayError) { setError(replayError.message); }
  }

  const exportStatus = exportJob?.status;
  const downloadUrl = exportJob?.download_url || exportJob?.downloadUrl || exportJob?.url;
  const currentStory = stories.find(story => story.storyId === selectedStoryId);
  return <section className="story-panel">
    <div className="story-toolbar"><div><p className="eyebrow">STORY MODE</p><h2>把你讲的故事画出来</h2><p className="muted">{recording ? '正在听你讲故事，停顿后会生成下一幕。' : '开启后，咚咚会把每段故事变成手绘动画。'}</p></div><button className={recording ? 'story-stop' : 'primary'} onClick={() => onModeChange(!recording)}>{recording ? <><Square size={14} />结束故事</> : <><Sparkles size={14} />开始讲故事</>}</button></div>
    <div className="story-history" aria-label="故事历史">
      <div className="story-history-head"><span><BookOpen size={14} />最近故事</span><button className="story-history-refresh" onClick={loadStories} disabled={historyLoading} aria-label="刷新故事历史">{historyLoading ? <LoaderCircle size={13} className="spin" /> : <RotateCcw size={13} />}</button></div>
      {historyError && <p className="story-history-error">{historyError}</p>}
      {stories.length ? <div className="story-history-list">{stories.slice(0, 8).map(story => <button key={story.storyId} className={story.storyId === selectedStoryId ? 'is-selected' : ''} onClick={() => loadStory(story.storyId)}><span>{story.title || '未命名故事'}</span><small>{story.scenes?.length || story.sceneCount || 0} 幕</small></button>)}</div> : <p className="story-history-empty">还没有保存的故事</p>}
      {currentStory && <p className="story-history-current">当前：{currentStory.title || currentStory.storyId}</p>}
    </div>
    {error && <div className="notice">{error}</div>}
    <div className="story-actions" aria-label="故事操作">
      <span className="story-count">{scenes.length} 幕{recording ? ' · 生成中' : ''}</span>
      <button className="story-action" onClick={replayStory} disabled={!activeStoryId || recording} title="重新播放故事"><RotateCcw size={14} />重播</button>
      <span className="story-export-label">导出</span>
      {[['png', FileImage, 'PNG'], ['pdf', FileText, 'PDF'], ['mp4', Film, 'MP4']].map(([format, Icon, label]) => <button key={format} className="story-action" onClick={() => exportStory(format)} disabled={!ready || exportStatus === 'starting' || exportStatus === 'running'} title={`导出 ${label}`}><Icon size={14} />{label}</button>)}
    </div>
    {(exportStatus === 'starting' || exportStatus === 'running') && <div className="story-export-status"><LoaderCircle size={14} className="spin" />正在准备 {exportJob.format.toUpperCase()} 导出…</div>}
    {exportStatus === 'done' && (downloadUrl ? <div className="story-export-status success"><span>导出完成</span><a href={downloadUrl} download><Download size={14} />下载 {exportJob.format.toUpperCase()}</a></div> : <div className="story-export-status">导出完成，服务端未返回下载链接。</div>)}
    {(exportError || ['error', 'failed', 'missing'].includes(exportStatus)) && <div className="story-export-status error">导出失败：{exportError || exportJob.error || '任务不存在'}</div>}
    {!scenes.length ? <div className="story-empty"><Sparkles size={24} /><span>{recording ? '先讲一句，咚咚会马上开始画。' : '点击开始讲故事，或对咚咚说“开始讲故事”。'}</span></div> : <div className="story-scenes">{scenes.map(scene => <Scene key={scene.sceneId} scene={scene} />)}</div>}
  </section>;
}
