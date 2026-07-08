<template>
  <n-config-provider>
    <n-message-provider>
      <n-dialog-provider>
        <n-notification-provider>
          <n-layout style="min-height: 100vh">
            <n-layout-header bordered style="background: #fff;">
              <div style="max-width: 1400px; margin: 0 auto; padding: 0 24px; display: flex; align-items: center; justify-content: space-between; height: 60px;">
                <div style="display: flex; align-items: center; gap: 12px; cursor: pointer;" @click="goHome">
                  <n-icon size="28" color="#18a058">
                    <svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>
                  </n-icon>
                  <h1 style="margin: 0; font-size: 20px; font-weight: 700;">AI 视频生成工作台</h1>
                </div>
                <div style="display: flex; gap: 8px;">
                  <n-button quaternary @click="goHome">
                    <template #icon>
                      <n-icon>
                        <svg viewBox="0 0 24 24" fill="currentColor"><path d="M10 20v-6h4v6h5v-8h3L12 3 2 12h3v8z"/></svg>
                      </n-icon>
                    </template>
                    工作流
                  </n-button>
                  <n-button quaternary @click="goTasks">
                    <template #icon>
                      <n-icon>
                        <svg viewBox="0 0 24 24" fill="currentColor"><path d="M19 3H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm-5 14H7v-2h7v2zm3-4H7v-2h10v2zm0-4H7V7h10v2z"/></svg>
                      </n-icon>
                    </template>
                    任务列表
                  </n-button>
                </div>
              </div>
            </n-layout-header>
            <n-layout-content>
              <router-view />
            </n-layout-content>
            <n-layout-footer style="text-align: center; padding: 20px; color: #999; font-size: 13px;">
              zentrix566 · AI 视频生成工作台 · 基于 pyJianYingDraft 自动生成剪映草稿
            </n-layout-footer>
          </n-layout>
        </n-notification-provider>
      </n-dialog-provider>
    </n-message-provider>
  </n-config-provider>
</template>

<script setup>
import { NConfigProvider, NMessageProvider, NDialogProvider, NNotificationProvider } from 'naive-ui'
import { useRouter } from 'vue-router'

const router = useRouter()

const goHome = () => router.push('/')
const goTasks = () => router.push('/tasks')
</script>
