import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  {
    path: '/',
    name: 'Home',
    component: () => import('./views/HomeView.vue')
  },
  {
    path: '/workflow/:id',
    name: 'WorkflowForm',
    component: () => import('./views/WorkflowFormView.vue'),
    props: true
  },
  {
    path: '/tasks',
    name: 'TaskList',
    component: () => import('./views/TaskListView.vue')
  },
  {
    path: '/tasks/:id',
    name: 'TaskDetail',
    component: () => import('./views/TaskDetailView.vue'),
    props: true
  },
  {
    path: '/tools/watermark',
    name: 'Watermark',
    component: () => import('./views/WatermarkView.vue')
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

export default router
