<template>
  <section class="source-panel" aria-label="信息来源">
    <p class="source-intro">这条信息来自公告原文，你可以在下方核对。</p>
    <label for="evidence-source-choice" class="source-label">选择来源（共 {{ ids.length }} 处）</label>
    <el-select id="evidence-source-choice" :model-value="selectedId" aria-label="选择信息来源"
      class="source-choice" @change="$emit('select', $event)">
      <el-option v-for="(id,index) in ids" :key="id" :value="id" :label="sourceOptionLabel(records[id],index+1)" />
    </el-select>
    <p v-if="loading" role="status">正在读取原文…</p>
    <div v-else-if="error" role="alert"><p>{{ error }}</p><el-button @click="$emit('retry')">重新加载</el-button></div>
    <template v-else-if="evidence">
      <div class="source-summary">
        <p><span class="source-label">来源文件</span><strong>采购公告</strong></p>
        <p v-if="sourceSection(evidence)"><span class="source-label">公告章节</span>{{ sourceSection(evidence) }}</p>
        <p><span class="source-label">公告编号</span>{{ evidence.announcement_id }}</p>
      </div>
      <h3>原文内容</h3>
      <!-- Treat source text as text, never HTML. Technical metadata stays optional. -->
      <pre class="source-quote">{{ evidence.raw_quote }}</pre>
      <details :key="evidence.evidence_id" class="source-technical">
        <summary>技术详情（用于核验）</summary>
        <p class="technical-note">以下编号和校验信息供维护、排查使用，不影响阅读原文。</p>
        <dl>
          <dt>证据编号</dt><dd><code>{{ evidence.evidence_id }}</code></dd>
          <dt>数据版本</dt><dd><code>{{ evidence.dataset_id }}</code></dd>
          <dt>文件校验值（SHA256）</dt><dd><code>{{ evidence.html_sha256 }}</code></dd>
          <dt>原文定位信息</dt><dd><code>{{ JSON.stringify(evidence.locator) }}</code></dd>
          <dt>原始章节及表头</dt><dd>{{ evidence.header_context || '未提供' }}</dd>
        </dl>
      </details>
    </template>
  </section>
</template>
<script setup>
import {sourceSection,sourceOptionLabel} from '../services/userPresentation'
defineProps({ids:{type:Array,default:()=>[]},selectedId:{type:String,default:''},
  records:{type:Object,default:()=>({})},evidence:{type:Object,default:null},
  loading:Boolean,error:{type:String,default:''}})
defineEmits(['select','retry'])
</script>
<style scoped>
.source-panel{color:#344054;font-size:14px;line-height:1.8}.source-intro{margin:0 0 20px;color:#667085}
.source-label{display:inline-block;font-weight:500;color:#667085;margin-right:16px}.source-choice{display:block;width:100%;margin:8px 0 24px}
.source-summary{padding:14px 18px;background:#f8fafc;border:1px solid #e4e7ec;border-radius:10px;overflow-wrap:anywhere}.source-summary p{margin:5px 0}.source-summary strong{color:#22304a;font-weight:500}
h3{font-size:16px;margin:24px 0 10px;color:#22304a}.source-quote{font:inherit;white-space:pre-wrap;overflow-wrap:anywhere;padding:18px 20px;background:#f3f7fc;border-left:3px solid #598aca;border-radius:4px;margin:0 0 28px}
.source-technical{border-top:1px solid #e4e7ec;padding-top:14px;color:#667085;font-size:12px}.source-technical summary{cursor:pointer;font-size:13px}.technical-note{color:#667085}dt{font-weight:500;margin-top:12px}dd{margin:3px 0;overflow-wrap:anywhere}code{font-size:12px}
</style>
