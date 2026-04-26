<script setup lang="ts">
import { ref } from 'vue'

const SERVERS = [
  { label: 'ss.gkux.cn', host: 'https://ss.gkux.cn' },
  { label: 'ss.238806.xyz', host: 'https://ss.238806.xyz' },
]

const serverIdx = ref(0)
const base = () => SERVERS[serverIdx.value].host

type Tab = 'overview' | 'logs' | 'ips' | 'paths'
const activeTab = ref<Tab>('overview')

interface Overview { total_requests: number; unique_ips: number }
interface LogItem { id: number; client_ip: string; method: string; path: string; timestamp: string }
interface LogsResponse { total: number; page: number; page_size: number; items: LogItem[] }
interface IPItem { client_ip: string; location: string; isp: string; access_count: number }
interface PathItem { path: string; access_count: number }

const overview = ref<Overview | null>(null)
const logs = ref<LogsResponse | null>(null)
const ipList = ref<IPItem[]>([])
const pathList = ref<PathItem[]>([])
const loading = ref(false)
const error = ref('')
const page = ref(1)
const pageSize = 20

async function get<T>(path: string): Promise<T> {
  const res = await fetch(base() + path)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json() as T
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    if (activeTab.value === 'overview') {
      overview.value = await get<Overview>('/show/me/overview')
    } else if (activeTab.value === 'logs') {
      logs.value = await get<LogsResponse>(`/show/me/logs?page=${page.value}&page_size=${pageSize}`)
    } else if (activeTab.value === 'ips') {
      const d = await get<{ items: IPItem[] }>('/show/me/ip?limit=50')
      ipList.value = d.items
    } else {
      const d = await get<{ items: PathItem[] }>('/show/me/path?limit=50')
      pathList.value = d.items
    }
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

function tab(t: Tab) {
  activeTab.value = t
  page.value = 1
  load()
}

function prev() {
  if (page.value > 1) { page.value--; load() }
}

function next() {
  if (logs.value && page.value * pageSize < logs.value.total) { page.value++; load() }
}

load()
</script>

<template>
  <div id="app">
    <header>
      <select v-model="serverIdx" @change="load">
        <option v-for="(s, i) in SERVERS" :key="i" :value="i">{{ s.label }}</option>
      </select>
      <nav>
        <button :class="{ active: activeTab === 'overview' }" @click="tab('overview')">Overview</button>
        <button :class="{ active: activeTab === 'logs' }" @click="tab('logs')">Logs</button>
        <button :class="{ active: activeTab === 'ips' }" @click="tab('ips')">IPs</button>
        <button :class="{ active: activeTab === 'paths' }" @click="tab('paths')">Paths</button>
      </nav>
    </header>
    <main>
      <div v-if="loading">Loading...</div>
      <div v-else-if="error" class="err">{{ error }}</div>
      <template v-else>
        <section v-if="activeTab === 'overview' && overview">
          <div class="stat-grid">
            <div class="stat"><span class="lbl">Total Requests</span><span class="val">{{ overview.total_requests.toLocaleString() }}</span></div>
            <div class="stat"><span class="lbl">Unique IPs</span><span class="val">{{ overview.unique_ips.toLocaleString() }}</span></div>
          </div>
        </section>
        <section v-else-if="activeTab === 'logs' && logs">
          <p class="meta">Total {{ logs.total.toLocaleString() }} records</p>
          <table>
            <thead><tr><th>IP</th><th>Method</th><th>Path</th><th>Time</th></tr></thead>
            <tbody>
              <tr v-for="item in logs.items" :key="item.id">
                <td class="mono">{{ item.client_ip }}</td>
                <td class="mono">{{ item.method }}</td>
                <td class="mono">{{ item.path }}</td>
                <td class="mono">{{ item.timestamp.replace('T', ' ').replace('+00:00', '') }}</td>
              </tr>
            </tbody>
          </table>
          <div class="pager">
            <button @click="prev" :disabled="page <= 1">Prev</button>
            <span>Page {{ page }}</span>
            <button @click="next" :disabled="!logs || page * pageSize >= logs.total">Next</button>
          </div>
        </section>
        <section v-else-if="activeTab === 'ips'">
          <table>
            <thead><tr><th>IP</th><th>Location</th><th>ISP</th><th>Access Count</th></tr></thead>
            <tbody>
              <tr v-for="item in ipList" :key="item.client_ip">
                <td class="mono">{{ item.client_ip }}</td>
                <td>{{ item.location || '-' }}</td>
                <td>{{ item.isp || '-' }}</td>
                <td class="mono">{{ item.access_count.toLocaleString() }}</td>
              </tr>
            </tbody>
          </table>
        </section>
        <section v-else-if="activeTab === 'paths'">
          <table>
            <thead><tr><th>Path</th><th>Access Count</th></tr></thead>
            <tbody>
              <tr v-for="item in pathList" :key="item.path">
                <td class="mono">{{ item.path }}</td>
                <td class="mono">{{ item.access_count.toLocaleString() }}</td>
              </tr>
            </tbody>
          </table>
        </section>
      </template>
    </main>
  </div>
</template>
