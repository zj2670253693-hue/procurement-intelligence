<template>
  <div>
    <el-card shadow="never">
      <div class="hero">
        <div class="hero-icon">
          <el-icon><Share /></el-icon>
        </div>
        <div class="hero-title">{{ title }}</div>
        <div class="hero-desc">{{ description }}</div>
        <el-tag type="warning" effect="light" round class="hero-tag">接口开发中，敬请期待</el-tag>
      </div>
    </el-card>

    <el-card shadow="never" class="mt-16">
      <template #header>
        <span>规划中的业务场景</span>
        <span class="page-hint">共 {{ items.length }} 个场景，每个对应一项独立查询接口</span>
      </template>

      <el-row :gutter="14">
        <el-col v-for="(item, i) in items" :key="i" :xs="24" :md="12" class="scene-col">
          <div class="scene">
            <div class="scene-no">{{ String(i + 1).padStart(2, '0') }}</div>
            <div class="scene-text">{{ item }}</div>
          </div>
        </el-col>
      </el-row>
    </el-card>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()

const title = computed(() => route.meta?.title || '功能建设中')
const description = computed(() => route.meta?.description || '')
const items = computed(() => route.meta?.items || [])
</script>

<style scoped>
.hero {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  padding: 26px 20px 30px;
}

.hero-icon {
  width: 62px;
  height: 62px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 18px;
  font-size: 30px;
  color: #7c3aed;
  background: #f5f3ff;
  margin-bottom: 16px;
}

.hero-title {
  font-size: 19px;
  font-weight: 600;
}

.hero-desc {
  max-width: 660px;
  margin-top: 10px;
  font-size: 13px;
  line-height: 1.8;
  color: var(--c-text-2);
}

.hero-tag {
  margin-top: 16px;
}

.scene-col {
  margin-bottom: 14px;
}

.scene {
  display: flex;
  gap: 13px;
  height: 100%;
  padding: 14px 16px;
  border-radius: var(--radius);
  background: var(--c-surface-2);
  border: 1px solid var(--c-border);
  transition: all 0.2s ease;
}

.scene:hover {
  border-color: #c7d2fe;
  background: #f8faff;
  box-shadow: var(--shadow-sm);
}

.scene-no {
  flex: 0 0 auto;
  font-size: 13px;
  font-weight: 700;
  color: #7c3aed;
  font-variant-numeric: tabular-nums;
  padding-top: 1px;
}

.scene-text {
  font-size: 13px;
  line-height: 1.75;
  color: var(--c-text-2);
}
</style>