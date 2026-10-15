<template><el-card style="max-width:480px;margin:40px auto"><template #header>平台审核账号登录（不是Linux账号）</template><el-alert title="密码仅通过HTTPS或受控本地连接发送，不保存到浏览器存储。" type="info" :closable="false" /><el-form @submit.prevent="submit" class="mt-16"><el-form-item label="账号"><el-input v-model="username" autocomplete="username" /></el-form-item><el-form-item label="密码"><el-input v-model="password" type="password" autocomplete="current-password" show-password /></el-form-item><el-button native-type="submit" type="primary" :loading="loading">登录</el-button></el-form><p v-if="error" style="color:#b42318">{{ error }}</p></el-card></template>
<script setup>
import {ref} from 'vue'
import {useRouter} from 'vue-router'
import {login} from '../api/auth'
const username=ref(''),password=ref(''),error=ref(''),loading=ref(false),router=useRouter()
async function submit(){loading.value=true;error.value='';try{await login(username.value,password.value);password.value='';await router.push('/task3')}catch(e){error.value=e.message;password.value=''}finally{loading.value=false}}
</script>
