import axios from 'axios'
import router from '../router'

/**
 * 统一的 axios 实例
 *
 * 开发环境走 Vite 代理：/api -> http://127.0.0.1:8000/api
 * 需要直连后端时，在 .env.local 里设置 VITE_API_BASE=http://127.0.0.1:8000/api
 */
const client = axios.create({
  baseURL: import.meta.env?.VITE_API_BASE || '/api',
  timeout: 20000,
  withCredentials: true,
})

client.interceptors.request.use((request) => {
  if (typeof document !== 'undefined' && ['post','put','patch','delete'].includes(request.method?.toLowerCase())) {
    const csrf = document.cookie.split('; ').find(item => item.startsWith('t3_csrf='))?.slice(8)
    if (csrf) request.headers['X-CSRF-Token'] = decodeURIComponent(csrf)
  }
  return request
})

/**
 * 会话失效必须显式跳回登录页。
 * 之前 401 只被包装成一条 Error：用户停在页面上继续操作，进度条照走到 100%，
 * 任务却从未到达服务器（数据库里查不到任何记录）。
 */
export function handleUnauthorized(error, { navigate } = {}) {
  const status = error?.response?.status
  const code = error?.response?.data?.code
  const expired = status === 401 || code === 'session_required'
  if (!expired) return error
  const go = navigate || ((target) => { if (router) router.replace(target) })
  const from = typeof window !== 'undefined' ? window.location.pathname + window.location.search : '/task3'
  go({ path: '/login', query: { next: from, reason: 'expired' } })
  const normalized = new Error('登录会话已失效或未登录，请重新登录后重试；本次操作没有提交到服务器。')
  normalized.status = 401
  normalized.code = code || 'session_required'
  normalized.expired = true
  return normalized
}

// 统一剥掉 data 外层，并把错误信息标准化成 Error.message
client.interceptors.response.use(
  (response) => response.data,
  (error) => {
    const unauthorized = handleUnauthorized(error)
    if (unauthorized !== error) return Promise.reject(unauthorized)
    const detail = error.response?.data?.detail
    const message =
      typeof detail === 'string' && detail
        ? detail
        : error.response
          ? `请求失败（HTTP ${error.response.status}）`
          : '无法连接后端服务，请确认后端已启动'
    const normalized = new Error(error.response?.data?.code || message)
    normalized.status = error.response?.status
    normalized.code = error.response?.data?.code
    return Promise.reject(normalized)
  },
)

export default client
