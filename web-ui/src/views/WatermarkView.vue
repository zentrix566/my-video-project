<template>
  <div class="page-container">
    <div style="margin-bottom: 24px;">
      <n-button @click="$router.push('/')" quaternary size="small" style="margin-bottom: 12px;">
        ← 返回首页
      </n-button>
      <h1 style="font-size: 28px; margin: 0 0 8px;">🖼️ 图片去水印工具</h1>
      <p style="color: #666; margin: 0;">上传图片 → 框选水印区域 → 一键去除。传统算法本地快速处理，AI模型效果更佳。</p>
    </div>

    <!-- 使用步骤 -->
    <div v-if="!imageUrl" style="margin-bottom: 24px;">
      <n-steps :current="1" status="process">
        <n-step title="上传图片" description="支持JPG/PNG/WebP" />
        <n-step title="框选水印" description="鼠标拖拽选择区域" />
        <n-step title="一键去除" description="选择算法开始处理" />
        <n-step title="下载结果" description="对比后保存图片" />
      </n-steps>
    </div>

    <!-- 上传区域 -->
    <n-upload
      v-if="!imageUrl"
      :show-file-list="false"
      accept="image/*"
      @before-upload="handleUpload"
      drag
      style="margin-bottom: 24px;"
    >
      <div style="padding: 48px 0; text-align: center;">
        <div style="font-size: 56px; margin-bottom: 12px;">📷</div>
        <n-text depth="2" style="font-size: 18px; font-weight: 500;">点击或拖拽图片到此处上传</n-text>
        <n-p depth="3" style="margin-top: 8px; font-size: 13px;">支持 JPG / PNG / WebP 格式，建议图片不要超过 4096×4096</n-p>
      </div>
    </n-upload>

    <!-- 工作区 -->
    <div v-if="imageUrl" style="display: flex; gap: 20px; flex-wrap: wrap;">
      <!-- 左侧：图片编辑区 -->
      <div style="flex: 1; min-width: 400px;">
        <n-card :bordered="false" style="margin-bottom: 16px;">
          <template #header>
            <div style="display: flex; justify-content: space-between; align-items: center;">
              <n-space align="center" :size="12">
                <span>{{ showResult ? '🔍 处理结果' : '✏️ 框选水印区域' }}</span>
                <n-tag v-if="regions.length === 0 && !showResult" type="warning" size="small">请框选水印</n-tag>
                <n-tag v-else-if="!showResult" type="success" size="small">已选 {{ regions.length }} 个区域</n-tag>
              </n-space>
              <div>
                <n-button-group size="small">
                  <n-button @click="showResult = false" :type="!showResult ? 'primary' : 'default'">原图</n-button>
                  <n-button @click="showResult = true" :type="showResult ? 'primary' : 'default'" :disabled="!resultUrl">结果</n-button>
                </n-button-group>
              </div>
            </div>
          </template>

          <div class="editor-area">
            <div class="img-wrapper"
              @mousedown="startDraw"
              @mousemove="onDraw"
              @mouseup="endDraw"
              @mouseleave="endDraw"
              :style="{ cursor: showResult ? 'default' : 'crosshair' }"
            >
              <img
                ref="imgRef"
                :src="showResult ? (resultUrl || imageUrl) : imageUrl"
                @load="onImageLoad"
                :style="{ maxWidth: '100%', maxHeight: '550px', display: 'block' }"
                draggable="false"
              />
              <!-- SVG 遮罩层 -->
              <svg
                v-if="!showResult"
                class="draw-layer"
                :viewBox="`0 0 ${imgNaturalW} ${imgNaturalH}`"
                preserveAspectRatio="none"
              >
                <rect
                  v-for="(r, i) in regions"
                  :key="i"
                  :x="r.x" :y="r.y" :width="r.w" :height="r.h"
                  :fill="methodColor(method, 0.25)"
                  :stroke="methodColor(method, 1)"
                  stroke-width="2"
                  :vector-effect="'non-scaling-stroke'"
                />
                <rect
                  v-if="currentRect"
                  :x="currentRect.x" :y="currentRect.y"
                  :width="currentRect.w" :height="currentRect.h"
                  :fill="methodColor(method, 0.15)"
                  :stroke="methodColor(method, 1)"
                  stroke-width="2"
                  stroke-dasharray="6,4"
                  :vector-effect="'non-scaling-stroke'"
                />
              </svg>

              <!-- 新手引导提示 -->
              <div v-if="!showResult && regions.length === 0" class="draw-hint">
                <div style="background: rgba(0,0,0,0.7); color: white; padding: 12px 20px; border-radius: 8px; font-size: 14px; pointer-events: none;">
                  👆 按住鼠标左键拖拽，框选要去除的水印区域
                </div>
              </div>
            </div>
          </div>

          <template #footer>
            <div style="display: flex; gap: 8px; flex-wrap: wrap; align-items: center;">
              <n-button @click="resetImage" size="small">🔄 重新上传</n-button>
              <n-button @click="clearRegions" size="small" :disabled="regions.length === 0">🗑️ 清除选框</n-button>
              <n-button @click="undoRegion" size="small" :disabled="regions.length === 0">↩️ 撤销</n-button>
              <n-tag v-if="showResult" type="success" size="small" style="margin-left: auto;">处理完成，可下载结果</n-tag>
            </div>
          </template>
        </n-card>
      </div>

      <!-- 右侧：参数面板 -->
      <div style="width: 340px; flex-shrink: 0;">
        <!-- 算法选择 -->
        <n-card :bordered="false" style="margin-bottom: 16px;">
          <template #header>
            <n-space align="center" :size="8">
              <span>选择算法</span>
              <n-tooltip trigger="hover">
                <template #trigger>
                  <n-icon size="16" style="color: #999; cursor: help;">
                    <svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-6h2v6zm0-8h-2V7h2v2z"/></svg>
                  </n-icon>
                </template>
                不同算法效果和速度不同，可多次尝试对比
              </n-tooltip>
            </n-space>
          </template>
          <n-radio-group v-model:value="method" style="width: 100%;">
            <n-space vertical :size="8" style="width: 100%;">
              <div v-for="m in availableMethods" :key="m.id" class="method-option"
                :class="{ 'method-disabled': m.available === false, 'method-selected': method === m.id }">
                <n-radio :value="m.id" :disabled="m.available === false" style="align-items: flex-start; width: 100%;">
                  <div style="padding-left: 4px; width: 100%;">
                    <div style="font-weight: 500; display: flex; align-items: center; gap: 6px; flex-wrap: wrap;">
                      {{ m.label }}
                      <n-tag v-if="m.type === 'traditional'" size="tiny" type="default">快速</n-tag>
                      <n-tag v-else-if="m.type === 'ai-cloud'" size="tiny" type="warning">AI云端</n-tag>
                      <n-tag v-if="m.available === false" size="tiny" type="error">不可用</n-tag>
                    </div>
                    <n-text depth="3" style="font-size: 12px; line-height: 1.5; margin-top: 2px; display: block;">{{ m.desc }}</n-text>
                  </div>
                </n-radio>
              </div>
            </n-space>
          </n-radio-group>

          <!-- 提示信息 -->
          <n-alert v-if="method === 'cloud' && !cloudConfigured" type="warning" style="margin-top: 12px;" :show-icon="true">
            需要配置火山引擎密钥：<br />
            在 <code>.env</code> 文件添加：<br />
            <code>VOLC_ACCESS_KEY=你的AK</code><br />
            <code>VOLC_SECRET_KEY=你的SK</code><br />
            <span style="font-size: 12px;">并开通「智能图像处理-物体擦除」服务</span>
          </n-alert>
        </n-card>

        <!-- 参数设置 -->
        <n-card :bordered="false" style="margin-bottom: 16px;">
          <template #header>参数设置</template>
          <n-form label-placement="top" size="small">
            <!-- 修复半径（仅传统算法） -->
            <n-form-item v-if="isTraditional" label="修复半径（像素）">
              <n-slider v-model:value="radius" :min="1" :max="20" :step="1" />
              <n-text depth="3" style="font-size: 12px;">值越大参考周围越多像素，适合较大水印</n-text>
            </n-form-item>

            <!-- 边缘扩展（所有算法） -->
            <n-form-item label="边缘扩展（像素）">
              <n-slider v-model:value="padding" :min="0" :max="15" :step="1" />
              <n-text depth="3" style="font-size: 12px;">框选区域向外扩展像素，避免边缘残留</n-text>
            </n-form-item>
          </n-form>
        </n-card>

        <!-- 执行按钮 -->
        <n-button
          type="primary"
          block
          size="large"
          :loading="processing"
          :disabled="!canProcess"
          @click="processImage"
        >
          <template v-if="processing">
            <n-spin size="small" style="margin-right: 8px;" />
            {{ processingTip }}
          </template>
          <template v-else>✨ 开始去除水印</template>
        </n-button>

        <div v-if="regions.length === 0" style="margin-top: 8px; text-align: center; color: #f0a020; font-size: 13px;">
          请先在图片上框选水印区域
        </div>

        <n-button
          v-if="resultUrl"
          block
          size="large"
          type="success"
          style="margin-top: 8px;"
          @click="downloadResult"
        >
          💾 下载处理后的图片
        </n-button>

        <n-alert v-if="resultUrl" type="success" style="margin-top: 16px;" :show-icon="true">
          处理完成！点击上方「原图/结果」切换对比，不满意可调整参数或重新框选。
        </n-alert>

        <!-- 快速提示 -->
        <n-alert type="info" style="margin-top: 16px;" :show-icon="false">
          <div style="font-size: 12px; line-height: 1.8;">
            <div><strong>💡 使用技巧：</strong></div>
            <div>• 小水印/纯色背景用传统算法秒出结果</div>
            <div>• 复杂背景/大面积水印推荐AI算法</div>
            <div>• 可多次框选多个区域批量处理</div>
          </div>
        </n-alert>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, nextTick } from 'vue'
