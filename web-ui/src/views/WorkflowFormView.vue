<template>
  <div class="page-container">
    <n-page-header @back="$router.push('/')">
      <template #title>{{ workflow?.label || '加载中...' }}</template>
      <template #subtitle v-if="workflow">{{ workflow.description }}</template>
    </n-page-header>

    <n-card style="margin-top: 20px;" v-if="workflow">
      <n-form
        ref="formRef"
        :model="params"
        :rules="rules"
        label-placement="left"
        label-width="140"
        require-mark-placement="right-hanging"
      >
        <n-form-item label="任务名称" path="name">
          <n-input v-model:value="params.name" placeholder="留空自动生成" clearable />
        </n-form-item>

        <n-divider title-placement="left" style="margin: 24px 0 16px;">输入素材</n-divider>

        <!-- ========== topic 主题AI生成片 ========== -->
        <template v-if="workflowId === 'topic'">
          <n-form-item label="主题" path="topic" rule-trigger="input">
            <n-input v-model:value="params.topic" placeholder="例如：南明李定国、苏轼被贬黄州" />
          </n-form-item>
          <n-form-item label="侧重点说明">
            <n-input
              v-model:value="params.brief"
              type="textarea"
              placeholder="可选：补充说明你想重点讲解的方面"
              :autosize="{ minRows: 2 }"
            />
          </n-form-item>
          <n-form-item label="视觉风格">
            <n-radio-group v-model:value="params.style" type="button">
              <n-radio-button value="epic">史诗历史（横屏）</n-radio-button>
              <n-radio-button value="documentary">纪录片（横屏）</n-radio-button>
              <n-radio-button value="shorts">竖屏短视频</n-radio-button>
            </n-radio-group>
          </n-form-item>
          <n-form-item label="场景数量">
            <n-input-number v-model:value="params.scenes" :min="3" :max="20" />
            <span style="margin-left: 12px; font-size: 12px; color: #999;">场景越多视频越长，生图费用越高</span>
          </n-form-item>
        </template>

        <!-- ========== meme 梗图/图集 ========== -->
        <template v-if="workflowId === 'meme'">
          <n-tabs v-model:value="memeInputMode" type="line" style="margin-bottom: 16px;">
            <n-tab-pane name="upload" tab="📤 上传图片">
              <n-form-item label="选择图片">
                <n-upload
                  :custom-request="handleUploadImages"
                  :file-list="uploadedImageList"
                  accept="image/*"
                  multiple
                  list-type="image-card"
                  @remove="handleRemoveImage"
                >
                  点击或拖拽上传图片
                </n-upload>
              </n-form-item>
            </n-tab-pane>
            <n-tab-pane name="local" tab="📁 本地目录">
              <n-form-item label="图片目录路径">
                <n-input v-model:value="params.source" placeholder="本地图片文件夹完整路径，例如 C:/photos/memes" />
              </n-form-item>
            </n-tab-pane>
          </n-tabs>

          <n-form-item label="BGM音频">
            <n-upload :custom-request="handleUploadBgm" :show-file-list="false" accept="audio/*">
              <n-button>上传BGM文件</n-button>
            </n-upload>
            <div v-if="params.bgm" style="margin-top: 8px; color: #18a058; font-size: 13px;">✓ {{ params.bgm }}</div>
          </n-form-item>

          <n-divider title-placement="left" style="margin: 24px 0 16px;">视频参数</n-divider>

          <n-form-item label="时长适配BGM">
            <n-switch v-model:value="params.fit_to_bgm" />
            <span style="margin-left: 8px; font-size: 13px; color: #999;">开启后每张图时长=BGM时长/图片数</span>
          </n-form-item>
          <n-form-item label="每张停留秒数" v-if="!params.fit_to_bgm">
            <n-input-number v-model:value="params.seconds_per_image" :min="1" :max="15" :step="0.5" />
          </n-form-item>
          <n-form-item label="方屏边长">
            <n-input-number v-model:value="params.canvas_w" :min="480" :max="2160" :step="20" />
            <span style="margin-left: 8px; font-size: 13px; color: #999;">梗图默认1080x1080方屏</span>
          </n-form-item>
          <n-form-item label="图片适配模式">
            <n-radio-group v-model:value="params.fit_mode" type="button">
              <n-radio-button value="contain">完整显示（推荐，带模糊背景）</n-radio-button>
              <n-radio-button value="cover">铺满裁切</n-radio-button>
            </n-radio-group>
          </n-form-item>
          <n-form-item label="开启运镜">
            <n-switch v-model:value="params.movement" />
            <span style="margin-left: 8px; font-size: 13px; color: #999;">Ken Burns 推拉平移效果</span>
          </n-form-item>
          <n-form-item label="排序方式">
            <n-radio-group v-model:value="params.sort" type="button">
              <n-radio-button value="name">按名称</n-radio-button>
              <n-radio-button value="newest">最新修改</n-radio-button>
              <n-radio-button value="random">随机</n-radio-button>
            </n-radio-group>
          </n-form-item>
          <n-form-item label="最多图片数">
            <n-input-number v-model:value="params.count" :min="1" :max="200" />
          </n-form-item>
          <n-form-item label="递归扫描子目录">
            <n-switch v-model:value="params.recursive" />
          </n-form-item>
          <n-form-item label="BGM音量">
            <n-slider v-model:value="params.bgm_volume" :min="0" :max="1" :step="0.05" style="max-width: 300px;" />
          </n-form-item>
        </template>

        <!-- ========== carousel 卡片轮播 ========== -->
        <template v-if="workflowId === 'carousel'">
          <n-tabs v-model:value="carouselMode" type="line" style="margin-bottom: 16px;">
            <n-tab-pane name="upload" tab="📤 上传卡片图片">
              <n-form-item label="选择卡片图片">
                <n-upload
                  :custom-request="handleUploadImages"
                  :file-list="uploadedImageList"
                  accept="image/*"
                  multiple
                  list-type="image-card"
                  @remove="handleRemoveImage"
                >
                  点击或拖拽上传卡片截图
                </n-upload>
              </n-form-item>
            </n-tab-pane>
            <n-tab-pane name="local" tab="📁 本地图片目录">
              <n-form-item label="卡片目录路径">
                <n-input v-model:value="params.source" placeholder="卡片截图文件夹完整路径" />
              </n-form-item>
            </n-tab-pane>
            <n-tab-pane name="json" tab="📝 JSON数据模式">
              <n-alert type="info" style="margin-bottom: 12px;">
                自动渲染白底圆角卡片：图片+标题+副标题+星级+评论。支持flag角标。
              </n-alert>
              <n-form-item label="卡片JSON数据">
                <n-input
                  v-model:value="cardsJsonText"
                  type="textarea"
                  placeholder='{"bg_color":"#18181c","bgm":"path/to/bgm.mp3","cards":[{"image":"path.jpg","title":"名称","subtitle":"副标题","stars":4.5,"comment":"评论内容","flag":"path/to/badge.png"}]}'
                  :autosize="{ minRows: 8 }"
                />
              </n-form-item>
            </n-tab-pane>
          </n-tabs>

          <n-form-item label="BGM音频">
            <n-upload :custom-request="handleUploadBgm" :show-file-list="false" accept="audio/*">
              <n-button>上传BGM文件</n-button>
            </n-upload>
            <div v-if="params.bgm" style="margin-top: 8px; color: #18a058; font-size: 13px;">✓ {{ params.bgm }}</div>
          </n-form-item>

          <n-divider title-placement="left" style="margin: 24px 0 16px;">画面与动画</n-divider>

          <n-form-item label="时长适配BGM">
            <n-switch v-model:value="params.fit_to_bgm" />
          </n-form-item>
          <n-form-item label="每张停留秒数" v-if="!params.fit_to_bgm">
            <n-input-number v-model:value="params.seconds_per_card" :min="0.5" :max="10" :step="0.5" />
          </n-form-item>
          <n-form-item label="固定总时长（秒）">
            <n-input-number v-model:value="params.duration" :min="5" :max="300" :step="5" placeholder="留空按每张秒数计算" clearable />
          </n-form-item>
          <n-form-item label="画布尺寸">
            <n-input-group style="max-width: 400px;">
              <n-input-number v-model:value="params.canvas_w" :min="480" :max="3840" />
              <n-input-group-text>×</n-input-group-text>
              <n-input-number v-model:value="params.canvas_h" :min="480" :max="3840" />
            </n-input-group>
            <n-button-group style="margin-left: 12px;">
              <n-button size="small" @click="params.canvas_w=1920;params.canvas_h=1080">横屏16:9</n-button>
              <n-button size="small" @click="params.canvas_w=1080;params.canvas_h=1920">竖屏9:16</n-button>
              <n-button size="small" @click="params.canvas_w=1080;params.canvas_h=1080">方屏1:1</n-button>
            </n-button-group>
          </n-form-item>
          <n-form-item label="一屏可见卡片数">
            <n-slider v-model:value="params.cards_visible" :min="1" :max="6" :step="0.5" style="max-width: 400px;" />
          </n-form-item>
          <n-form-item label="卡片条带高度占比">
            <n-slider v-model:value="params.strip_height" :min="0.4" :max="1" :step="0.05" style="max-width: 400px;" />
          </n-form-item>
          <n-form-item label="卡片圆角">
            <n-input-number v-model:value="params.card_radius" :min="0" :max="48" />
          </n-form-item>
          <n-form-item label="卡片间距">
            <n-input-number v-model:value="params.card_gap" :min="0" :max="80" />
          </n-form-item>
          <n-form-item label="滚动方向">
            <n-radio-group v-model:value="params.direction" type="button">
              <n-radio-button value="left">⬅️ 向左滚动</n-radio-button>
              <n-radio-button value="right">➡️ 向右滚动</n-radio-button>
            </n-radio-group>
          </n-form-item>
          <n-form-item label="背景颜色">
            <n-color-picker v-model:value="params.bg_color" :show-alpha="false" />
          </n-form-item>
          <n-form-item label="首张模糊背景">
            <n-switch v-model:value="params.bg_blur" />
            <span style="margin-left: 8px; font-size: 13px; color: #999;">用首张图片模糊作为背景</span>
          </n-form-item>
          <n-form-item label="不渲染文字到卡片" v-if="carouselMode === 'json'">
            <n-switch v-model:value="params.no_text" />
            <span style="margin-left: 8px; font-size: 13px; color: #999;">图片自带UI时勾选</span>
          </n-form-item>
          <n-form-item label="标题字号" v-if="carouselMode === 'json' && !params.no_text">
            <n-input-number v-model:value="params.title_size_px" :min="12" :max="80" />
          </n-form-item>
          <n-form-item label="副标题字号" v-if="carouselMode === 'json' && !params.no_text">
            <n-input-number v-model:value="params.subtitle_size_px" :min="10" :max="60" />
          </n-form-item>
          <n-form-item label="BGM音量">
            <n-slider v-model:value="params.bgm_volume" :min="0" :max="1" :step="0.05" style="max-width: 300px;" />
          </n-form-item>
          <n-form-item label="最多图片数" v-if="carouselMode !== 'json'">
            <n-input-number v-model:value="params.count" :min="1" :max="200" />
          </n-form-item>
          <n-form-item label="排序方式" v-if="carouselMode !== 'json'">
            <n-radio-group v-model:value="params.sort" type="button">
              <n-radio-button value="name">按名称</n-radio-button>
              <n-radio-button value="newest">最新修改</n-radio-button>
              <n-radio-button value="random">随机</n-radio-button>
            </n-radio-group>
          </n-form-item>
          <n-form-item label="递归扫描子目录" v-if="carouselMode === 'local'">
            <n-switch v-model:value="params.recursive" />
          </n-form-item>
        </template>

        <!-- ========== code_walk 代码走读 ========== -->
        <template v-if="workflowId === 'code_walk'">
          <n-form-item label="项目本地路径" path="project">
            <n-input v-model:value="params.project" placeholder="本地Vue/React项目完整路径，例如 E:/github/my-project" />
          </n-form-item>
          <n-form-item label="讲解侧重点">
            <n-input v-model:value="params.brief" type="textarea" placeholder="可选：你想重点讲解的内容" :autosize="{ minRows: 2 }" />
          </n-form-item>

          <n-divider title-placement="left" style="margin: 24px 0 16px;">参数设置</n-divider>

          <n-form-item label="场景数量">
            <n-input-number v-model:value="params.scenes" :min="4" :max="12" />
            <span style="margin-left: 12px; font-size: 12px; color: #999;">建议6-8段</span>
          </n-form-item>
          <n-form-item label="Dev端口">
            <n-input-number v-model:value="params.dev_port" :min="1024" :max="65535" />
          </n-form-item>
          <n-form-item label="跳过启动Dev Server">
            <n-switch v-model:value="params.skip_dev_server" />
            <span style="margin-left: 8px; font-size: 13px; color: #999;">项目已运行时勾选</span>
          </n-form-item>
        </template>

        <!-- ========== doc_video 需求讲解 ========== -->
        <template v-if="workflowId === 'doc_video'">
          <n-form-item label="PDF文档" path="pdf">
            <n-upload :custom-request="(opts) => handleUploadFile(opts, 'pdf')" :show-file-list="false" accept=".pdf">
              <n-button>上传PDF文档</n-button>
            </n-upload>
            <div v-if="params.pdf" style="margin-top: 8px; color: #18a058; font-size: 13px;">✓ {{ params.pdf }}</div>
          </n-form-item>
          <n-form-item label="录屏视频" path="mp4">
            <n-upload :custom-request="(opts) => handleUploadFile(opts, 'mp4')" :show-file-list="false" accept="video/*">
              <n-button>上传录屏视频</n-button>
            </n-upload>
            <div v-if="params.mp4" style="margin-top: 8px; color: #18a058; font-size: 13px;">✓ {{ params.mp4 }}</div>
          </n-form-item>
          <n-form-item label="讲解侧重点">
            <n-input v-model:value="params.brief" type="textarea" placeholder="可选：补充说明重点讲解内容" :autosize="{ minRows: 2 }" />
          </n-form-item>

          <n-divider title-placement="left" style="margin: 24px 0 16px;">参数设置</n-divider>

          <n-form-item label="场景数量">
            <n-input-number v-model:value="params.scenes" :min="3" :max="20" />
          </n-form-item>
          <n-form-item label="跳过视觉模型">
            <n-switch v-model:value="params.no_vision" />
            <span style="margin-left: 8px; font-size: 13px; color: #f56c6c;">⚠️ 盲讲模式：费用低但可能不准确</span>
          </n-form-item>
        </template>

        <!-- ========== narration 录屏讲解 ========== -->
        <template v-if="workflowId === 'narration'">
          <n-form-item label="录屏视频" path="mp4">
            <n-upload :custom-request="(opts) => handleUploadFile(opts, 'mp4')" :show-file-list="false" accept="video/*">
              <n-button>上传录屏视频</n-button>
            </n-upload>
            <div v-if="params.mp4" style="margin-top: 8px; color: #18a058; font-size: 13px;">✓ {{ params.mp4 }}</div>
          </n-form-item>
          <n-form-item label="背景提示">
            <n-input v-model:value="params.brief" type="textarea" placeholder="可选：视频内容背景介绍，帮助AI理解画面" :autosize="{ minRows: 2 }" />
          </n-form-item>

          <n-divider title-placement="left" style="margin: 24px 0 16px;">参数设置</n-divider>

          <n-form-item label="场景数量">
            <n-input-number v-model:value="params.scenes" :min="3" :max="20" />
          </n-form-item>
          <n-form-item label="跳过视觉模型">
            <n-switch v-model:value="params.no_vision" />
            <span style="margin-left: 8px; font-size: 13px; color: #f56c6c;">⚠️ 盲讲模式：费用低但可能不准确</span>
          </n-form-item>
        </template>

        <n-divider />

        <div style="display: flex; gap: 12px; justify-content: flex-end;">
          <n-button @click="$router.push('/')">取消</n-button>
          <n-button type="primary" size="large" :loading="submitting" @click="submitForm">
            🚀 开始生成
          </n-button>
        </div>
      </n-form>
    </n-card>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useMessage } from 'naive-ui'
