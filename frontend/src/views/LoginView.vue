<template><el-card style="max-width:480px;margin:40px auto"><template #header>登录招采标讯</template><el-alert v-if="expired" title="尚未登录或登录已过期，请登录后继续操作。" type="warning" :closable="false" /><el-form @submit.prevent="submit" class="mt-16"><el-form-item label="账号"><el-input v-model="username" autocomplete="username" /></el-form-item><el-form-item label="密码"><el-input v-model="password" type="password" autocomplete="current-password" show-password /></el-form-item><el-button native-type="submit" type="primary" :loading="loading">登录</el-button></el-form><p v-if="error" style="color:#b42318">{{ error }}</p><details style="font-size:12px;color:#667085;margin-top:20px"><summary>账号与安全说明</summary><p>请使用网站账号登录，网站账号与服务器账号分开管理。登录信息通过加密连接传输，密码不会保存在浏览器存储中。</p></details></el-card></template>
<script setup>
import {ref,computed} from 'vue'
import {useRoute,useRouter} from 'vue-router'
import {login} from '../api/auth'
const username=ref(''),password=ref(''),error=ref(''),loading=ref(false),router=useRouter(),route=useRoute()
const expired=computed(()=>route.query.reason==='expired')
const next=computed(()=>typeof route.query.next==='string' && route.query.next.startsWith('/') && !route.query.next.startsWith('//') ? route.query.next : '/task3')
async function submit(){loading.value=true;error.value='';try{await login(username.value,password.value);password.value='';await router.replace(next.value)}catch(e){error.value=e.message;password.value=''}finally{loading.value=false}}
</script>
