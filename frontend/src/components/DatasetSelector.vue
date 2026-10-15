<template>
  <div class="dataset-bar">
    <span>当前数据集</span>
    <el-select :model-value="selected" @change="select" style="width:340px">
      <el-option v-for="item in choices" :key="item.upload_dataset_id" :value="item.upload_dataset_id" :label="item.label || `${item.upload_dataset_id} · ${item.availability}`" :disabled="!['completed','partial'].includes(item.availability)" />
    </el-select>
    <el-button @click="refresh">刷新批次</el-button><el-button v-if="user" @click="exit">退出平台账号</el-button><router-link v-else to="/login">平台登录</router-link>
    <small v-if="error">{{ error }}（官方只读展示不受影响）</small>
  </div>
</template>
<script setup>
import {ref,computed,onMounted} from 'vue'
import {useRoute,useRouter} from 'vue-router'
import {listDatasets} from '../api/task3'
import {session,logout} from '../api/auth'
const route=useRoute(),router=useRouter(),choices=ref([{upload_dataset_id:'official',availability:'completed',label:'官方冻结数据（只读）'}]),error=ref(''),user=ref(null)
const selected=computed(()=>route.query.dataset_id || 'official')
function select(id){router.replace({path:route.path,query:{...route.query,dataset_id:id}})}
async function refresh(){error.value='';try{choices.value=(await listDatasets()).items}catch(e){error.value=e.status===401?'登录后查看个人批次':e.message}}
async function exit(){try{await logout()}finally{user.value=null;choices.value=choices.value.slice(0,1);router.push('/login')}}
onMounted(async()=>{try{user.value=await session()}catch{}await refresh()})
</script>
<style scoped>.dataset-bar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:16px;font-size:13px}small{color:#b42318}</style>