import { api } from '../api'

const route = useRoute()
const router = useRouter()
const message = useMessage()

const workflowId = computed(() => route.params.id)
const workflow = ref(null)
const submitting = ref(false)
const formRef = ref(null)
const carouselMode = ref('upload')
const memeInputMode = ref('upload')
const cardsJsonText = ref('')
const uploadedImageList = ref([])
const uploadedImagePaths = ref([])

// 默认参数
const defaultParams = {
  name: '',
  auto_confirm: true,
  // topic
  topic: '',
  brief: '',
  style: 'epic',
  scenes: 8,
  // meme
  source: '',
  bgm: '',
  fit_to_bgm: true,
  sort: 'name',
  canvas_w: 1080,
  canvas_h: 1080,
  fit_mode: 'contain',
  movement: false,
  count: 30,
  recursive: false,
  bgm_volume: 0.8,
  seconds_per_image: 3.0,
  // carousel
  seconds_per_card: 3.0,
  duration: null,
  cards_visible: 3.5,
  bg_color: '#18181c',
  direction: 'left',
  bg_blur: false,
  card_radius: 18,
  card_gap: 30,
  strip_height: 0.85,
  no_text: false,
  title_size_px: 42,
  subtitle_size_px: 26,
  // code walk
  project: '',
  dev_port: 5173,
  skip_dev_server: false,
  // doc video / narration
  pdf: '',
  mp4: '',
  no_vision: false,
}

