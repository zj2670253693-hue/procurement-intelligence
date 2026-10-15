<template>
  <div v-if="datasetId!=='official'" style="margin-bottom:16px">
    <el-alert v-if="loading" title="正在核对当前批次覆盖状态；尚不能确认是否完整。" type="info" :closable="false"/>
    <el-alert v-else-if="error" :title="'覆盖状态核对失败：'+error+'；请勿把当前展示当成完整成果。'" type="warning" :closable="false"/>
    <template v-else-if="message"><el-alert :title="message" type="warning" show-icon :closable="false"/><el-button link @click="report">下载本批次失败／不完整报告</el-button></template>
  </div>
</template>
<script setup>
import {ref,computed,watch} from 'vue'
import {useDataset} from '../services/useDataset'
import {datasetCoverage} from '../api/task3'
import {downloadStandard} from '../api/task2'
import {coverageMessage} from '../services/coveragePresentation'
const {datasetId,context}=useDataset(),coverage=ref(null),loading=ref(false),error=ref('')
const message=computed(()=>coverageMessage(coverage.value))
watch(datasetId,async id=>{
  context.select(id);const ticket=context.ticket();coverage.value=null;error.value='';loading.value=id!=='official'
  if(id==='official')return
  try{const value=await datasetCoverage(id);if(context.accept(ticket))coverage.value=value}
  catch(e){if(context.accept(ticket))error.value=e.message}
  finally{if(context.accept(ticket))loading.value=false}
},{immediate:true})
async function report(){try{await downloadStandard(datasetId.value,'errors.json')}catch(e){error.value=e.message}}
</script>
