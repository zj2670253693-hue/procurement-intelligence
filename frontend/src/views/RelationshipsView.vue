<template>
  <div ref="root" v-loading="loading" data-query-render-root>
    <el-alert title="只展示公告能确认的信息。未说明的金额和投标结果会标为“未明确”，不会自动补成零或未中标。" type="info" :closable="false" show-icon />
    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" class="mt-16" />
    <el-card class="mt-16" shadow="never">
      <template #header><span>供应商与采购关系</span><el-tag class="version">{{ status?.source_count || 0 }}份公告</el-tag></template>
      <el-tabs v-model="type" @tab-change="changeScenario">
        <el-tab-pane v-for="s in scenarios" :key="s.value" :name="s.value" :label="s.label" />
      </el-tabs>
      <p class="explanation">{{ scenario.description }}</p>
      <div class="query-form">
        <el-select v-if="!multiple" v-model="selector" filterable remote :remote-method="loadChoices" :loading="choicesLoading" :placeholder="buyerMode ? '输入采购单位完整名称' : '输入中标供应商完整名称'" class="actor">
          <el-option v-for="o in choices" :key="o.value" :label="o.label" :value="o.value" />
        </el-select>
        <el-select v-else v-model="selectors" multiple filterable remote :remote-method="loadChoices" :loading="choicesLoading" :multiple-limit="10" placeholder="选择2至10家中标供应商" class="actor">
          <el-option v-for="o in choices" :key="o.value" :label="o.label" :value="o.value" />
        </el-select>
        <el-select v-model="scopeLevel" class="scope"><el-option label="按项目查看" value="project" /><el-option label="按采购包查看" value="event" /></el-select>
        <el-button type="primary" :disabled="!status || (multiple ? selectors.length < 2 : !selector)" @click="search">查询</el-button>
      </div>
      <details class="query-technical"><summary>统计口径与技术信息</summary>
        <p>按公告中的完整名称查询，不自动合并跨公告的同名公司。公告数量与已确认的独立交易次数分别统计，缺失信息不按零计算。</p>
        <p>同项目参与不等于同采购包竞争；采购包金额不计入制造商的成交额。投标名单以原文披露范围为准。</p>
        <p class="version-text">数据版本：{{ status?.dataset_id || '尚未发布' }}</p>
        <p v-if="lastSample" class="version-text">页面渲染耗时：{{ lastSample.elapsed_ms.toFixed(1) }} ms（包含表格、分页和关系图，不是纯接口耗时）</p>
      </details>
    </el-card>
    <template v-if="result">
      <el-card shadow="never" class="mt-16">
        <template #header><span>查询结果（{{ result.total }} 条）</span><span v-if="result.project_count !== undefined" class="version">涉及 {{ result.project_count }} 个项目</span></template>
        <el-table :data="result.items" stripe empty-text="没有符合原文证据条件的记录；不代表实际不存在合作或竞标。">
          <el-table-column :label="type === 'common_projects' ? '项目名称' : type === 'common_buyers' ? '采购单位' : '供应商 / 投标方'" min-width="240"><template #default="{ row }">{{ row.name || row.project_name }}</template></el-table-column>
          <el-table-column v-if="type === 'buyer_suppliers'" label="角色" width="135"><template #default="{ row }">{{ row.role === 'product_supplier' ? '产品供应商' : '中标供应商' }}</template></el-table-column>
          <el-table-column v-if="type !== 'common_buyers'" prop="announcement_count" label="相关公告数" width="120" />
          <el-table-column v-if="!type.startsWith('common')" label="已确认交易次数" width="145"><template #default="{ row }">{{ display(row.transaction_count) }}</template></el-table-column>
          <el-table-column v-if="type !== 'common_buyers'" label="可确认金额（元）" width="165"><template #default="{ row }">{{ display(type === 'common_projects' ? row.project_amount_cny : row.amount_cny) }}</template></el-table-column>
          <el-table-column v-if="type === 'common_projects'" label="各投标方结果" min-width="220"><template #default="{ row }"><div v-for="(v,k) in row.results" :key="k">{{ k }}：{{ outcomes[v] || v }}</div></template></el-table-column>
          <el-table-column v-if="type === 'common_buyers'" label="各供应商的合作记录" min-width="280"><template #default="{ row }"><div v-for="(v,k) in row.by_supplier" :key="k">{{ k }}：公告 {{ v.announcement_count }}；去重交易 {{ display(v.transaction_count) }}；金额 {{ display(v.amount_cny) }} 元</div></template></el-table-column>
          <el-table-column label="信息来源" width="115"><template #default="{ row }"><el-button link type="primary" @click="rowEvidence(row)">查看来源</el-button></template></el-table-column>
        </el-table>
        <el-pagination class="mt-16" layout="total, prev, pager, next" :total="Math.min(result.total, 10000)" :page-size="pageSize" v-model:current-page="page" @current-change="run" />
        <p v-if="result.result_truncated" class="explanation">结果超过10000条展示上限，请缩小查询范围。</p>
      </el-card>
      <el-card v-if="result.top5" shadow="never" class="mt-16">
        <template #header>经常参与投标的供应商 TOP5</template>
        <el-table :data="result.top5"><el-table-column prop="name" label="投标方" /><el-table-column prop="announcement_count" label="相关公告数" width="120" /><el-table-column label="已确认成交金额（元）"><template #default="{ row }">{{ display(row.amount_cny) }}</template></el-table-column><el-table-column label="信息来源"><template #default="{ row }"><el-button link @click="rowEvidence(row)">查看来源</el-button></template></el-table-column></el-table>
      </el-card>
      <el-card v-if="result.combinations" shadow="never" class="mt-16">
        <template #header>公告中披露的投标组合</template>
        <el-table :data="result.combinations"><el-table-column label="竞标组合"><template #default="{ row }">{{ row.names.join(' / ') }}</template></el-table-column><el-table-column prop="announcement_count" label="公告频次" width="120" /><el-table-column label="名单覆盖" width="130"><template #default="{ row }">{{ row.roster_complete ? '原文证明完整' : '仅已知披露部分' }}</template></el-table-column></el-table>
        <el-pagination layout="total, prev, pager, next" :total="result.combinations_total" :page-size="pageSize" v-model:current-page="secondaryPage" @current-change="run" />
      </el-card>
      <el-card v-if="result.nonwinning_participants" shadow="never" class="mt-16">
        <template #header>明确未中标的投标参与方</template>
        <el-table :data="result.nonwinning_participants" empty-text="未发现能明确证明未中标的记录，未知结果不推断为落标。"><el-table-column prop="name" label="主体" /><el-table-column prop="announcement_count" label="公告数" /></el-table>
        <el-pagination layout="total, prev, pager, next" :total="result.nonwinning_participants_total" :page-size="pageSize" v-model:current-page="secondaryPage" @current-change="run" />
        <p class="explanation">另有 {{ result.unknown_result_count }} 条记录未说明中标结果；公告中的投标名单可能未披露完整。</p>
      </el-card>
      <el-card shadow="never" class="mt-16">
        <template #header>采购关系图 · 点击连线查看来源</template>
        <el-alert v-if="result.graph.truncated" title="关系较多，当前图中仅显示一部分；请在结果表格中翻页查看更多。" type="warning" :closable="false" />
        <RelationshipGraph :graph="result.graph" @evidence="openEvidence" />
      </el-card>
    </template>
    <el-drawer v-model="drawer" title="信息来源" size="min(680px, 100vw)">
      <EvidenceSourcePanel :ids="evidenceIds" :selected-id="selectedEvidence" :records="evidenceRecords"
        :evidence="evidence" :loading="evidenceLoading" :error="evidenceError" @select="selectEvidence" @retry="readEvidence" />
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, computed, watch, nextTick, onUnmounted } from 'vue'
import { useDataset } from '../services/useDataset'
import { createRenderMeasurement } from '../services/renderMeasurement'
import RelationshipGraph from '../components/RelationshipGraph.vue'
import EvidenceSourcePanel from '../components/EvidenceSourcePanel.vue'
const {datasetId,context}=useDataset()
const root=ref(null),lastSample=ref(null)
const meter=createRenderMeasurement({nextTick,nextFrame:()=>new Promise(resolve=>requestAnimationFrame(resolve)),isReady:()=>Boolean(root.value?.querySelector('.el-table') && root.value?.querySelector('.el-pagination') && root.value?.querySelector('.graph-scroll') && (!result.value?.graph.nodes.length || root.value.querySelector('.graph-scroll svg')))})
let resultScope='official',evidenceScope='official'
import { ElMessage } from 'element-plus'
import { task2Status, organizationChoices, relationshipQuery, sourceEvidence } from '../api/task2'
const scenarios=[
  {value:'buyer_suppliers',label:'① 合作供应商',description:'看看这家采购单位与哪些供应商有合作，以及已确认的交易次数和金额。'},
  {value:'buyer_bidders',label:'② 常见投标方',description:'看看哪些供应商经常参与这家单位的招标，以及公告中披露的投标组合。'},
  {value:'supplier_peers',label:'③ 共同投标方',description:'看看哪些公司曾与这家供应商参与同一项目，并区分已中标、未中标和结果未明确。'},
  {value:'common_buyers',label:'④ 共同采购单位',description:'选择多家供应商，找出与它们都曾合作过的采购单位，不要求在同一项目中中标。'},
  {value:'common_projects',label:'⑤ 共同投标项目',description:'选择多家供应商，查看它们共同参与过的项目和各自的投标结果。共同投标不等于共同中标。'},
]
const type=ref('buyer_suppliers'),scopeLevel=ref('project'),selector=ref(''),selectors=ref([]),choices=ref([]),status=ref(null),result=ref(null),error=ref(''),loading=ref(false),choicesLoading=ref(false),page=ref(1),pageSize=30
const scenario=computed(()=>scenarios.find(s=>s.value===type.value)),buyerMode=computed(()=>type.value.startsWith('buyer')),multiple=computed(()=>type.value.startsWith('common'))
let generation=0,choiceGeneration=0
const secondaryPage=ref(1)
const display=v=>v === null || v === undefined ? '未明确' : v
const outcomes={won:'中标',lost:'明确未中标',unknown:'未知',mixed:'多个包结果不同'}
async function loadChoices(term='') { const token=++choiceGeneration,ticket=context.ticket();choicesLoading.value=true;try {const data=await organizationChoices({role:buyerMode.value?'buyer':'winner',term,dataset_id:status.value?.dataset_id},ticket.dataset_id);if(token===choiceGeneration && context.accept(ticket))choices.value=data.items} catch(e){if(token===choiceGeneration && context.accept(ticket))ElMessage.error(e.message)}finally{if(token===choiceGeneration && context.accept(ticket))choicesLoading.value=false} }
async function changeScenario(){meter.cancel(generation);generation++;evidenceGeneration++;drawer.value=false;evidence.value=null;lastSample.value=null;loading.value=false;result.value=null;selector.value='';selectors.value=[];page.value=1;secondaryPage.value=1;await loadChoices('')}
function search(){page.value=1;secondaryPage.value=1;run()}
async function run(){const token=++generation,ticket=context.ticket();loading.value=true;error.value='';lastSample.value=null;meter.start(token,{dataset_id:ticket.dataset_id,query_type:type.value});try{const data=await relationshipQuery({query_type:type.value,selector:selector.value,selectors:selectors.value,scope_level:scopeLevel.value,dataset_id:status.value.dataset_id,page:page.value,page_size:pageSize,secondary_page:secondaryPage.value,secondary_page_size:pageSize},ticket.dataset_id);if(token!==generation || !context.accept(ticket))return;result.value=data;resultScope=ticket.dataset_id;loading.value=false;const sample=await meter.finish(token);if(sample && token===generation && context.accept(ticket)){lastSample.value=sample;globalThis.__t3RenderSamples=[...(globalThis.__t3RenderSamples||[]),sample].slice(-200)}}catch(e){meter.cancel(token);if(token===generation && context.accept(ticket))error.value=e.message}finally{if(token===generation && context.accept(ticket))loading.value=false}}
const drawer=ref(false),evidenceIds=ref([]),selectedEvidence=ref(''),evidence=ref(null),evidenceLoading=ref(false),evidenceError=ref(''),evidenceRecords=ref({})
let evidenceGeneration=0,evidenceVersion=''
function rowEvidence(row){const ids=new Set(row.evidence_ids||[]);Object.values(row.by_supplier||{}).forEach(x=>(x.evidence_ids||[]).forEach(id=>ids.add(id)));openEvidence([...ids])}
function openEvidence(ids){if(!ids?.length){ElMessage.info('这条信息暂时没有可查看的原文');return}evidenceVersion=result.value.dataset_id;evidenceScope=resultScope;evidenceIds.value=ids;evidenceRecords.value={};selectedEvidence.value=ids[0];drawer.value=true;readEvidence()}
function selectEvidence(id){selectedEvidence.value=id;readEvidence()}
async function readEvidence(){const token=++evidenceGeneration,ticket=context.ticket();evidence.value=null;evidenceError.value='';evidenceLoading.value=true;try{const value=await sourceEvidence(selectedEvidence.value,evidenceVersion,evidenceScope);if(token===evidenceGeneration && context.accept(ticket)){evidence.value=value;evidenceRecords.value={...evidenceRecords.value,[selectedEvidence.value]:value}}}catch(e){if(token===evidenceGeneration && context.accept(ticket))evidenceError.value='原文暂时无法加载，请重新加载或稍后再试。'}finally{if(token===evidenceGeneration && context.accept(ticket))evidenceLoading.value=false}}
watch(datasetId,async id=>{context.select(id);meter.cancel(generation);generation++;choiceGeneration++;evidenceGeneration++;result.value=null;status.value=null;choices.value=[];selector.value='';selectors.value=[];drawer.value=false;evidence.value=null;evidenceIds.value=[];selectedEvidence.value='';lastSample.value=null;page.value=1;secondaryPage.value=1;error.value='';loading.value=true;const ticket=context.ticket();try{const value=await task2Status(id);if(!context.accept(ticket))return;status.value=value;await loadChoices('')}catch(e){if(context.accept(ticket))error.value=e.message}finally{if(context.accept(ticket))loading.value=false}},{immediate:true})
onUnmounted(()=>{meter.cancel(generation);generation++;choiceGeneration++;evidenceGeneration++})
</script>

<style scoped>
.mt-16{margin-top:16px}.version{float:right}.version-text{font-size:12px;color:#667085;overflow-wrap:anywhere}.query-form{display:flex;gap:12px;flex-wrap:wrap}.actor{width:min(100%,560px)}.scope{width:185px}.explanation{color:#526078;font-size:14px;line-height:1.7}.query-technical{margin-top:16px;font-size:12px;color:#667085;line-height:1.8}.query-technical summary{cursor:pointer}code{overflow-wrap:anywhere}
</style>
