<template>
  <div class="page-container">
    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px;">
      <h1 style="margin: 0; font-size: 28px;">任务列表</h1>
      <n-button type="primary" @click="loadTasks" :loading="loading">
        <template #icon><n-icon><svg viewBox="0 0 24 24" fill="currentColor"><path d="M17.65 6.35C16.2 4.9 14.21 4 12 4c-4.42 0-7.99 3.58-7.99 8s3.57 8 7.99 8c3.73 0 6.84-2.55 7.73-6h-2.08c-.82 2.33-3.04 4-5.65 4-3.31 0-6-2.69-6-6s2.69-6 6-6c1.66 0 3.14.69 4.22 1.78L13 11h7V4l-2.35 2.35z"/></svg></n-icon></template>
        刷新
      </n-button>
    </div>

    <n-card v-if="tasks.length === 0" style="text-align: center; padding: 60px 20px;">
      <n-empty description="暂无任务，去首页创建一个吧">
        <n-button type="primary" @click="$router.push('/')">选择工作流</n-button>
      </n-empty>
    </n-card>

    <n-list v-else bordered>
      <n-list-item v-for="task in tasks" :key="task.task_id" style="cursor: pointer;" @click="goDetail(task.task_id)">
        <n-thing>
          <template #header>
            <div style="display: flex; align-items: center; justify-content: space-between;">
              <span>{{ task.name }}</span>
              <n-tag :type="statusType(task.status)" size="small" round>{{ statusLabel(task.status) }}</n-tag>
            </div>
          </template>
          <template #description>
            <n-space :size="16" style="font-size: 13px; color: #666;">
              <span><n-icon size="14" style="vertical-align: -2px; margin-right: 4px;"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm4.2 14.2L11 13V7h1.5v5.2l4.5 2.7-.8 1.3z"/></svg></n-icon> {{ task.workflow_label }}</span>
              <span><n-icon size="14" style="vertical-align: -2px; margin-right: 4px;"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M11.99 2C6.47 2 2 6.48 2 12s4.47 10 9.99 10C17.52 22 22 17.52 22 12S17.52 2 11.99 2zM12 20c-4.42 0-8-3.58-8-8s3.58-8 8-8 8 3.58 8 8-3.58 8-8 8zm.5-13H11v6l5.25 3.15.75-1.23-4.5-2.67z"/></svg></n-icon> {{ formatTime(task.created_at) }}</span>
            </n-space>
          </template>
          <template v-if="task.status === 'running' || task.status === 'waiting_confirm'">
            <n-progress :percentage="task.progress" :height="6" :show-indicator="false" style="margin-top: 8px;" />
            <div v-if="task.current_step" style="font-size: 12px; color: #888; margin-top: 4px;">{{ task.current_step }}</div>
          </template>
        </n-thing>
      </n-list-item>
    </n-list>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'

const router = useRouter()
const tasks = ref([])
const loading = ref(false)
let pollTimer = null

const statusType = (s) => ({
  pending: 'default',
  running: 'info',
  waiting_confirm: 'warning',
  success: 'success',
  failed: 'error',
  cancelled: 'default'
}[s] || 'default')

const statusLabel = (s) => ({
  pending: '等待中',
  running: '执行中',
  waiting_confirm: '等待确认',
  success: '成功',
  failed: '失败',
  cancelled: '已取消'
}[s] || s)

const formatTime = (iso) => {
  if (!iso) return ''
  const d = new Date(iso)
  return d.toLocaleString('zh-CN')
}

const loadTasks = async () => {
  loading.value = true
  try {
    const res = await api.listTasks(100)
    tasks.value = res.tasks
  } catch (e) {
    console.error(e)
  } finally {
    loading.value = false
  }
}

const goDetail = (id) => router.push(`/tasks/${id}`)

onMounted(() => {
  loadTasks()
  pollTimer = setInterval(loadTasks, 3000)
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})
</script>
