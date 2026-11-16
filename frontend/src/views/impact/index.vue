<template>
  <section class="page" data-module="impact">
    <header class="page-head">
      <div>
        <h2>元数据影响范围重算</h2>
        <p class="page-desc">
          从元数据过期批次与依赖字段数据重算影响对象清单；同表影响对象、确认状态与表结构统一来自同一版本来源。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" @click="exportRows">导出影响对象清单</button>
      </div>
    </header>

    <!-- 概览看板：按影响原因排列 -->
    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">影响对象总数</span>
        <strong class="stat-value">{{ board['影响对象总数'] ?? 0 }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">待复核总数</span>
        <strong class="stat-value">{{ board['待复核总数'] ?? 0 }}</strong>
      </article>
      <article v-for="group in board['按影响原因'] ?? []" :key="group['影响原因']" class="stat-card">
        <span class="stat-label">{{ group['影响原因'] }}（待复核 {{ group['待复核数'] }}）</span>
        <strong class="stat-value">{{ group['影响对象数'] }}</strong>
      </article>
    </div>

    <!-- 过期批次与重算控制 -->
    <h3 class="section-title">过期批次与重算</h3>
    <table class="data-table">
      <thead>
        <tr>
          <th>批次编号</th>
          <th>批次状态</th>
          <th>涉及表</th>
          <th>重算进度</th>
          <th>最后完整表</th>
          <th>影响对象数</th>
          <th>最近重算时间</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="batch in batches" :key="batch.id">
          <td>{{ batch['批次编号'] }}</td>
          <td>{{ batch['批次状态'] }}</td>
          <td>{{ (batch['涉及表编号'] ?? []).join('、') }}</td>
          <td>{{ Math.round((batch['重算进度'] ?? 0) * 100) }}%</td>
          <td>{{ batch['最后完整表'] || '—' }}</td>
          <td>{{ batch['影响对象数'] }}</td>
          <td>{{ batch['最近重算时间'] || '—' }}</td>
          <td class="row-actions">
            <button class="link" type="button" @click="recompute(batch.id)">刷新重算</button>
            <button class="link" type="button" @click="recomputeWithBreak(batch.id)">演练中断续算</button>
          </td>
        </tr>
      </tbody>
    </table>

    <!-- 元数据版本与确认 -->
    <h3 class="section-title">元数据版本（重新确认后受影响对象按新字段结构重算）</h3>
    <table class="data-table">
      <thead>
        <tr>
          <th>版本号</th>
          <th>表编号</th>
          <th>表名</th>
          <th>数仓层</th>
          <th>确认状态</th>
          <th>字段结构</th>
          <th>版本说明</th>
          <th>确认时间</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="version in versions" :key="version.id">
          <td>v{{ version.version_no }}</td>
          <td>{{ version['表编号'] }}</td>
          <td>{{ version['表名'] }}</td>
          <td>{{ version['所属数仓层'] }}</td>
          <td>{{ version['确认状态'] }}</td>
          <td>
            <button class="link" type="button" @click="showVersion(version)">查看最后一版</button>
          </td>
          <td>{{ version['版本说明'] }}</td>
          <td>{{ version['确认时间'] || '—' }}</td>
          <td>
            <button
              v-if="version['确认状态'] !== '已确认'"
              class="link"
              type="button"
              @click="confirmVersion(version.id)"
            >
              确认版本并重算
            </button>
            <span v-else class="muted-text">当前确认版</span>
          </td>
        </tr>
      </tbody>
    </table>

    <!-- 影响对象清单 -->
    <h3 class="section-title">影响对象数据清单（元数据存储与依赖解析同源）</h3>
    <form class="filter-bar" @submit.prevent="reloadImpacts">
      <label class="filter-item">
        <span>影响原因</span>
        <select v-model="filters.reason">
          <option value="">全部</option>
          <option v-for="reason in reasons" :key="reason" :value="reason">{{ reason }}</option>
        </select>
      </label>
      <label class="filter-item">
        <span>复核状态</span>
        <select v-model="filters.review_status">
          <option value="">全部</option>
          <option value="待复核">待复核</option>
          <option value="已复核">已复核</option>
        </select>
      </label>
      <label class="filter-item">
        <span>来源模块</span>
        <select v-model="filters.source_module">
          <option value="">全部</option>
          <option value="dependency">依赖解析</option>
          <option value="sqltpl">SQL模板</option>
        </select>
      </label>
      <label class="filter-item">
        <span>表编号</span>
        <input v-model="filters.table_code" placeholder="如 META-1001" />
      </label>
      <label class="filter-item">
        <span>关键字</span>
        <input v-model="filters.keyword" placeholder="对象编号/目标对象" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th>批次编号</th>
          <th>表编号/表名</th>
          <th>来源模块</th>
          <th>来源对象</th>
          <th>目标对象</th>
          <th>影响原因</th>
          <th>过期字段</th>
          <th>版本来源</th>
          <th>确认状态/结构</th>
          <th>复核状态</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in impacts" :key="row.id">
          <td>{{ row['批次编号'] }}</td>
          <td>{{ row['表编号'] }}<br /><span class="muted-text">{{ row['表名'] }}</span></td>
          <td>{{ sourceLabel(row['来源模块']) }}</td>
          <td>{{ row['来源对象编号'] }}</td>
          <td>{{ row['目标对象'] }}</td>
          <td><span class="reason-tag">{{ row['影响原因'] }}</span></td>
          <td>
            <div v-for="field in row['过期字段列表']" :key="field.field" class="field-line">
              <strong>{{ field.field }}</strong>
              <span class="muted-text">（{{ field['旧别名'] }} → {{ field['新别名'] || '已删除' }}）</span>
            </div>
          </td>
          <td>v{{ row['版本来源号'] }}</td>
          <td>{{ row['元数据确认状态'] }}<br /><span class="muted-text">{{ row['结构一致性'] }}</span></td>
          <td>{{ row['复核状态'] }}</td>
          <td>
            <button
              v-if="row['复核状态'] === '待复核'"
              class="link"
              type="button"
              @click="review(row.id)"
            >
              标记已复核
            </button>
            <span v-else class="muted-text">已完成</span>
          </td>
        </tr>
        <tr v-if="!impacts.length">
          <td colspan="11" class="empty-state">暂无影响对象，请先在上方对过期批次发起重算</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ impactTotal }} 条影响对象记录</span>
      <span v-if="message" class="error-text">{{ message }}</span>
    </footer>

    <!-- 最后一版元数据详情 -->
    <div v-if="detailVersion" class="modal-mask" @click.self="detailVersion = null">
      <div class="modal-card">
        <header class="modal-head">
          <strong>{{ detailVersion['表名'] }} 最后一版元数据（v{{ detailVersion.version_no }}）</strong>
          <button class="link" type="button" @click="detailVersion = null">关闭</button>
        </header>
        <p class="muted-text">{{ detailVersion['版本说明'] }} · 确认状态：{{ detailVersion['确认状态'] }}</p>
        <table class="data-table">
          <thead>
            <tr><th>字段名</th><th>字段别名</th><th>字段类型</th></tr>
          </thead>
          <tbody>
            <tr v-for="field in detailVersion['字段清单']" :key="field.field">
              <td>{{ field.field }}</td>
              <td>{{ field.alias }}</td>
              <td>{{ field.data_type }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'

import { request } from '@/api/client'

type Board = Record<string, any>
type Row = Record<string, any>

const route = useRoute()
const reasons = ['字段删除', '字段改名', '结构不一致', '模板别名失效', '新版本待确认']

const batches = ref<Row[]>([])
const versions = ref<Row[]>([])
const impacts = ref<Row[]>([])
const impactTotal = ref(0)
const board = ref<Board>({})
const detailVersion = ref<Row | null>(null)
const message = ref('')

const filters = reactive<Record<string, string>>({
  reason: '',
  review_status: '',
  source_module: '',
  table_code: typeof route.query.table_code === 'string' ? route.query.table_code : '',
  keyword: '',
})

function sourceLabel(module: string): string {
  if (module === 'dependency') return '依赖解析'
  if (module === 'sqltpl') return 'SQL模板'
  return module
}

async function loadBoard() {
  const response = await request('/api/impact/board')
  if (response.ok) board.value = await response.json()
}

async function loadBatches() {
  const response = await request('/api/impact/batches')
  if (response.ok) {
    const payload = await response.json()
    batches.value = payload.items ?? []
  }
}

async function loadVersions() {
  const response = await request('/api/impact/versions?size=100')
  if (response.ok) {
    const payload = await response.json()
    versions.value = payload.items ?? []
  }
}

async function recompute(batchId: number) {
  message.value = ''
  const response = await request(`/api/impact/batches/${batchId}/recompute`, {
    method: 'POST',
    body: JSON.stringify({ values: {} }),
  })
  const payload = await response.json()
  if (!response.ok) {
    message.value = payload.detail ?? '重算未完成'
  } else {
    message.value = payload.message
  }
  await refreshAll()
}

async function recomputeWithBreak(batchId: number) {
  message.value = ''
  // 第一次调用只处理一张表即中断，检查点保留；再发起一次普通重算完成续算。
  const first = await request(`/api/impact/batches/${batchId}/recompute`, {
    method: 'POST',
    body: JSON.stringify({ values: { fail_after_tables: 1 } }),
  })
  const firstPayload = await first.json()
  await refreshAll()
  const second = await request(`/api/impact/batches/${batchId}/recompute`, {
    method: 'POST',
    body: JSON.stringify({ values: {} }),
  })
  const secondPayload = await second.json()
  message.value = `中断：${firstPayload.detail ?? '已落盘'}；恢复：${secondPayload.message ?? ''}`
  await refreshAll()
}

async function confirmVersion(versionId: number) {
  message.value = ''
  const response = await request(`/api/impact/versions/${versionId}/confirm`, { method: 'POST' })
  const payload = await response.json()
  message.value = payload.message ?? '版本确认失败'
  await refreshAll()
}

async function review(impactId: number) {
  message.value = ''
  const response = await request(`/api/impact/objects/${impactId}/review`, {
    method: 'POST',
    body: JSON.stringify({ values: { remark: '人工复核通过' } }),
  })
  const payload = await response.json()
  message.value = payload.message
  await refreshAll()
}

function showVersion(version: Row) {
  detailVersion.value = version
}

function resetFilters() {
  filters.reason = ''
  filters.review_status = ''
  filters.source_module = ''
  filters.table_code = ''
  filters.keyword = ''
  void reloadImpacts()
}

async function reloadImpacts() {
  message.value = ''
  const params = new URLSearchParams()
  Object.entries(filters).forEach(([key, value]) => {
    if (value) params.set(key, value)
  })
  const response = await request(`/api/impact/objects?${params.toString()}`)
  if (!response.ok) {
    message.value = '影响对象清单读取失败'
    return
  }
  const payload = await response.json()
  impacts.value = payload.items ?? []
  impactTotal.value = payload.total ?? 0
}

async function refreshAll() {
  await Promise.all([loadBoard(), loadBatches(), loadVersions(), reloadImpacts()])
}

function exportRows() {
  window.open('/api/impact/export', '_blank')
}

onMounted(refreshAll)
</script>

<style scoped>
.section-title {
  margin: 18px 0 8px;
  font-size: 15px;
}
.muted-text {
  color: var(--muted);
  font-size: 12px;
}
.reason-tag {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 10px;
  background: #eef4ff;
  color: var(--brand);
  font-size: 12px;
}
.field-line {
  font-size: 12px;
  line-height: 1.6;
}
.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 20;
}
.modal-card {
  width: 640px;
  max-height: 70vh;
  overflow: auto;
  background: #fff;
  border-radius: 10px;
  padding: 16px 18px;
}
.modal-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}
</style>
