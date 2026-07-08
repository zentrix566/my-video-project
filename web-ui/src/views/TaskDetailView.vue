<template>
  <div class="page-container">
    <n-page-header @back="$router.push('/tasks')">
      <template #title>
        <span v-if="task">{{ task.name }}</span>
        <span v-else>任务详情</span>
      </template>
      <template #subtitle v-if="task">
        <n-tag :type="statusType(task.status)" size="small" round style="margin-right: 8px;">{{ statusLabel(task.status) }}</n-tag>
        {{ task.workflow_label }} · 创建于 {{ formatTime(task.created_at) }}
      </template>
      <template #extra v-if="task">
        <n-space>
          <n-button v-if="canResume(task)" @click="showResumeModal = true" type="warning" ghost>
            <template #icon><n-icon><svg viewBox="0 0 24 24" fill="currentColor"><path d="M17.65 6.35C16.2 4.9 14.21 4 12 4c-4.42 0-7.99 3.58-7.99 8s3.57 8 7.99 8c3.73 0 6.84-2.55 7.73-6h-2.08c-.82 2.33-3.04 4-5.65 4-3.31 0-6-2.69-6-6s2.69-6 6-6c1.66 0 3.14.69 4.22 1.78L13 11h7V4l-2.35 2.35z"/></svg></n-icon></template>
            断点续跑
          </n-button>
          <n-button v-if="task.status === 'running' || task.status === 'waiting_confirm'" @click="cancelTask" type="error" ghost>取消任务</n-button>
          <n-button v-if="task.draft_path" type="primary" @click="openFolder">
            <template #icon><n-icon><svg viewBox="0 0 24 24" fill="currentColor"><path d="M10 4H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2h-8l-2-2z"/></svg></n-icon></template>
            打开草稿目录
          </n-button>
        </n-space>
      </template>
    </n-page-header>

    <!-- 断点续跑弹窗 -->
    <n-modal v-model:show="showResumeModal" preset="card" title="断点续跑" style="max-width: 500px;">
      <n-alert type="warning" style="margin-bottom: 16px;">
        选择需要跳过的已完成步骤，程序将从上次失败的位置继续执行，避免重复消耗AI配额。
      </n-alert>
      <n-form label-placement="left" label-width="120">
        <n-form-item v-for="step in resumeSteps" :key="step.key" :label="step.label">
          <n-switch v-model:value="resumeSkip[step.key]" />
          <span style="margin-left: 8px; font-size: 13px; color: #999;">{{ step.desc }}</span>
        </n-form-item>
      </n-form>
      <template #footer>
        <div style="display: flex; justify-content: flex-end; gap: 8px;">
          <n-button @click="showResumeModal = false">取消</n-button>
          <n-button type="warning" @click="doResume">
            <template #icon><n-icon><svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg></n-icon></template>
            开始续跑
          </n-button>
        </div>
      </template>
    </n-modal>

    <div v-if="!task" style="text-align: center; padding: 80px;">
      <n-spin size="large" />
      <p style="margin-top: 16px; color: #999;">加载中...</p>
    </div>

    <template v-else>
      <!-- 进度条 -->
      <n-card style="margin-top: 16px;">
        <div style="margin-bottom: 8px; display: flex; justify-content: space-between; align-items: center;">
          <span style="font-weight: 500;">执行进度</span>
          <span style="color: #666;">{{ Math.round(task.progress) }}% · {{ task.current_step || '准备中' }}</span>
        </div>
        <n-progress :percentage="task.progress" :height="10" :status="task.status==='failed'?'error':task.status==='success'?'success':'default'" />

        <!-- 成功提示 -->
        <n-result v-if="task.status === 'success'" status="success" title="生成完成！" style="padding: 20px 0 0;">
          <template #description>
            <p>剪映草稿已生成，请点击右上角「打开草稿目录」，然后在剪映专业版中打开草稿即可编辑。</p>
            <p v-if="task.draft_path" style="word-break: break-all; color: #888; font-size: 13px; font-family: monospace;">{{ task.draft_path }}</p>
          </template>
        </n-result>

        <!-- 失败提示 -->
        <n-result v-if="task.status === 'failed'" status="error" title="任务失败" style="padding: 20px 0 0;">
          <template #description>
            <p style="color: #d03050;">{{ task.error }}</p>
            <p style="color: #999; font-size: 13px;">请查看下方日志排查问题</p>
          </template>
        </n-result>
      </n-card>

      <!-- 参数卡片 -->
      <n-card title="任务参数" style="margin-top: 16px;" collapsible :default-collapsed="true">
        <n-descriptions :column="2" bordered size="small">
          <n-descriptions-item v-for="(v, k) in task.params" :key="k" :label="k">
            <template v-if="typeof v === 'object'">{{ JSON.stringify(v) }}</template>
            <template v-else>{{ v }}</template>
          </n-descriptions-item>
        </n-descriptions>
      </n-card>

      <!-- 日志终端 -->
      <n-card title="执行日志" style="margin-top: 16px;">
        <div class="log-terminal" ref="logRef">
          <div v-for="(log, idx) in task.logs" :key="idx" class="log-line" :class="log.level">
            <span style="color: #666; margin-right: 8px;">[{{ formatLogTime(log.timestamp) }}]</span>
            {{ log.message }}
          </div>
          <div v-if="task.logs.length === 0" style="color: #666; text-align: center; padding: 20px;">暂无日志输出...</div>
        </div>
        <div style="margin-top: 12px; display: flex; justify-content: flex-end; gap: 8px;">
          <n-button size="small" @click="scrollToBottom(true)" :disabled="!autoScroll">滚动到最新</n-button>
          <n-switch v-model:value="autoScroll" size="small" />
          <span style="font-size: 12px; color: #888; line-height: 22px;">自动滚动</span>
        </div>
      </n-card>
    </template>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useMessage } from 'naive-ui'
