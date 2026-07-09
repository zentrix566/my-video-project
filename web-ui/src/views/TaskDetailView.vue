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

      <!-- 执行日志（在进度条下面） -->
      <n-card title="执行日志" style="margin-top: 16px;">
        <div class="log-terminal" ref="logRef">
          <div v-for="(log, idx) in task.logs" :key="idx" class="log-line" :class="log.level">
            <span style="color: #666; margin-right: 8px;">[{{ formatLogTime(log.timestamp) }}]</span>
            {{ log.message }}
          </div>
          <div v-if="task.logs.length === 0" style="color: #666; text-align: center; padding: 20px;">
            {{ isHistoryTask ? '历史任务日志不保留' : '暂无日志输出...' }}
          </div>
        </div>
        <div style="margin-top: 12px; display: flex; justify-content: flex-end; gap: 8px;" v-if="!isHistoryTask">
          <n-button size="small" @click="scrollToBottom(true)" :disabled="!autoScroll">滚动到最新</n-button>
          <n-switch v-model:value="autoScroll" size="small" />
          <span style="font-size: 12px; color: #888; line-height: 22px;">自动滚动</span>
        </div>
      </n-card>

      <!-- 任务参数（放最下面，默认折叠） -->
      <n-card title="任务配置参数" style="margin-top: 16px;" collapsible :default-collapsed="true">
        <n-alert v-if="isRecoveredTask" type="info" style="margin-bottom: 12px;">
          这是从剪映草稿目录自动恢复的历史任务，详细运行参数没有保留，点击右上角「打开草稿目录」可以直接使用生成好的视频草稿。
        </n-alert>
        <template v-else>
          <n-descriptions :column="1" bordered size="small">
            <n-descriptions-item v-for="(v, k) in displayParams" :key="k">
              <template #label>
                <span>{{ paramLabels[k]?.label || paramLabels[k] || k }}</span>
                <span v-if="paramLabels[k]?.desc" style="color: #999; font-size: 12px; margin-left: 8px; font-weight: normal;">
                  {{ paramLabels[k].desc }}
                </span>
              </template>
              <template v-if="isBooleanParam(v)">
                <n-tag :type="v ? 'success' : 'default'" size="small">{{ v ? '启用' : '未启用' }}</n-tag>
              </template>
              <template v-else-if="typeof v === 'object'">
                <pre style="margin: 0; white-space: pre-wrap; word-break: break-all; font-size: 12px; line-height: 1.5;">{{ JSON.stringify(v, null, 2) }}</pre>
              </template>
              <template v-else-if="v === '' || v === null || v === undefined">
                <span style="color: #999;">（未设置）</span>
              </template>
              <template v-else>
                <span style="word-break: break-all; line-height: 1.6;">{{ v }}</span>
              </template>
            </n-descriptions-item>
          </n-descriptions>
          <p style="margin-top: 12px; font-size: 12px; color: #999;">
            这里展示的是创建任务时使用的所有配置参数，供参考。
          </p>
        </template>
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