import { useMessage } from 'naive-ui'
import { api } from '../api'

const message = useMessage()

const imageUrl = ref('')
const resultUrl = ref('')
const showResult = ref(false)
const originalFile = ref(null)

const editorRef = ref(null)
const imgRef = ref(null)
const imgNaturalW = ref(0)
const imgNaturalH = ref(0)
const imgDisplayRect = ref({ left: 0, top: 0, width: 0, height: 0 })

const regions = ref([])
const drawing = ref(false)
const startPos = ref({ x: 0, y: 0 })
const currentRect = ref(null)

const method = ref('telea')
const radius = ref(5)
const padding = ref(3)
const processing = ref(false)
const processingTip = ref('处理中...')

// 算法可用性
const allMethods = ref([
  { id: 'telea', label: 'OpenCV TELEA', type: 'traditional', desc: '速度最快，适合小面积纯色背景水印', available: true },
  { id: 'ns', label: 'OpenCV NS', type: 'traditional', desc: '边缘衔接更自然，速度稍慢', available: true },
  { id: 'cloud', label: 'AI 云端 (火山引擎)', type: 'ai-cloud', desc: '商用级擦除，效果最佳，需配置AK/SK', available: false },
])

const cloudConfigured = ref(false)

onMounted(async () => {
  try {
    const cfg = await api.getWatermarkConfig()
    for (const m of cfg.methods) {
      const found = allMethods.value.find(x => x.id === m.id)
      if (found) {
        found.available = m.available !== false
      }
    }
    cloudConfigured.value = allMethods.value.find(m => m.id === 'cloud')?.available ?? false

    // 自动选中第一个可用算法
    const firstAvailable = allMethods.value.find(m => m.available !== false)
    if (firstAvailable) {
      method.value = firstAvailable.id
    }
  } catch (e) {
    console.warn('加载去水印配置失败:', e)
    message.warning('配置加载失败，默认使用传统算法')
  }
})

