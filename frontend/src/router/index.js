import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'login', component: () => import('../views/Login.vue') },
    { path: '/', name: 'dashboard', component: () => import('../views/Dashboard.vue'), meta: { auth: true } },
    { path: '/viewer/:caseId', name: 'viewer', component: () => import('../views/Viewer3D.vue'), meta: { auth: true } },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

router.beforeEach((to) => {
  const token = localStorage.getItem('token')
  if (to.meta.auth && !token) return { name: 'login' }
  if (to.name === 'login' && token) return { name: 'dashboard' }
})

export default router
