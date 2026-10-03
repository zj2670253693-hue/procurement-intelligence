import client from './client'

/**
 * 任务一：实体识别与自动化提取 相关接口
 *
 * 后续任务二、任务三的接口请新建 task2.js / task3.js，
 * 组件层只依赖这些服务函数，接口变化时不用改动页面。
 */

/** 健康检查：服务与数据库连通性 + 数据总量 */
export function getHealth() {
  return client.get('/health')
}

/** 提取结果总览：公告数、标的物数、7 个字段填充率 */
export function getStats() {
  return client.get('/stats')
}

/**
 * 标的物检索（分页）
 * @param {Object} params
 * @param {string} [params.keyword]   关键词，匹配产品名称/品牌/规格型号/品目
 * @param {string} [params.field]     只返回该字段非空的记录，如 unit_price
 * @param {number} [params.page]      页码，从 1 开始
 * @param {number} [params.page_size] 每页条数
 */
export function searchEntities(params) {
  return client.get('/entities', { params })
}

/** 查询某篇公告提取出的全部标的物 */
export function getEntitiesByAnnouncement(announcementId) {
  return client.get(`/entities/${announcementId}`)
}

/** 查看公告原文（HTML 正文 + 附件文件清单） */
export function getAnnouncement(announcementId) {
  return client.get(`/announcements/${announcementId}`)
}

/** 按需加载公告附件文本（解析较慢，切到附件页签时再调） */
export function getAnnouncementAttachments(announcementId) {
  return client.get(`/announcements/${announcementId}/attachments`)
}