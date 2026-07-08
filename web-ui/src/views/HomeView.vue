<template>
  <div class="page-container">
    <div style="margin-bottom: 32px; text-align: center; padding: 40px 0 20px;">
      <h1 style="font-size: 36px; margin: 0 0 12px; background: linear-gradient(135deg, #18a058, #36ad6a); -webkit-background-clip: text; -webkit-text-fill-color: transparent;">选择视频工作流</h1>
      <p style="font-size: 16px; color: #666; margin: 0;">输入内容，一键生成剪映草稿，直接在剪映专业版中打开编辑</p>
    </div>

    <n-grid :cols="3" :x-gap="20" :y-gap="20">
      <n-gi v-for="wf in workflows" :key="wf.id" class="workflow-card-wrapper">

        <n-card class="workflow-card" @click="selectWorkflow(wf)" hoverable>
          <div style="padding: 8px 0;">
            <div style="display: flex; align-items: center; margin-bottom: 12px;">
              <n-icon size="32" :color="wf.color" style="margin-right: 12px;">
                <component :is="wf.icon" />
              </n-icon>
              <div>
                <h3 style="margin: 0; font-size: 18px;">
                  {{ wf.label }}
                  <span :class="wf.free ? 'free-badge' : 'paid-badge'">{{ wf.free ? '零费用' : 'AI生成' }}</span>
                </h3>
              </div>
            </div>
            <p style="color: #666; font-size: 14px; line-height: 1.6; margin: 0; min-height: 44px;">
              {{ wf.description }}
            </p>
            <div style="margin-top: 16px; display: flex; flex-wrap: wrap; gap: 6px;">
              <n-tag v-for="tag in wf.tags" :key="tag" size="small" :bordered="false" type="info">{{ tag }}</n-tag>
            </div>
          </div>
        </n-card>
      </n-gi>
    </n-grid>

    <div style="margin-top: 48px; padding: 24px; background: #fff; border-radius: 12px;">
      <n-alert type="info" title="使用说明" :show-icon="false">
        <ol style="margin: 8px 0 0; padding-left: 20px; line-height: 2;">
          <li>选择合适的工作流，填写参数并上传所需素材</li>
          <li>点击「开始生成」，后台会自动执行流水线</li>
          <li>可以在「任务列表」中查看所有历史任务和实时进度</li>
          <li>任务完成后，点击「打开草稿目录」按钮，然后在剪映专业版中打开草稿即可</li>
          <li><strong>零费用</strong>工作流完全本地处理，不调用任何AI接口，无任何费用</li>
        </ol>
      </n-alert>
    </div>
  </div>
</template>

<script setup>
import { h } from 'vue'
import { useRouter } from 'vue-router'
import { NIcon } from 'naive-ui'

const router = useRouter()

// 图标
const IconSparkles = () => h('svg', { viewBox: '0 0 24 24', fill: 'currentColor' }, [
  h('path', { d: 'M12 2l2.4 7.6L22 12l-7.6 2.4L12 22l-2.4-7.6L2 12l7.6-2.4z' })
])
const IconImages = () => h('svg', { viewBox: '0 0 24 24', fill: 'currentColor' }, [
  h('path', { d: 'M21 19V5c0-1.1-.9-2-2-2H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2zM8.5 13.5l2.5 3.01L14.5 12l4.5 6H5l3.5-4.5z' })
])
const IconCarousel = () => h('svg', { viewBox: '0 0 24 24', fill: 'currentColor' }, [
  h('path', { d: 'M2 6h4v12H2zm7-3h4v18H9zm7 6h4v6h-4z' })
])
const IconCode = () => h('svg', { viewBox: '0 0 24 24', fill: 'currentColor' }, [
  h('path', { d: 'M9.4 16.6L4.8 12l4.6-4.6L8 6l-6 6 6 6 1.4-1.4zm5.2 0l4.6-4.6-4.6-4.6L16 6l6 6-6 6-1.4-1.4z' })
])
const IconDoc = () => h('svg', { viewBox: '0 0 24 24', fill: 'currentColor' }, [
  h('path', { d: 'M14 2H6c-1.1 0-1.99.9-1.99 2L4 20c0 1.1.89 2 1.99 2H18c1.1 0 2-.9 2-2V8l-6-6zm2 16H8v-2h8v2zm0-4H8v-2h8v2zm-3-5V3.5L18.5 9H13z' })
])
const IconMic = () => h('svg', { viewBox: '0 0 24 24', fill: 'currentColor' }, [
  h('path', { d: 'M12 14c1.66 0 2.99-1.34 2.99-3L15 5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3zm5.3-3c0 3-2.54 5.1-5.3 5.1S6.7 14 6.7 11H5c0 3.41 2.72 6.23 6 6.72V21h2v-3.28c3.28-.48 6-3.3 6-6.72h-1.7z' })
])

const workflows = [
  {
    id: 'topic',
    label: '主题AI生成片',
    description: '输入主题，AI自动写稿+生图+配音+字幕，生成历史/人物介绍短视频',
    free: false,
    color: '#18a058',
    icon: IconSparkles,
    tags: ['LLM写稿', 'AI生图', 'TTS配音', '自动运镜']
  },
  {
    id: 'meme',
    label: '梗图/图集视频',
    description: '上传本地图片，自动生成带模糊背景+BGM的图集视频',
    free: true,
    color: '#f0a020',
    icon: IconImages,
    tags: ['零费用', '模糊背景', 'BGM', '自动挑图']
  },
  {
    id: 'carousel',
    label: '卡片轮播短视频',
    description: '图片或JSON数据，自动生成横向滚动卡片轮播短视频（球员评分/商品推荐）',
    free: true,
    color: '#d03050',
    icon: IconCarousel,
    tags: ['零费用', '卡片渲染', '滚动动画', 'BGM']
  },
  {
    id: 'code_walk',
    label: '代码走读视频',
    description: '指定前端项目路径，自动抓取UI+生成讲稿+配音，制作代码走读视频',
    free: false,
    color: '#2080f0',
    icon: IconCode,
    tags: ['Playwright抓UI', '代码高亮', 'LLM讲稿', 'TTS配音']
  },
  {
    id: 'doc_video',
    label: '需求讲解视频',
    description: '上传PDF文档+录屏视频，视觉大模型理解画面，自动生成讲解配音',
    free: false,
    color: '#7c3aed',
    icon: IconDoc,
    tags: ['PDF解析', '视觉大模型', '智能切段', 'TTS配音']
  },
  {
    id: 'narration',
    label: '录屏讲解视频',
    description: '上传录屏视频，AI理解画面自动生成讲解配音和字幕（软件教程/操作演示）',
    free: false,
    color: '#0891b2',
    icon: IconMic,
    tags: ['视觉大模型', '智能切段', 'TTS配音', '字幕']
  }
]

const selectWorkflow = (wf) => {
  router.push(`/workflow/${wf.id}`)
}
</script>