const params = reactive({ ...defaultParams })

const rules = {
  topic: { required: true, message: '请输入主题', trigger: 'blur' },
  project: { required: true, message: '请填写项目路径', trigger: 'blur' },
}

onMounted(async () => {
  const wfs = await api.listWorkflows()
  workflow.value = wfs.find(w => w.id === workflowId.value)
  if (!workflow.value) {
    message.error('工作流不存在')
    router.push('/')
  }
})

// 上传多张图片
const handleUploadImages = async ({ file, onProgress, onFinish, onError }) => {
  try {
    const result = await api.uploadFile(file.file, onProgress)
    uploadedImagePaths.value.push(result.path)
    uploadedImageList.value.push({
      id: result.file_id,
      name: result.filename,
      url: `file://${result.path}`,
      status: 'finished'
    })
    message.success(`上传成功: ${result.filename}`)
    onFinish()
  } catch (e) {
    message.error(`上传失败: ${e.message}`)
    onError()
  }
}

const handleRemoveImage = ({ id }) => {
  const idx = uploadedImageList.value.findIndex(f => f.id === id)
  if (idx !== -1) {
    uploadedImageList.value.splice(idx, 1)
    uploadedImagePaths.value.splice(idx, 1)
  }
}

// 上传单文件到指定字段
const handleUploadFile = async ({ file, onProgress, onFinish, onError }, field) => {
  try {
    const result = await api.uploadFile(file.file, onProgress)
    params[field] = result.path
    message.success(`上传成功: ${result.filename}`)
    onFinish()
  } catch (e) {
    message.error(`上传失败: ${e.message}`)
    onError()
  }
}

