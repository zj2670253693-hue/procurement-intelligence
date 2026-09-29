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
    return Promise.reject(new Error(message))
  },
)

export default client