// 参数字段中文标签 + 说明映射
const paramLabels = {
  // 通用参数
  topic: { label: '视频主题', desc: '用户输入的视频主题内容' },
  brief: { label: '补充说明', desc: '用户指定的视频侧重点和额外要求' },
  style: { label: '视觉风格', desc: '视频画面的整体风格' },
  scenes: { label: '场景数量', desc: '视频拆分的场景段落数' },
  speaker: { label: '配音音色', desc: 'AI配音使用的声音' },
  dry_run: { label: '试算模式', desc: '仅估算费用不实际生成' },
  resume_latest: { label: '断点续跑', desc: '从上次中断位置继续执行' },
  // 代码走读参数
  project: { label: '项目路径', desc: '本地前端项目的完整目录路径' },
  dev_port: { label: '开发端口', desc: '前端开发服务器监听的端口号' },
  skip_dev_server: { label: '跳过启动服务', desc: '不自动启动npm run dev，使用已运行的服务' },
  skip_scan: { label: '跳过项目扫描', desc: '续跑时跳过项目结构扫描步骤' },
  skip_llm: { label: '跳过AI写稿', desc: '续跑时跳过讲稿生成分镜步骤' },
  skip_shots: { label: '跳过画面渲染', desc: '续跑时跳过UI截图/视频录制步骤' },
  skip_tts: { label: '跳过AI配音', desc: '续跑时跳过语音合成步骤' },
  skip_titles: { label: '跳过标题生成', desc: '不自动生成备选标题' },
  skip_image: { label: '跳过图片生成', desc: '续跑时跳过AI绘图步骤' },
  // 梗图/轮播参数
  source: { label: '素材目录', desc: '本地图片素材文件夹路径' },
  bgm: { label: '背景音乐', desc: '使用的BGM音频文件路径' },
  fit_to_bgm: { label: '适配BGM时长', desc: '视频总长度自动匹配背景音乐长度' },
  count: { label: '图片数量', desc: '选取的图片/卡片数量' },
  range: { label: '选择范围', desc: '指定选取图片的序号范围' },
  sort: { label: '排序方式', desc: '图片的排序规则（名称/时间/随机）' },
  canvas_w: { label: '画布宽度', desc: '输出视频的宽度像素' },
  canvas_h: { label: '画布高度', desc: '输出视频的高度像素' },
  fit_mode: { label: '图片适配', desc: '图片如何适配画布大小（包含/覆盖）' },
  movement: { label: '肯·伯恩斯运镜', desc: '给静态图片添加缓慢推拉摇移动画' },
  recursive: { label: '递归扫描', desc: '扫描子目录下的所有图片' },
  bgm_volume: { label: 'BGM音量', desc: '背景音乐音量大小（0-1）' },
  seconds_per_image: { label: '单图停留时长', desc: '每张图片在视频中显示的秒数' },
  seconds_per_card: { label: '单卡停留时长', desc: '每张轮播卡片显示的秒数' },
  duration: { label: '视频总时长', desc: '指定视频总长度（秒）' },
  cards_visible: { label: '同屏卡片数', desc: '轮播时一屏同时可见的卡片数量' },
  bg_color: { label: '背景颜色', desc: '视频画布背景色' },
  direction: { label: '滚动方向', desc: '卡片轮播滚动方向（左/右）' },
  bg_blur: { label: '模糊背景', desc: '用第一张图片模糊作为全屏背景' },
  card_radius: { label: '卡片圆角', desc: '卡片边角圆角大小（像素）' },
  card_gap: { label: '卡片间距', desc: '相邻卡片之间的空白距离' },
  strip_height: { label: '条带高度', desc: '卡片区域占整个画布的比例' },
  no_text: { label: '不渲染文字', desc: '不在卡片上叠加文字（截图自带UI时使用）' },
  title_size_px: { label: '标题字号', desc: '卡片标题文字大小' },
  subtitle_size_px: { label: '副标题字号', desc: '卡片副标题文字大小' },
  data: { label: '卡片数据', desc: 'JSON格式的卡片内容配置' },
  uploaded_images: { label: '上传图片', desc: '用户通过网页上传的图片文件列表' },
  // 文档/录屏讲解参数
  pdf: { label: 'PDF文档路径', desc: '讲解使用的需求文档PDF路径' },
  mp4: { label: '录屏视频路径', desc: '需要配音讲解的录屏视频文件路径' },
  no_vision: { label: '盲讲模式', desc: '不使用视觉大模型识别画面，直接生成讲解' },
  skip_parse: { label: '跳过解析', desc: '续跑时跳过PDF/视频解析抽帧步骤' },
  skip_vision: { label: '跳过视觉识别', desc: '续跑时跳过AI理解画面步骤' },
  skip_cut: { label: '跳过视频切段', desc: '续跑时跳过按时间戳切割原视频步骤' },
}

// 永远不显示的内部参数
const INTERNAL_SKIP_KEYS = new Set(['auto_confirm', 'note'])

// 过滤掉无意义的内部参数，用户能看懂的才显示
const displayParams = computed(() => {
  if (!task.value?.params) return {}
  const result = {}
  for (const [k, v] of Object.entries(task.value.params)) {
    if (INTERNAL_SKIP_KEYS.has(k)) continue
    // 跳过false的布尔开关（没启用的功能不用展示，减少干扰）
    if (typeof v === 'boolean' && v === false) continue
    result[k] = v
  }
  return result
})

const isBooleanParam = (v) => typeof v === 'boolean'

// 判断是否是从剪映目录恢复的历史任务（参数只有note标记）
const isRecoveredTask = computed(() => {
  return task.value?.params?.note && task.value.params.note.includes('自动恢复')
})

// 判断是否是历史任务（已结束且无运行日志）
const isHistoryTask = computed(() => {
  return task.value?.status && ['success', 'failed', 'cancelled'].includes(task.value.status)
    && task.value.logs.length === 0
})

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
