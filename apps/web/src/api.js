export async function api(path, options = {}) {
  const response = await fetch(`/api${path}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...options.headers },
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '参数格式不正确');
  return data;
}

export const statusText = { planning: '正在规划任务', running: '进行中', done: '已完成', stopped: '已停止', canceled: '已取消', failed: '失败', exhausted: '失败', timeout: '已超时', paused: '已暂停', error: '失败', open: '待执行', blocked: '已阻断' };
