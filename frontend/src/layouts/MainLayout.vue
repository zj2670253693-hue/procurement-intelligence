<template>
  <el-container class="layout">
    <el-aside :width="asideWidth" class="aside">
      <div class="brand">
        <div class="brand-logo">
          <el-icon><DataLine /></el-icon>
        </div>
        <div class="brand-text">
          <div class="brand-title">招采标讯</div>
          <div class="brand-sub">智能分析引擎</div>
        </div>
      </div>

      <div class="nav-group-label">技术指标</div>

      <el-menu :default-active="activeMenu" :default-openeds="defaultOpeneds" router class="nav">
        <el-sub-menu index="task1">
          <template #title>
            <el-icon><Search /></el-icon>
            <span>任务一 · 实体识别与自动化提取</span>
          </template>
          <el-menu-item index="/task1/stats">
            <el-icon><PieChart /></el-icon>
            <span>数据总览</span>
          </el-menu-item>
          <el-menu-item index="/task1/entities">
            <el-icon><Files /></el-icon>
            <span>标的物检索</span>
          </el-menu-item>
        </el-sub-menu>

        <el-sub-menu index="task2">
          <template #title>
            <el-icon><Share /></el-icon>
            <span>任务二 · 用户画像与关系分析</span>
          </template>
          <el-menu-item index="/task2">
            <el-icon><Connection /></el-icon>
            <span>业务场景查询</span>
          </el-menu-item>
        </el-sub-menu>
      </el-menu>

      <div class="nav-group-label">平台能力</div>

      <el-menu :default-active="activeMenu" router class="nav">
        <el-menu-item index="/task3">
          <el-icon><UploadFilled /></el-icon>
          <span>任务三 · 数据集上传与处理</span>
        </el-menu-item>
      </el-menu>

      <div class="aside-footer">
        <span>第三届高校 ICT 产教融合创新大赛</span>
        <span class="aside-footer-sub">赛题五 · 中国软件与技术服务股份有限公司</span>
      </div>
    </el-aside>

    <el-container class="main-wrap">
      <el-header class="header" :height="'60px'">
        <div class="header-left">
          <div class="header-title">{{ pageTitle }}</div>
          <div v-if="pageDesc" class="header-desc">{{ pageDesc }}</div>
        </div>

        <div class="header-right">
          <div class="stat-chip">
            <el-icon class="chip-icon"><Files /></el-icon>
            <span class="chip-num">{{ health?.announcements ?? '—' }}</span>
            <span class="chip-label">公告</span>
          </div>
          <div class="stat-chip">
            <el-icon class="chip-icon"><Goods /></el-icon>
            <span class="chip-num">{{ health?.entities ?? '—' }}</span>
            <span class="chip-label">标的物</span>
          </div>
          <div class="conn" :class="connected ? 'is-on' : 'is-off'">
            <span class="dot" />
            {{ connected ? '后端已连接' : '后端未连接' }}
          </div>
        </div>
      </el-header>

      <el-main class="main">
        <div class="main-inner">
          <router-view />
        </div>
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import { getHealth } from '../api/task1'

const route = useRoute()
const health = ref(null)

const asideWidth = '248px'
const defaultOpeneds = ['task1', 'task2']

const activeMenu = computed(() => route.path)
const pageTitle = computed(() => route.meta?.title || '')
const pageDesc = computed(() => route.meta?.headerDesc || '')
const connected = computed(() => health.value?.status === 'ok')

onMounted(async () => {
  try {
    health.value = await getHealth()
  } catch {
    health.value = null
  }
})
</script>

<style scoped>
.layout {
  height: 100vh;
}

