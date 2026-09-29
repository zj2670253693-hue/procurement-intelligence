import { createRouter, createWebHistory } from 'vue-router'

import MainLayout from '../layouts/MainLayout.vue'

/**
 * 路由规划：
 *   任务一 实体识别与自动化提取   -> 已实现
 *   任务二 用户画像建模与关系分析 -> 占位，待后端场景接口完成后替换
 *   任务三 可视化分析检索平台     -> 已实现数据集上传
 */
const routes = [
  {
    path: '/',
    component: MainLayout,
    redirect: '/task1/stats',
    children: [
      {
        path: 'task1/stats',
        name: 'task1-stats',
        component: () => import('../views/StatsView.vue'),
        meta: {
          title: '数据总览',
          headerDesc: '任务一提取结果的整体情况与核心字段填充率',
        },
      },
      {
        path: 'task1/entities',
        name: 'task1-entities',
        component: () => import('../views/EntitiesView.vue'),
        meta: {
          title: '标的物检索',
          headerDesc: '按关键词与字段条件检索全部标的物提取结果',
        },
      },
      {
        path: 'task2',
        name: 'task2',
        component: () => import('../views/ComingSoonView.vue'),
        meta: {
          title: '用户画像建模与关系分析',
          headerDesc: '采购单位 - 中标供应商 - 投标参与方关系分析',
          description:
            '基于竞标与得分数据，构建「采购单位 - 中标供应商 - 投标参与方」关系模型，支撑用户画像与竞争合作分析。',
          items: [
            '指定采购单位，查询长期/大量合作的中标供应商与产品供应商（合作次数、交易总金额）',
            '指定采购单位，查询历史项目中高频（TOP5）参与投标的主体及高频协同投标组合',
            '指定中标供应商，查询高频（TOP5）共同竞标主体及全部参与投标主体',
            '指定两个或多个中标供应商，查询与其均存在合作关系的采购单位、频次与金额',
            '指定两个或多个中标供应商，查询历史共同参与竞标的项目信息与竞标结果',
          ],
        },
      },
      {
        path: 'task3',
        name: 'task3',
        component: () => import('../views/UploadView.vue'),
        meta: {
          title: '数据集上传与自动处理',
          headerDesc: '批量上传同规范数据集，自动解析、实体抽取与结构化入库',
        },
      },
    ],
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.afterEach((to) => {
  document.title = to.meta?.title
    ? `${to.meta.title} - 招采标讯智能分析引擎`
    : '招采标讯智能分析引擎'
})

export default router