import React, { useEffect, useMemo, useState } from 'react';
import { Pause, Play, Sparkles, Square } from 'lucide-react';

function Scene({ scene }) {
  const [playing, setPlaying] = useState(true);
  const markup = useMemo(() => scene?.svg || '', [scene]);
  if (!scene) return <div className="story-empty">等待你讲下一段故事…</div>;
  return <article className="story-scene">
    <div className="story-scene-head"><span>第 {scene.index || '?'} 幕</span><span className="muted">{scene.storyText}</span><button onClick={() => setPlaying(value => !value)} aria-label={playing ? '暂停动画' : '播放动画'}>{playing ? <Pause size={14} /> : <Play size={14} />}</button></div>
    <div className={`story-paper ${playing ? 'is-playing' : 'is-paused'}`} dangerouslySetInnerHTML={{ __html: markup }} />
  </article>;
}

export default function StoryCanvas({ state, onModeChange }) {
  const [scenes, setScenes] = useState([]);
  const [error, setError] = useState('');
  useEffect(() => {
    setScenes([]); setError('');
    if (!state?.storyId) return undefined;
    let cursor = 0;
    const source = new EventSource(`/api/story/events?since=${cursor}`);
    source.onmessage = event => {
      const item = JSON.parse(event.data);
      cursor = item.id || cursor;
      if (item.type === 'scene') setScenes(previous => [...previous, { ...item.data.scene, index: previous.length + 1 }]);
      if (item.type === 'error') setError(item.data?.message || '这一幕生成失败');
    };
    source.onerror = () => source.close();
    return () => source.close();
  }, [state?.storyId]);
  const recording = state?.status === 'recording';
  return <section className="story-panel">
    <div className="story-toolbar"><div><p className="eyebrow">STORY MODE</p><h2>把你讲的故事画出来</h2><p className="muted">{recording ? '正在听你讲故事，停顿后会生成下一幕。' : '开启后，咚咚会把每段故事变成手绘动画。'}</p></div><button className={recording ? 'story-stop' : 'primary'} onClick={() => onModeChange(!recording)}>{recording ? <><Square size={14} />结束故事</> : <><Sparkles size={14} />开始讲故事</>}</button></div>
    {error && <div className="notice">{error}</div>}
    {!scenes.length ? <div className="story-empty"><Sparkles size={24} /><span>{recording ? '先讲一句，咚咚会马上开始画。' : '点击开始讲故事，或对咚咚说“开始讲故事”。'}</span></div> : <div className="story-scenes">{scenes.map(scene => <Scene key={scene.sceneId} scene={scene} />)}</div>}
  </section>;
}