/* ---------------- 侧边栏 ---------------- */
.aside {
  display: flex;
  flex-direction: column;
  background: linear-gradient(180deg, #0f172a 0%, #16203a 100%);
  overflow-y: auto;
}

.brand {
  display: flex;
  align-items: center;
  gap: 11px;
  padding: 20px 18px;
}

.brand-logo {
  width: 36px;
  height: 36px;
  flex: 0 0 36px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 9px;
  background: linear-gradient(135deg, #3b82f6, #2563eb);
  color: #fff;
  font-size: 19px;
  box-shadow: 0 4px 12px rgba(37, 99, 235, 0.4);
}

.brand-title {
  font-size: 16px;
  font-weight: 600;
  color: #fff;
  letter-spacing: 1.5px;
  line-height: 1.2;
}

.brand-sub {
  font-size: 11px;
  color: rgba(255, 255, 255, 0.45);
  letter-spacing: 1.2px;
  margin-top: 2px;
}

.nav-group-label {
  padding: 14px 20px 6px;
  font-size: 11px;
  letter-spacing: 1.5px;
  color: rgba(255, 255, 255, 0.32);
}

.nav {
  border-right: none;
  padding: 0 10px;
  background: transparent;
  /* 配色一律走 Element Plus 官方菜单变量。
     注意：el-sub-menu 展开后会渲染嵌套的 <ul class="el-menu el-menu--inline">，
     它也带 .el-menu 类并继承 --el-menu-bg-color（默认 #fff）。
     若只把外层背景设为透明，内层会保留白底，而菜单项文字是浅色，
     就会出现「白字白底看不见、只剩白色空块」的问题。
     在根节点设置变量可自动向下继承，从根上避免这类问题。 */
  --el-menu-bg-color: transparent;
  --el-menu-text-color: var(--c-aside-text);
  --el-menu-hover-bg-color: rgba(255, 255, 255, 0.07);
  --el-menu-active-color: #ffffff;
  --el-menu-border-color: transparent;
  --el-menu-item-height: 42px;
  --el-menu-sub-item-height: 40px;
  --el-menu-base-level-padding: 14px;
  --el-menu-level-padding: 16px;
}

/* 兜底：任何层级的菜单都保持透明背景 */
.nav :deep(.el-menu) {
  background-color: transparent;
}

.nav :deep(.el-sub-menu__title),
.nav :deep(.el-menu-item) {
  height: 42px;
  line-height: 42px;
  margin-bottom: 2px;
  border-radius: 8px;
  font-size: 13px;
}

.nav :deep(.el-sub-menu__title:hover),
.nav :deep(.el-menu-item:hover) {
  background-color: rgba(255, 255, 255, 0.07);
  color: #fff;
}

.nav :deep(.el-sub-menu.is-active > .el-sub-menu__title) {
  color: #fff;
}

.nav :deep(.el-menu-item.is-active) {
  color: #fff;
  background: linear-gradient(90deg, #2563eb, #3b82f6);
  box-shadow: 0 3px 10px rgba(37, 99, 235, 0.35);
}

/* 二级菜单缩进更紧凑 */
.nav :deep(.el-menu--inline .el-menu-item) {
  padding-left: 42px !important;
  min-width: auto;
}

.aside-footer {
  margin-top: auto;
  padding: 16px 20px 18px;
  font-size: 10.5px;
  line-height: 1.7;
  color: rgba(255, 255, 255, 0.28);
  border-top: 1px solid rgba(255, 255, 255, 0.06);
  display: flex;
  flex-direction: column;
}

.aside-footer-sub {
  color: rgba(255, 255, 255, 0.2);
}

/* ---------------- 顶栏 ---------------- */
.main-wrap {
  overflow: hidden;
}

.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  background: var(--c-surface);
  border-bottom: 1px solid var(--c-border);
}

.header-title {
  font-size: 16px;
  font-weight: 600;
}

.header-desc {
  font-size: 12px;
  color: var(--c-text-3);
  margin-top: 2px;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 10px;
}

.stat-chip {
  display: flex;
  align-items: baseline;
  gap: 6px;
  padding: 6px 12px;
  border-radius: 999px;
  background: var(--c-surface-2);
  border: 1px solid var(--c-border);
}

.chip-icon {
  color: var(--c-primary);
  font-size: 13px;
  align-self: center;
}

.chip-num {
  font-size: 15px;
  font-weight: 600;
  color: var(--c-text);
  font-variant-numeric: tabular-nums;
}

.chip-label {
  font-size: 11.5px;
  color: var(--c-text-3);
}

.conn {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 6px 13px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 500;
}

.conn.is-on {
  color: #067647;
  background: #ecfdf3;
  border: 1px solid #abefc6;
}

.conn.is-off {
  color: #b42318;
  background: #fef3f2;
  border: 1px solid #fecdca;
}

.dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
  box-shadow: 0 0 0 3px rgba(0, 0, 0, 0.05);
}

/* ---------------- 内容区 ---------------- */
.main {
  padding: 20px 24px 28px;
  overflow-y: auto;
}

.main-inner {
  max-width: 1440px;
  margin: 0 auto;
}
</style>