const availableMethods = computed(() => allMethods.value)

const isTraditional = computed(() => method.value === 'telea' || method.value === 'ns')

const canProcess = computed(() => {
  if (regions.value.length === 0) return false
  if (processing.value) return false
  const m = allMethods.value.find(x => x.id === method.value)
  return m && m.available !== false
})

function methodColor(m, alpha) {
  if (m === 'cloud') return `rgba(240,160,32,${alpha})`
  return `rgba(255,80,0,${alpha})`
}

function handleUpload(file) {
  if (!file.file.type.startsWith('image/')) {
    message.error('请上传图片文件')
    return false
  }
  // 检查文件大小（限制30MB）
  if (file.file.size > 30 * 1024 * 1024) {
    message.error('图片大小不能超过30MB')
    return false
  }
  originalFile.value = file.file
  if (imageUrl.value) URL.revokeObjectURL(imageUrl.value)
  if (resultUrl.value) URL.revokeObjectURL(resultUrl.value)
  imageUrl.value = URL.createObjectURL(file.file)
  resultUrl.value = ''
  showResult.value = false
  regions.value = []
  message.success('图片上传成功，请框选水印区域')
  return false
}

function resetImage() {
  if (imageUrl.value) URL.revokeObjectURL(imageUrl.value)
  if (resultUrl.value) URL.revokeObjectURL(resultUrl.value)
  imageUrl.value = ''
  resultUrl.value = ''
  originalFile.value = null
  regions.value = []
  showResult.value = false
}

function onImageLoad() {
  const img = imgRef.value
  if (!img) return
  imgNaturalW.value = img.naturalWidth
  imgNaturalH.value = img.naturalHeight
  requestAnimationFrame(updateDisplayRect)
}

function updateDisplayRect() {
  const img = imgRef.value
  if (!img) return
  const rect = img.getBoundingClientRect()
  imgDisplayRect.value = {
    left: rect.left,
    top: rect.top,
    width: rect.width,
    height: rect.height
  }
}

