<template>
  <div v-loading="loading">
    <el-alert title="口径说明：按原文完整名称检索，不代表跨公告法人合并。原文记录数、已去重交易次数分别显示；未确认身份、金额或投标名单完整性时保留未知，不填0。" type="info" :closable="false" show-icon />
    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" class="mt-16" />
    <el-card class="mt-16" shadow="never">
      <template #header><span>任务二 · 五类关系查询</span><el-tag class="version">{{ status?.source_count || 0 }}份公告</el-tag></template>
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
        <el-select v-model="scopeLevel" class="scope"><el-option label="项目归口" value="project" /><el-option label="明确采购包归口" value="event" /></el-select>
        <el-button type="primary" :disabled="!status || (multiple ? selectors.length < 2 : !selector)" @click="search">查询</el-button>
      </div>
      <p class="version-text">事实版本：{{ status?.dataset_id || '尚未发布' }}</p>
    </el-card>
    <template v-if="result">
      <el-card shadow="never" class="mt-16">
        <template #header><span>查询结果（{{ result.total }} 条）</span><span v-if="result.project_count !== undefined" class="version">已观察项目 {{ result.project_count }} 个，非跨公告去重统计</span></template>
        <el-table :data="result.items" stripe empty-text="没有符合原文证据条件的记录；不代表实际不存在合作或竞标。">
          <el-table-column :label="type === 'common_projects' ? '项目名称' : '主体名称'" min-width="240"><template #default="{ row }">{{ row.name || row.project_name }}</template></el-table-column>
          <el-table-column v-if="type === 'buyer_suppliers'" label="角色" width="135"><template #default="{ row }">{{ row.role === 'product_supplier' ? '明示产品供应商' : '中标竞标主体' }}</template></el-table-column>
          <el-table-column v-if="type !== 'common_buyers'" prop="announcement_count" label="原文公告数" width="120" />
          <el-table-column v-if="!type.startsWith('common')" label="已去重交易次数" width="145"><template #default="{ row }">{{ display(row.transaction_count) }}</template></el-table-column>
          <el-table-column v-if="type !== 'common_buyers'" label="可确认金额（元）" width="165"><template #default="{ row }">{{ display(type === 'common_projects' ? row.project_amount_cny : row.amount_cny) }}</template></el-table-column>
          <el-table-column v-if="type === 'common_projects'" label="各主体竞标结果" min-width="220"><template #default="{ row }"><div v-for="(v,k) in row.results" :key="k">{{ k }}：{{ outcomes[v] || v }}</div></template></el-table-column>
          <el-table-column v-if="type === 'common_buyers'" label="各供应商的合作记录" min-width="280"><template #default="{ row }"><div v-for="(v,k) in row.by_supplier" :key="k">{{ k }}：公告 {{ v.announcement_count }}；去重交易 {{ display(v.transaction_count) }}；金额 {{ display(v.amount_cny) }} 元</div></template></el-table-column>
          <el-table-column label="原文依据" width="115"><template #default="{ row }"><el-button link type="primary" @click="rowEvidence(row)">查看证据</el-button></template></el-table-column>
        </el-table>
        <el-pagination class="mt-16" layout="total, prev, pager, next" :total="Math.min(result.total, 10000)" :page-size="pageSize" v-model:current-page="page" @current-change="run" />
        <p v-if="result.result_truncated" class="explanation">结果超过10000条展示上限，请缩小查询范围。</p>
      </el-card>
      <el-card v-if="result.top5" shadow="never" class="mt-16">
        <template #header>高频竞标主体 TOP5（原文公告频次，不把中标记录自动当作竞标）</template>
        <el-table :data="result.top5"><el-table-column prop="name" label="竞标主体" /><el-table-column prop="announcement_count" label="公告频次" width="120" /><el-table-column label="本主体可确认成交金额（元）"><template #default="{ row }">{{ display(row.amount_cny) }}</template></el-table-column><el-table-column label="依据"><template #default="{ row }"><el-button link @click="rowEvidence(row)">原文</el-button></template></el-table-column></el-table>
      </el-card>
      <el-card v-if="result.combinations" shadow="never" class="mt-16">
        <template #header>已披露竞标组合（完整主体集合，不推断围标）</template>
        <el-table :data="result.combinations"><el-table-column label="竞标组合"><template #default="{ row }">{{ row.names.join(' / ') }}</template></el-table-column><el-table-column prop="announcement_count" label="公告频次" width="120" /><el-table-column label="名单覆盖" width="130"><template #default="{ row }">{{ row.roster_complete ? '原文证明完整' : '仅已知披露部分' }}</template></el-table-column></el-table>
        <el-pagination layout="total, prev, pager, next" :total="result.combinations_total" :page-size="pageSize" v-model:current-page="secondaryPage" @current-change="run" />
      </el-card>
      <el-card v-if="result.nonwinning_participants" shadow="never" class="mt-16">
        <template #header>明确未中标的投标参与方</template>
        <el-table :data="result.nonwinning_participants" empty-text="未发现能明确证明未中标的记录，未知结果不推断为落标。"><el-table-column prop="name" label="主体" /><el-table-column prop="announcement_count" label="公告数" /></el-table>
        <el-pagination layout="total, prev, pager, next" :total="result.nonwinning_participants_total" :page-size="pageSize" v-model:current-page="secondaryPage" @current-change="run" />
        <p class="explanation">另有 {{ result.unknown_result_count }} 条竞标结果未知记录。列表按已知参与方独立分页，不宣称原始投标名单完整。</p>
      </el-card>
      <el-card shadow="never" class="mt-16">
        <template #header>原文关系图 · 点击关系线查看证据</template>
        <el-alert v-if="result.graph.truncated" title="关系图已按100节点/200边截取；完整结果请用表格分页查看。" type="warning" :closable="false" />
        <div class="graph-scroll"><svg v-if="nodes.length" :viewBox="`0 0 1120 ${graphHeight}`" role="img" aria-label="已验证关系图">
          <g v-for="edge in result.graph.edges" :key="edge.id" tabindex="0" role="button" :aria-label="edgeLabels(edge.kind)" @click="openEvidence(edge.evidence_ids)" @keydown.enter="openEvidence(edge.evidence_ids)">
            <line :x1="positions[edge.from].x" :y1="positions[edge.from].y" :x2="positions[edge.to].x" :y2="positions[edge.to].y" stroke="#90a4bd" stroke-width="2" />
            <text :x="(positions[edge.from].x+positions[edge.to].x)/2" :y="(positions[edge.from].y+positions[edge.to].y)/2-5" class="edge-label">{{ edgeLabels(edge.kind) }}</text>
          </g>
          <g v-for="node in nodes" :key="node.id" :transform="`translate(${positions[node.id].x},${positions[node.id].y})`" tabindex="0" role="button" @click="openEvidence(node.evidence_ids)" @keydown.enter="openEvidence(node.evidence_ids)">
            <rect x="-115" y="-19" width="230" height="38" rx="7" :fill="colors[node.kind]" /><text text-anchor="middle" y="4" class="node-label">{{ node.label.length > 16 ? node.label.slice(0,16)+'…' : node.label }}</text><title>{{ node.label }}；{{ node.identity_status }}</title>
          </g>
        </svg><el-empty v-else description="没有可显示的已验证关系" /></div>
      </el-card>
    </template>
    <el-drawer v-model="drawer" title="原文证据（与查询固定同一版本）" size="55%">
      <el-select v-model="selectedEvidence" placeholder="选择来源证据" @change="readEvidence" style="width:100%"><el-option v-for="id in evidenceIds" :key="id" :label="id" :value="id" /></el-select>
      <div v-loading="evidenceLoading" v-if="evidence" class="mt-16"><p>公告：{{ evidence.announcement_id }}</p><p>HTML SHA256：<code>{{ evidence.html_sha256 }}</code></p><p>位置：表 {{ evidence.locator?.table }} / 行 {{ evidence.locator?.row }} / 单元格 {{ evidence.locator?.cell }}；字符 {{ evidence.locator?.start }}–{{ evidence.locator?.end }}</p><h4>原文摘录</h4><pre>{{ evidence.raw_quote }}</pre><p>{{ evidence.header_context }}</p><p class="version-text">{{ evidence.dataset_id }}</p></div>
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { task2Status, organizationChoices, relationshipQuery, sourceEvidence } from '../api/task2'
const scenarios=[
  {value:'buyer_suppliers',label:'① 合作供应商',description:'查询采购单位的中标供应商和原文明确的制造商/产品供应商；包金额不复制给制造商。'},
  {value:'buyer_bidders',label:'② 高频投标与组合',description:'查询历史竞标主体TOP5及已披露完整组合。项目归口的同项目参与，不等于同包竞争。'},
  {value:'supplier_peers',label:'③ 共同竞标主体',description:'查询中标供应商历次有竞标证据的项目，区分共同竞标主体、明确未中标参与方和未知结果。'},
  {value:'common_buyers',label:'④ 共同采购单位',description:'多家供应商分别与同一采购人合作即可；不要求同项目共同中标。'},
  {value:'common_projects',label:'⑤ 共同竞标项目',description:'要求各主体都有竞标证据，展示各自结果；不是“共同中标”项目。未明确项目成交总额时金额留未知。'},
]
const type=ref('buyer_suppliers'),scopeLevel=ref('project'),selector=ref(''),selectors=ref([]),choices=ref([]),status=ref(null),result=ref(null),error=ref(''),loading=ref(false),choicesLoading=ref(false),page=ref(1),pageSize=30
const scenario=computed(()=>scenarios.find(s=>s.value===type.value)),buyerMode=computed(()=>type.value.startsWith('buyer')),multiple=computed(()=>type.value.startsWith('common'))
let generation=0,choiceGeneration=0
const secondaryPage=ref(1)
const display=v=>v === null || v === undefined ? '未知/未确认' : v
const outcomes={won:'中标',lost:'明确未中标',unknown:'未知',mixed:'多个包结果不同'}
async function loadChoices(term='') { const token=++choiceGeneration;choicesLoading.value=true;try {const data=await organizationChoices({role:buyerMode.value?'buyer':'winner',term,dataset_id:status.value?.dataset_id});if(token===choiceGeneration)choices.value=data.items} catch(e){if(token===choiceGeneration)ElMessage.error(e.message)}finally{if(token===choiceGeneration)choicesLoading.value=false} }
async function changeScenario(){generation++;loading.value=false;result.value=null;selector.value='';selectors.value=[];page.value=1;secondaryPage.value=1;await loadChoices('')}
function search(){page.value=1;secondaryPage.value=1;run()}
async function run(){const token=++generation;loading.value=true;error.value='';try{const data=await relationshipQuery({query_type:type.value,selector:selector.value,selectors:selectors.value,scope_level:scopeLevel.value,dataset_id:status.value.dataset_id,page:page.value,page_size:pageSize,secondary_page:secondaryPage.value,secondary_page_size:pageSize});if(token===generation)result.value=data}catch(e){if(token===generation)error.value=e.message}finally{if(token===generation)loading.value=false}}
const drawer=ref(false),evidenceIds=ref([]),selectedEvidence=ref(''),evidence=ref(null),evidenceLoading=ref(false)
let evidenceGeneration=0,evidenceVersion=''
function rowEvidence(row){const ids=new Set(row.evidence_ids||[]);Object.values(row.by_supplier||{}).forEach(x=>(x.evidence_ids||[]).forEach(id=>ids.add(id)));openEvidence([...ids])}
function openEvidence(ids){if(!ids?.length){ElMessage.info('这条记录没有可显示的证据');return}evidenceVersion=result.value.dataset_id;evidenceIds.value=ids;selectedEvidence.value=ids[0];drawer.value=true;readEvidence()}
async function readEvidence(){const token=++evidenceGeneration;evidence.value=null;evidenceLoading.value=true;try{const value=await sourceEvidence(selectedEvidence.value,evidenceVersion);if(token===evidenceGeneration)evidence.value=value}catch(e){if(token===evidenceGeneration)ElMessage.error(e.message)}finally{if(token===evidenceGeneration)evidenceLoading.value=false}}
const nodes=computed(()=>result.value?.graph.nodes||[])
const colors={project:'#d9eafa',event:'#dff2e6',bid_subject:'#ffebcc',organization:'#e9e1f4'}
const positions=computed(()=>{const pos={},counts={};const columns={project:145,event:415,bid_subject:685,organization:955};nodes.value.forEach(n=>{const i=counts[n.kind]||0;pos[n.id]={x:columns[n.kind]||145,y:50+i*65};counts[n.kind]=i+1});return pos})
const graphHeight=computed(()=>Math.max(220,...Object.values(positions.value).map(p=>p.y+40)))
const edgeLabels=k=>({procured_by:'采购单位',package_of:'所属项目',individual_subject:'法人主体',consortium_member:'联合体成员',awarded_to:'中标',product_supplier:'明示产品供应商',award_only_won:'仅中标证据',bid_participation_won:'竞标·中标',bid_participation_lost:'竞标·未中标',bid_participation_unknown:'竞标·结果未知'}[k]||k)
onMounted(async()=>{loading.value=true;try{status.value=await task2Status();await loadChoices('')}catch(e){error.value=e.message}finally{loading.value=false}})
</script>

<style scoped>
.mt-16{margin-top:16px}.version{float:right}.version-text{font-size:12px;color:#667085;overflow-wrap:anywhere}.query-form{display:flex;gap:12px;flex-wrap:wrap}.actor{width:min(100%,560px)}.scope{width:185px}.explanation{color:#526078;font-size:14px;line-height:1.7}.graph-scroll{overflow:auto;max-height:680px}.graph-scroll svg{min-width:900px;width:100%}.graph-scroll g[role=button]{cursor:pointer}.graph-scroll g[role=button]:focus{outline:2px solid #2879ce}.edge-label{font-size:10px;fill:#45536a}.node-label{font-size:12px;fill:#23324a}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f7fa;padding:16px;line-height:1.8}code{overflow-wrap:anywhere}
</style>