const handleUploadBgm = async ({ file, onProgress, onFinish, onError }) => {
  try {
    const result = await api.uploadFile(file.file, onProgress)
    params.bgm = result.path
    message.success(`BGM上传成功: ${result.filename}`)
    onFinish()
  } catch (e) {
    message.error(`上传失败: ${e.message}`)
    onError()
  }
}

const submitForm = async () => {
  try {
    await formRef.value?.validate()
  } catch (e) {
    message.warning('请补全必填字段')
    return
  }

  // 校验输入模式
  const hasUploadedImages = uploadedImagePaths.value.length > 0
  if (workflowId.value === 'carousel' || workflowId.value === 'meme') {
    const mode = workflowId.value === 'carousel' ? carouselMode.value : memeInputMode.value
    if (mode === 'upload' && !hasUploadedImages) {
      message.error('请至少上传一张图片')
      return
    }
    if (mode === 'local' && !params.source) {
      message.error('请填写本地目录路径')
      return
    }
    if (mode === 'json') {
      try {
        params.data = JSON.parse(cardsJsonText.value)
        params.source = ''
      } catch (e) {
        message.error('JSON格式错误: ' + e.message)
        return
      }
    }
  }

  if (workflowId.value === 'doc_video' && (!params.pdf || !params.mp4)) {
    message.error('请上传PDF和视频文件')
    return
  }
  if (workflowId.value === 'narration' && !params.mp4) {
    message.error('请上传视频文件')
    return
  }

  submitting.value = true
  try {
    // 构造最终params
    const submitParams = { ...params }

    // 添加上传的图片路径
    if (hasUploadedImages && (carouselMode.value === 'upload' || memeInputMode.value === 'upload')) {
      submitParams.uploaded_images = [...uploadedImagePaths.value]
      delete submitParams.source
    }

    // 移除空值和不相关字段
    Object.keys(submitParams).forEach(k => {
      if (submitParams[k] === '' || submitParams[k] === null || submitParams[k] === undefined) {
        delete submitParams[k]
      }
    })

    const result = await api.createTask({
      workflow: workflowId.value,
      name: params.name || undefined,
      params: submitParams
    })
    message.success('任务已创建，正在后台执行')
    router.push(`/tasks/${result.task_id}`)
  } catch (e) {
    message.error(`创建任务失败: ${e.message}`)
  } finally {
    submitting.value = false
  }
}
</script>