function getImgCoords(e) {
  updateDisplayRect()
  const r = imgDisplayRect.value
  if (r.width === 0 || r.height === 0) return { x: 0, y: 0 }
  const sx = (e.clientX - r.left) / r.width * imgNaturalW.value
  const sy = (e.clientY - r.top) / r.height * imgNaturalH.value
  return {
    x: Math.max(0, Math.min(imgNaturalW.value, sx)),
    y: Math.max(0, Math.min(imgNaturalH.value, sy))
  }
}

function startDraw(e) {
  if (showResult.value) return
  if (e.button !== 0) return
  e.preventDefault()
  drawing.value = true
  const p = getImgCoords(e)
  startPos.value = p
  currentRect.value = { x: p.x, y: p.y, w: 0, h: 0 }
}

function onDraw(e) {
  if (!drawing.value || showResult.value) return
  e.preventDefault()
  const p = getImgCoords(e)
  const x = Math.min(startPos.value.x, p.x)
  const y = Math.min(startPos.value.y, p.y)
  const w = Math.abs(p.x - startPos.value.x)
  const h = Math.abs(p.y - startPos.value.y)
  currentRect.value = { x, y, w, h }
}

function endDraw() {
  if (!drawing.value) return
  drawing.value = false
  if (currentRect.value && currentRect.value.w > 5 && currentRect.value.h > 5) {
    regions.value.push({ ...currentRect.value })
    message.success(`已添加区域 ${regions.value.length}，可继续框选或点击开始处理`)
  }
  currentRect.value = null
}

function clearRegions() {
  regions.value = []
  message.info('已清除所有选框')
}

function undoRegion() {
  if (regions.value.length > 0) {
    regions.value.pop()
    message.info('已撤销最后一个选框')
  }
}

function removeRegion(i) {
  regions.value.splice(i, 1)
}

async function processImage() {
  if (!originalFile.value || regions.value.length === 0) {
    message.warning('请先框选水印区域')
    return
  }
  processing.value = true
  showResult.value = false

  if (method.value === 'cloud') {
    processingTip.value = '正在调用云端AI处理...'
  } else {
    processingTip.value = '正在处理...'
  }

  try {
    const url = await api.removeWatermark(
      originalFile.value,
      regions.value.map(r => ({
        x: Math.round(r.x),
        y: Math.round(r.y),
        w: Math.round(r.w),
        h: Math.round(r.h)
      })),
      method.value,
      radius.value,
      padding.value
    )
    if (resultUrl.value) URL.revokeObjectURL(resultUrl.value)
    resultUrl.value = url
    showResult.value = true
    message.success('✅ 水印去除完成！')
  } catch (e) {
    console.error('处理失败:', e)
    message.error('处理失败: ' + e.message, { duration: 8000 })
  } finally {
    processing.value = false
  }
}

function downloadResult() {
  if (!resultUrl.value || !originalFile.value) return
  const a = document.createElement('a')
  a.href = resultUrl.value
  const origName = originalFile.value.name
  const dot = origName.lastIndexOf('.')
  const base = dot > 0 ? origName.slice(0, dot) : origName
  const ext = dot > 0 ? origName.slice(dot) : '.png'
  a.download = `${base}_无水印${ext}`
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  message.success('图片下载已开始')
}
</script>

<style scoped>
.editor-area {
  background: #f5f5f5;
  border-radius: 6px;
  padding: 12px;
  min-height: 300px;
  display: flex;
  justify-content: center;
  align-items: center;
  position: relative;
}
.img-wrapper {
  position: relative;
  display: inline-block;
  line-height: 0;
  box-shadow: 0 2px 12px rgba(0,0,0,0.1);
}
.draw-layer {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
}
.draw-hint {
  position: absolute;
  top: 20px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 10;
  animation: pulse 2s infinite;
}
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.7; }
}
.method-option {
  padding: 10px 12px;
  border: 1px solid #e0e0e6;
  border-radius: 8px;
  transition: all 0.2s;
  cursor: pointer;
  width: 100%;
  box-sizing: border-box;
}
.method-option:hover:not(.method-disabled) {
  border-color: #18a058;
  background: #f0faf3;
}
.method-option.method-selected {
  border-color: #18a058;
  background: #f0faf3;
}
.method-option.method-disabled {
  opacity: 0.55;
  cursor: not-allowed;
  background: #fafafa;
}
.method-option.method-disabled:hover {
  border-color: #e0e0e6;
  background: #fafafa;
}
code {
  background: #f5f5f5;
  padding: 2px 6px;
  border-radius: 3px;
  font-size: 12px;
  font-family: 'Fira Code', Consolas, monospace;
  display: inline-block;
  margin: 2px 0;
}
</style>
