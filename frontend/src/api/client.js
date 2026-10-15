import axios from 'axios'

/**
 * 统一的 axios 实例
 *
 * 开发环境走 Vite 代理：/api -> http://127.0.0.1:8000/api
 * 需要直连后端时，在 .env.local 里设置 VITE_API_BASE=http://127.0.0.1:8000/api
 */
const client = axios.create({
  baseURL: import.meta.env.VITE_API_BASE || '/api',
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

// 统一剥掉 data 外层，并把错误信息标准化成 Error.message
client.interceptors.response.use(
  (response) => response.data,
  (error) => {
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
