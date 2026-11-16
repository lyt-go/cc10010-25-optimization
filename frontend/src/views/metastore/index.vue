<template>
  <section class="page" data-module="metastore">
    <header class="page-head">
      <div>
        <h2>元数据存储管理</h2>
        <p class="page-desc">维护ETL涉及的元数据信息，包括表结构、分层归属和数据量级。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记元数据表</button>
        <button class="btn" type="button" @click="exportRows">导出元数据存储清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无元数据存储数据，可先登记元数据表</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条元数据存储记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <h3 class="section-title">影响对象数据清单（与依赖解析同源于同一份清单，同一版本来源）</h3>
    <form class="filter-bar" @submit.prevent="reloadImpacts">
      <label class="filter-item">
        <span>表编号</span>
        <input v-model="impactFilter.table_code" placeholder="如 META-1001" />
      </label>
      <label class="filter-item">
        <span>影响原因</span>
        <select v-model="impactFilter.reason">
          <option value="">全部</option>
          <option v-for="reason in reasons" :key="reason" :value="reason">{{ reason }}</option>
        </select>
      </label>
      <label class="filter-item">
        <span>复核状态</span>
        <select v-model="impactFilter.review_status">
          <option value="">全部</option>
          <option value="待复核">待复核</option>
          <option value="已复核">已复核</option>
        </select>
      </label>
      <button class="btn" type="submit">查询影响对象</button>
      <button class="btn ghost" type="button" @click="resetImpactFilter">重置条件</button>
    </form>
    <table class="data-table">
      <thead>
        <tr>
          <th>批次编号</th>
          <th>表编号/表名</th>
          <th>来源模块</th>
          <th>来源对象</th>
          <th>影响原因</th>
          <th>过期字段</th>
          <th>版本来源</th>
          <th>确认状态/结构</th>
          <th>复核状态</th>
          <th>最后一版</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in impactRows" :key="`impact-${row.id}`">
          <td>{{ row['批次编号'] }}</td>
          <td>{{ row['表编号'] }}<br /><span class="muted-text">{{ row['表名'] }}</span></td>
          <td>{{ sourceLabel(row['来源模块']) }}</td>
          <td>{{ row['来源对象编号'] }}</td>
          <td>{{ row['影响原因'] }}</td>
          <td>
            <div v-for="field in row['过期字段列表']" :key="field.field" class="field-line">
              <strong>{{ field.field }}</strong>
              <span class="muted-text">（{{ field['问题说明'] }}）</span>
            </div>
          </td>
          <td>v{{ row['版本来源号'] }}</td>
          <td>{{ row['元数据确认状态'] }} / {{ row['结构一致性'] }}</td>
          <td>{{ row['复核状态'] }}</td>
          <td>
            <button class="link" type="button" @click="openLastVersion(row['表编号'])">查看最后一版</button>
          </td>
        </tr>
        <tr v-if="!impactRows.length">
          <td colspan="10" class="empty-state">暂无影响对象，请先在「影响范围重算」页对过期批次发起重算</td>
        </tr>
      </tbody>
    </table>

    <div v-if="lastVersion" class="modal-mask" @click.self="lastVersion = null">
      <div class="modal-card">
        <header class="modal-head">
          <strong>{{ lastVersion['表名'] }} 最后一版元数据（v{{ lastVersion.version_no }}）</strong>
          <button class="link" type="button" @click="lastVersion = null">关闭</button>
        </header>
        <p class="muted-text">确认状态：{{ lastVersion['确认状态'] }} · {{ lastVersion['版本说明'] }}</p>
        <table class="data-table">
          <thead>
            <tr><th>字段名</th><th>字段别名</th><th>字段类型</th></tr>
          </thead>
          <tbody>
            <tr v-for="field in lastVersion['字段清单']" :key="field.field">
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

import { request } from '@/api/client'

type Row = Record<string, string | number | null>
type AnyRow = Record<string, any>

const ENDPOINT = '/api/metastore'
const columns = ["表编号", "表名", "所属数仓层", "字段数量", "分区字段", "主键字段", "数据量级", "最近更新时间"]
const actions = ["提交元数据评审", "确认元数据", "标记过期"]
const statuses = ["草稿", "元数据评审中", "已确认", "已过期"]
const stats = [{"label": "元数据表总数", "value": 0}, {"label": "已确认表", "value": 0}, {"label": "评审中表", "value": 0}]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '元数据表登记入口尚未接入审批流'
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    if (!response.ok) {
      throw new Error('元数据存储动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '元数据存储操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('元数据表列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '元数据存储列表读取失败'
  }
}

const reasons = ['字段删除', '字段改名', '结构不一致', '模板别名失效', '新版本待确认']
const impactRows = ref<AnyRow[]>([])
const lastVersion = ref<AnyRow | null>(null)
const impactFilter = reactive<Record<string, string>>({
  table_code: '',
  reason: '',
  review_status: '',
})

function sourceLabel(module: string): string {
  if (module === 'dependency') return '依赖解析'
  if (module === 'sqltpl') return 'SQL模板'
  return module
}

function resetImpactFilter() {
  impactFilter.table_code = ''
  impactFilter.reason = ''
  impactFilter.review_status = ''
  void reloadImpacts()
}

async function reloadImpacts() {
  const params = new URLSearchParams()
  Object.entries(impactFilter).forEach(([key, value]) => {
    if (value) params.set(key, value)
  })
  try {
    const response = await request(`${ENDPOINT}/impact-objects?${params.toString()}`)
    if (!response.ok) {
      impactRows.value = []
      return
    }
    const payload = await response.json()
    impactRows.value = payload.items ?? []
  } catch {
    impactRows.value = []
  }
}

async function openLastVersion(tableCode: string) {
  try {
    const response = await request(`${ENDPOINT}/${tableCode}/last-version`)
    if (!response.ok) return
    lastVersion.value = await response.json()
  } catch {
    lastVersion.value = null
  }
}

onMounted(() => {
  void reload()
  void reloadImpacts()
})
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
