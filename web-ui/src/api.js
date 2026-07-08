/**
 * API 客户端封装
 */
const BASE = '/api'

async function request(url, options = {}) {
  const res = await fetch(BASE + url, {
    headers: { 'Content-Type': 'application/json' },
    ...options
  })
  if (!res.ok) {
    let msg = `HTTP ${res.status}`
    try {
      const err = await res.json()
      msg = err.detail || JSON.stringify(err)
    } catch (e) {}
    throw new Error(msg)
  }
  return res.json()
}

export const api = {
  // 健康检查
  health: () => request('/health'),

  // 工作流
  listWorkflows: () => request('/workflows'),

  // 任务
  createTask: (data) => request('/tasks', {
    method: 'POST',
    body: JSON.stringify(data)
  }),
  listTasks: (limit = 50) => request(`/tasks?limit=${limit}`),
  getTask: (id, logTail = 300) => request(`/tasks/${id}?log_tail=${logTail}`),
  cancelTask: (id) => request(`/tasks/${id}/cancel`, { method: 'POST' }),
  confirmTask: (id, data) => request(`/tasks/${id}/confirm`, {
    method: 'POST',
    body: JSON.stringify(data)
  }),
  openDraftFolder: (id) => request(`/tasks/${id}/open-folder`, { method: 'POST' }),

  // 文件上传
  async uploadFile(file, onProgress) {
    const formData = new FormData()
    formData.append('file', file)
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest()
      xhr.open('POST', BASE + '/upload')
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve(JSON.parse(xhr.responseText))
        } else {
          reject(new Error(`上传失败: ${xhr.status}`))
        }
      }
      xhr.onerror = () => reject(new Error('网络错误'))
      if (onProgress) {
        xhr.upload.onprogress = (e) => {
          if (e.lengthComputable) {
            onProgress(Math.round((e.loaded / e.total) * 100))
          }
        }
      }
      xhr.send(formData)
    })
  },

  listFiles: () => request('/files')
}
