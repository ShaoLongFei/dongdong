import React, { useState } from 'react';
import { FolderOpen } from 'lucide-react';
import { api } from './api';

export default function FolderPicker({ value, onChange, disabled, report, onPending }) {
  const [pending, setPending] = useState(false);
  async function choose() {
    if (pending || disabled) return;
    setPending(true); onPending(true);
    try {
      const result = await api('/folders/select', { method: 'POST' });
      if (result.path) onChange(result.path);
    } catch (error) { report(error); }
    finally { setPending(false); onPending(false); }
  }
  return <div className="folder-picker">
    <button type="button" disabled={disabled || pending} onClick={choose}><FolderOpen size={15} />{pending ? '等待选择…' : value ? '更换文件夹' : '选择文件夹'}</button>
    <span className="muted text-xs" title={value || ''}>{value || '尚未选择文件夹'}</span>
  </div>;
}
