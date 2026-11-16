<template>
  <section class="page" data-module="dependency">
    <header class="page-head">
      <div>
        <h2>依赖解析管理</h2>
        <p class="page-desc">管理ETL作业间的依赖关系，定义时间偏移、触发条件和等待策略。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记依赖关系</button>
        <button class="btn" type="button" @click="exportRows">导出依赖解析清单</button>
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
          <td :colspan="columns.length + 1" class="empty-state">暂无依赖解析数据，可先登记依赖关系</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条依赖解析记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <h3 class="section-title">待复核关系（读取影响对象数据清单，跨模块同一版本来源）</h3>
    <table class="data-table">
      <thead>
        <tr>
          <th>关系编号</th>
          <th>表编号/表名</th>
          <th>影响原因</th>
          <th>过期字段</th>
          <th>版本来源</th>
          <th>确认状态/结构</th>
          <th>待复核标志</th>
          <th>最后一版元数据</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in reviewRows" :key="`review-${row.id}`">
          <td>{{ row['关系编号'] }}</td>
          <td>{{ row['表编号'] }}<br /><span class="muted-text">{{ row['表名'] }}</span></td>
          <td>{{ row['影响原因'] }}</td>
          <td>
            <div v-for="field in row['过期字段列表']" :key="field.field" class="field-line">
              <strong>{{ field.field }}</strong>
              <span class="muted-text">（{{ field['问题说明'] }}）</span>
            </div>
          </td>
          <td>v{{ row['版本来源号'] }}</td>
          <td>{{ row['元数据确认状态'] }} / {{ row['结构一致性'] }}</td>
          <td>
            <span :class="row['待复核标志'] ? 'flag-pending' : 'flag-done'">
              {{ row['待复核标志'] ? '待复核' : '已复核' }}
            </span>
          </td>
          <td>
            <button class="link" type="button" @click="openLastVersion(row)">查看最后一版</button>
          </td>
        </tr>
        <tr v-if="!reviewRows.length">
          <td colspan="8" class="empty-state">暂无待复核关系，请先在「影响范围重算」页对过期批次发起重算</td>
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
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>
type AnyRow = Record<string, any>

const ENDPOINT = '/api/dependency'
const columns = ["关系编号", "上游作业", "下游作业", "依赖类型", "偏移量分钟", "触发条件", "等待超时", "跳过标志"]
const actions = ["提交关系评审", "生效关系", "解除关系"]
const statuses = ["草稿", "关系评审中", "已生效", "已解除"]
const stats = [{"label": "依赖关系总数", "value": 0}, {"label": "已生效关系", "value": 0}, {"label": "评审中关系", "value": 0}]

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
  errorMessage.value = '依赖关系登记入口尚未接入审批流'
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    if (!response.ok) {
      throw new Error('依赖解析动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '依赖解析操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('依赖关系列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '依赖解析列表读取失败'
  }
}

const reviewRows = ref<AnyRow[]>([])
const lastVersion = ref<AnyRow | null>(null)

async function reloadReviewQueue() {
  try {
    const response = await request(`${ENDPOINT}/review-queue?size=100`)
    if (!response.ok) return
    const payload = await response.json()
    reviewRows.value = payload.items ?? []
  } catch {
    reviewRows.value = []
  }
}

function openLastVersion(row: AnyRow) {
  lastVersion.value = (row['最后一版元数据'] as AnyRow) ?? null
}

onMounted(() => {
  void reload()
  void reloadReviewQueue()
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
.flag-pending {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 10px;
  background: #fff1f0;
  color: #b42318;
  font-size: 12px;
}
.flag-done {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 10px;
  background: #ecfdf3;
  color: #027a48;
  font-size: 12px;
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