import { api } from '../api'

const route = useRoute()
const router = useRouter()
const message = useMessage()

const taskId = route.params.id
const task = ref(null)
const autoScroll = ref(true)
const logRef = ref(null)
const showResumeModal = ref(false)
const resumeSkip = reactive({})
let pollTimer = null

// 各工作流断点步骤配置
const RESUME_STEPS = {
  topic: [
    { key: 'skip_titles', label: '跳过标题生成', desc: '已生成10条B站标题' },
    { key: 'skip_llm', label: '跳过LLM写稿', desc: '旁白稿+场景拆分已完成' },
    { key: 'skip_image', label: '跳过AI生图', desc: '所有场景图片已生成' },
    { key: 'skip_tts', label: '跳过AI配音', desc: 'TTS音频已生成' },
  ],
  code_walk: [
    { key: 'skip_scan', label: '跳过项目扫描', desc: '项目结构和关键文件已扫描' },
    { key: 'skip_llm', label: '跳过LLM写稿', desc: '讲稿和分镜已生成' },
    { key: 'skip_shots', label: '跳过截图渲染', desc: 'UI截图/代码高亮图已渲染' },
    { key: 'skip_tts', label: '跳过AI配音', desc: 'TTS音频已生成' },
  ],
  doc_video: [
    { key: 'skip_parse', label: '跳过解析抽帧', desc: 'PDF文本+视频帧已抽取' },
    { key: 'skip_vision', label: '跳过视觉理解', desc: '帧画面已描述完成' },
    { key: 'skip_llm', label: '跳过LLM写稿', desc: '讲稿+时间戳已生成' },
    { key: 'skip_cut', label: '跳过视频切段', desc: '视频已按时间戳切割' },
    { key: 'skip_tts', label: '跳过AI配音', desc: 'TTS音频已生成' },
  ],
  narration: [
    { key: 'skip_parse', label: '跳过解析抽帧', desc: '视频帧已抽取' },
    { key: 'skip_vision', label: '跳过视觉理解', desc: '帧画面已描述完成' },
    { key: 'skip_llm', label: '跳过LLM写稿', desc: '讲稿+时间戳已生成' },
    { key: 'skip_cut', label: '跳过视频切段', desc: '视频已按时间戳切割' },
    { key: 'skip_tts', label: '跳过AI配音', desc: 'TTS音频已生成' },
  ],
}

const resumeSteps = computed(() => {
  if (!task.value) return []
  return RESUME_STEPS[task.value.workflow] || []
})

const canResume = (t) => {
  if (!t) return false
  // 只有失败/成功/取消的AI任务可以续跑（梗图/轮播没必要）
  if (!['failed', 'success', 'cancelled'].includes(t.status)) return false
  return !!RESUME_STEPS[t.workflow]
}

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
  return new Date(iso).toLocaleString('zh-CN')
}

const formatLogTime = (iso) => {
  const d = new Date(iso)
  return d.toLocaleTimeString('zh-CN', { hour12: false })
}

const scrollToBottom = (force = false) => {
  nextTick(() => {
    if (logRef.value && (autoScroll.value || force)) {
      logRef.value.scrollTop = logRef.value.scrollHeight
    }
  })
}

const loadTask = async () => {
  try {
    task.value = await api.getTask(taskId, 500)
    scrollToBottom()
    // 任务结束后停止轮询
    if (['success', 'failed', 'cancelled'].includes(task.value.status)) {
      if (pollTimer) {
        clearInterval(pollTimer)
        pollTimer = null
      }
    }
  } catch (e) {
    message.error(`加载任务失败: ${e.message}`)
  }
}

const cancelTask = async () => {
  try {
    await api.cancelTask(taskId)
    message.success('已请求取消')
    loadTask()
  } catch (e) {
    message.error(`取消失败: ${e.message}`)
  }
}

const openFolder = async () => {
  try {
    await api.openDraftFolder(taskId)
    message.success('已打开目录')
  } catch (e) {
    message.error(`打开失败: ${e.message}`)
  }
}

const doResume = async () => {
  if (!task.value) return
  try {
    // 构造续跑参数：原参数 + resume_latest + 选中的skip
    const newParams = { ...task.value.params, resume_latest: true }
    Object.keys(resumeSkip).forEach(k => {
      if (resumeSkip[k]) newParams[k] = true
    })
    const result = await api.createTask({
      workflow: task.value.workflow,
      name: `${task.value.name} - 续跑`,
      params: newParams
    })
    message.success('续跑任务已创建')
    showResumeModal.value = false
    router.push(`/tasks/${result.task_id}`)
  } catch (e) {
    message.error(`续跑失败: ${e.message}`)
  }
}

watch(() => showResumeModal.value, (show) => {
  // 打开弹窗时重置skip选项
  if (show) {
    Object.keys(resumeSkip).forEach(k => resumeSkip[k] = false)
  }
})

watch(() => task.value?.status, (s) => {
  if (s === 'success') message.success('任务完成！')
  if (s === 'failed') message.error('任务失败，请查看日志')
})

onMounted(() => {
  loadTask()
  pollTimer = setInterval(loadTask, 1500)
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})
</script>
