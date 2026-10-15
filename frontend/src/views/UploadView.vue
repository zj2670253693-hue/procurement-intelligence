<template>
  <div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <el-alert title="只解析不产生实体或交易结果。付费抽取需平台账号、服务器有限预算授权和已验证计数策略；不会使用旧Bearer令牌。" type="info" :closable="false" />
    <el-alert v-if="caps.public_demo" title="匿名演示仅接受1篇不超过1MB的HTML；演示批次属于公开演示空间，不要上传私有资料。" type="warning" :closable="false" />
    <el-card class="mt-16" shadow="never"><template #header>批量上传与断点恢复</template>
      <el-upload v-model:file-list="fileList" drag multiple :auto-upload="false" accept=".html,.htm,.zip" :disabled="uploading"><el-icon class="el-icon--upload"><UploadFilled/></el-icon><div>选择HTML和同名ZIP，或规范整包ZIP；单文件最多{{ Math.floor((caps.file_bytes||0)/1024**3) }}GiB</div></el-upload>
      <p>刷新后需重新选择原始文件；服务器会校验相同清单并跳过已确认分片。页面不保存文件正文、密码或API密钥。</p>
      <el-checkbox v-model="runExtraction" :disabled="!caps.paid_inference_ready || uploading">使用已获服务器有限授权的大模型抽取（可能计费）</el-checkbox>
      <div v-if="runExtraction"><el-input-number v-model="budget.max_calls" :min="1" :max="8"/><el-input-number v-model="budget.max_input_tokens" :min="1" :max="64000"/><el-input-number v-model="budget.max_output_tokens" :min="1" :max="32768"/><p>调用数／输入token／输出token有限上限；实际许可仍由服务器决定。</p></div>
      <p>{{ uploadPhase }}</p><el-progress :percentage="uploadPercent"/>
      <el-button type="primary" :loading="uploading" :disabled="!fileList.length" @click="submit">{{ receiving ? '恢复上传' : '校验并上传' }}</el-button>
      <el-button v-if="uploading" @click="controller?.abort()">停止本地上传</el-button><el-button v-if="!uploading" @click="newBatch">另建批次</el-button>
    </el-card>
    <el-card v-if="task" class="mt-16" shadow="never"><template #header>{{ statusText(task.status) }} · {{ task.task_id }}</template>
      <p>阶段：{{ task.phase }}；公告 {{ task.done || 0 }}/{{ task.total || 0 }}；基础记录 {{ task.entities || 0 }}；附件明细 {{ task.detail_entities || 0 }}</p>
      <el-progress :percentage="Math.min(100,task.total?Math.round(task.done/task.total*100):0)"/>
      <p>上游模型预留调用：{{ task.model_ledger?.calls_reserved || 0 }}；用量未知的调用：{{ task.model_ledger?.unknown_usage_attempts || 0 }}；离线关系构建调用：0（不代表上游0调用）。</p>
      <el-alert v-if="task.status==='partial'" title="仅部分成功；下载失败清单和原始结果核对，不能当完整成功。" type="warning" :closable="false"/>
      <el-alert v-if="task.status==='needs_confirmation'" title="存在不确定模型请求，禁止自动重发；需管理员核对请求及账单。" type="warning" :closable="false"/>
      <pre v-if="task.diagnostics?.length">{{ JSON.stringify(task.diagnostics,null,2) }}</pre>
      <el-button @click="reconnect">重新连接进度</el-button><el-button @click="cancel">取消服务器任务</el-button>
      <el-button v-if="['failed','cancelled'].includes(task.status)" @click="retry">明确重试（不重置费用预算）</el-button>
      <el-button v-if="task.mode==='extract' && ['completed','partial'].includes(task.status)" type="primary" @click="openResults">查看这个批次的实体及关系</el-button>
      <div v-if="['completed','partial','validated_only'].includes(task.status)"><el-button v-for="name in ['standard.xlsx','raw_results.json','errors.json','errors.csv']" :key="name" @click="download(name)">下载{{ name }}</el-button></div>
    </el-card>
    <el-card class="mt-16" shadow="never"><template #header>本人处理历史 <el-button link @click="loadTasks">刷新</el-button></template>
      <el-table :data="tasks" empty-text="没有本人任务；网络错误会单独提示"><el-table-column prop="task_id" label="任务"/><el-table-column prop="mode" label="模式"/><el-table-column label="状态"><template #default="{row}">{{ statusText(row.status) }}</template></el-table-column><el-table-column prop="entities" label="基础记录"/><el-table-column prop="detail_entities" label="附件明细"/><el-table-column label="操作"><template #default="{row}"><el-button link @click="attach(row)">恢复查看</el-button></template></el-table-column></el-table>
    </el-card>
  </div>
