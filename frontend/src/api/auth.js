import client from './client'
export const login = (username,password) => client.post('/platform/auth/login',{username,password})
export const session = () => client.get('/platform/auth/session')
export const logout = () => client.post('/platform/auth/logout')