</template>
<script setup>
import {ref,reactive,computed,onMounted,onUnmounted} from 'vue'
import {useRoute,useRouter} from 'vue-router'
import {UploadFilled} from '@element-plus/icons-vue'
import {getTask,listTasks,beginUpload,resumeUpload,sendPart,completeUpload,cancelTask,retryTask,capabilities} from '../api/task3'
import {downloadStandard} from '../api/task2'
import {hashParts,uploadParts} from '../services/chunkUpload'
import {createTaskPolling,terminalStatuses} from '../services/taskPolling'
const route=useRoute(),router=useRouter(),fileList=ref([]),runExtraction=ref(false),uploading=ref(false),task=ref(null),tasks=ref([]),error=ref(''),uploadPhase=ref('等待选择文件'),uploadPercent=ref(0),caps=ref({}),budget=reactive({max_calls:8,max_input_tokens:64000,max_output_tokens:32768})
let controller,requestKey='',receiptTask=''
const receiving=computed(()=>task.value?.status==='receiving')
const statusText=status=>({receiving:'接收中',queued:'等待处理',running:'处理中',validating:'验收中',validated_only:'仅解析完成（无实体）',completed:'处理完成',partial:'部分成功',failed:'失败',cancelled:'已取消',needs_confirmation:'需管理员确认'}[status]||status)
const polling=createTaskPolling({fetch:getTask,onResult:value=>{task.value=value;if(terminalStatuses.has(value.status))loadTasks()},onError:e=>{error.value='进度连接中断：'+e.message+'；任务可能仍在服务器运行，请重新连接。'}})
function saveReceipt(){sessionStorage.setItem('task3_upload_receipt',JSON.stringify({request_key:requestKey,task_id:receiptTask,mode:runExtraction.value?'extract':'parse_only'}))}
async function loadTasks(){try{tasks.value=(await listTasks()).items}catch(e){error.value=e.message}}
async function attach(row){task.value=row;receiptTask=row.task_id;try{if(row.status==='receiving'){const session=await resumeUpload(row.task_id);requestKey=session.request_key;runExtraction.value=row.mode==='extract';saveReceipt()}await router.replace({path:route.path,query:{...route.query,task_id:row.task_id}});polling.start(row.task_id)}catch(e){error.value=e.message}}
function reconnect(){error.value='';if(task.value)polling.start(task.value.task_id)}
function newBatch(){polling.stop();task.value=null;requestKey='';receiptTask='';sessionStorage.removeItem('task3_upload_receipt');uploadPercent.value=0}
async function submit(){error.value='';uploading.value=true;controller=new AbortController();try{
  const settings=await capabilities();caps.value=settings
  const files=fileList.value.map(entry=>entry.raw),total=files.reduce((sum,file)=>sum+file.size,0)
  if(total>settings.received_bytes || files.some(file=>file.size>settings.file_bytes))throw new Error('文件超过服务器总量/单文件限制')
  const specs=[];let hashed=0
  for(const file of files){uploadPhase.value='正在分片校验 '+file.name;specs.push(await hashParts(file,settings.chunk_bytes,{signal:controller.signal,onProgress:value=>{uploadPercent.value=Math.round((hashed+value.hashed_bytes)/total*100)}}));hashed+=file.size}
  requestKey=requestKey||globalThis.crypto.randomUUID()
  const session=await beginUpload({request_key:requestKey,files:specs,mode:runExtraction.value?'extract':'parse_only',...(runExtraction.value?{budget:{...budget}}:{})})
  receiptTask=session.job.task_id;task.value=session.job;saveReceipt()
  await router.replace({path:route.path,query:{...route.query,task_id:receiptTask}})
  uploadPhase.value='传输分片（已确认部分自动跳过）'
  const value=await uploadParts(session,files,{signal:controller.signal,sendPart,complete:completeUpload,onProgress:value=>{uploadPercent.value=Math.round(value.uploaded_bytes/value.total_bytes*100)}})
  task.value=value;uploadPhase.value='上传确认完成；处理进度单独显示';polling.start(value.task_id);await loadTasks()
}catch(e){error.value=e.message;uploadPhase.value='尚未确认完成；保留服务器断点'}finally{uploading.value=false}}
async function cancel(){try{task.value=await cancelTask(task.value.task_id);reconnect()}catch(e){error.value=e.message}}
async function retry(){try{task.value=await retryTask(task.value.task_id);reconnect()}catch(e){error.value=e.message}}
function openResults(){router.push({path:'/task1/entities',query:{dataset_id:task.value.upload_dataset_id}})}
async function download(name){try{await downloadStandard(task.value.upload_dataset_id,name)}catch(e){error.value=e.message}}
onMounted(async()=>{try{caps.value=await capabilities()}catch(e){error.value=e.message}try{const receipt=JSON.parse(sessionStorage.getItem('task3_upload_receipt')||'null');requestKey=receipt?.request_key||'';receiptTask=route.query.task_id||receipt?.task_id||'';runExtraction.value=receipt?.mode==='extract';if(receiptTask){task.value=await getTask(receiptTask);polling.start(receiptTask)}}catch(e){error.value=e.message}await loadTasks()})
onUnmounted(()=>{controller?.abort();polling.stop()})
</script>
<style scoped>.mt-16{margin-top:16px}p{color:#526078;line-height:1.7}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f7fa;padding:12px}.el-button{margin-top:10px}</style>
