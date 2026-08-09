<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import TreeNode from './TreeNode.vue'

const albums = ref([]), tree = ref([]), ancestors = ref([]), current = ref(null)
const leftOpen = ref(false), rightOpen = ref(false), loading = ref(false)
const query = ref(''), sort = ref('name'), order = ref('asc')
const layout = ref(localStorage.getItem('myreader-layout') || 'vertical')
const cardInfoBackground = ref(localStorage.getItem('myreader-card-info-background') || 'default')
const scanPaths = ref(''), recursive = ref(true), events = ref([])
const menu = ref(null), coverDialog = ref(null), coverOptions = ref({ items: [], albums: [] }), error = ref('')
const preview = ref(null)
const fileInput = ref(null)
const eventLabels = { info: '执行', ok: '完成', warn: '跳过', error: '错误' }

const crumbs = computed(() => [...ancestors.value, ...(current.value ? [current.value] : [])])
const thumb = album => `/api/albums/${album.id}/cover?${layout.value === 'vertical' ? 'width=300&height=400' : 'width=450&height=300'}&mode=cover&v=${album.cover_version}`
const originalCover = album => `/api/albums/${album.id}/cover/original?v=${album.cover_version}`
const formatSize = value => {
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']; let n = Number(value), i = 0
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++ }
  return `${i ? n.toFixed(n >= 10 ? 1 : 2) : n} ${units[i]}`
}
const logEvent = (type, text, front = false) => {
  const item = { type, text, time: new Date().toLocaleTimeString('zh-CN', { hour12: false }) }
  front ? events.value.unshift(item) : events.value.push(item)
}

async function api(url, options) {
  const response = await fetch(url, options)
  if (!response.ok) {
    let detail = response.statusText
    try { detail = (await response.json()).detail || detail } catch {}
    throw new Error(detail)
  }
  return response.json()
}

async function loadAlbums(parentId = current.value?.id ?? null) {
  loading.value = true; error.value = ''
  try {
    const params = new URLSearchParams({ view: 'children', q: query.value, sort: sort.value, order: order.value })
    if (parentId != null) params.set('parent_id', parentId)
    const [data, treeData] = await Promise.all([
      api(`/api/albums?${params}`),
      api(`/api/albums?view=tree&q=${encodeURIComponent(query.value)}`)
    ])
    albums.value = data.items; current.value = data.parent; ancestors.value = data.ancestors
    tree.value = treeData.tree
  } catch (e) { error.value = e.message } finally { loading.value = false }
}

function enter(album) { if (album.type === 'folder') loadAlbums(album.id) }
function goBack() { loadAlbums(ancestors.value.at(-1)?.id ?? null) }
function setLayout(value) { layout.value = value; localStorage.setItem('myreader-layout', value) }

let clickTimer
function previewAlbum(album) {
  clearTimeout(clickTimer)
  clickTimer = setTimeout(() => { menu.value = null; preview.value = album }, 260)
}
function activateAlbum(album) {
  clearTimeout(clickTimer); preview.value = null
  album.type === 'folder' ? enter(album) : openViewer(album)
}
function movePreview(step) {
  const index = albums.value.findIndex(album => album.id === preview.value?.id)
  if (index < 0 || albums.value.length < 2) return
  preview.value = albums.value[(index + step + albums.value.length) % albums.value.length]
}
function setCardInfoBackground(value) { cardInfoBackground.value = value; localStorage.setItem('myreader-card-info-background', value) }

async function initialize() {
  loading.value = true
  try { await api('/api/refresh', { method: 'POST' }) } catch (e) { error.value = e.message }
  await loadAlbums(null)
}

async function refresh() {
  loading.value = true
  try {
    const result = await api('/api/refresh', { method: 'POST' })
    logEvent(result.removed ? 'warn' : 'ok', `刷新完成：检查 ${result.checked}，移除 ${result.removed}`, true)
    await loadAlbums(current.value?.id ?? null)
  } catch (e) { error.value = e.message; logEvent('error', `刷新失败：${e.message}`, true) } finally { loading.value = false }
}

async function scan() {
  const paths = scanPaths.value.split(/\r?\n/).map(v => v.trim()).filter(Boolean)
  if (!paths.length) return
  loading.value = true; events.value = []
  try {
    const response = await fetch('/api/scans', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ paths, recursive: recursive.value })
    })
    if (!response.ok) throw new Error('扫描请求失败')
    const reader = response.body.getReader(), decoder = new TextDecoder(); let buffer = ''
    while (true) {
      const { value, done } = await reader.read(); if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n'); buffer = lines.pop()
      for (const line of lines) if (line) {
        const event = JSON.parse(line)
        if (event.type === 'started') logEvent('info', event.path)
        else if (event.type === 'skipped') logEvent('warn', `${event.path}（${event.reason}）`)
        else logEvent('ok', `登记 ${event.registered}，跳过 ${event.skipped}`)
      }
    }
    scanPaths.value = ''; await loadAlbums(current.value?.id ?? null)
  } catch (e) { error.value = e.message; logEvent('error', e.message) } finally { loading.value = false }
}

function showMenu(event, album) {
  event.preventDefault()
  menu.value = { x: Math.min(event.clientX, innerWidth - 224), y: Math.min(event.clientY, innerHeight - 250), album }
}
async function openExplorer(album) {
  menu.value = null
  try { await api('/api/explorer/open', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path: album.path, type: album.type }) }) }
  catch (e) { error.value = e.message }
}
async function openViewer(album) {
  menu.value = null
  try { await api('/api/viewer/open', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path: album.path, type: album.type }) }) }
  catch (e) { error.value = e.message }
}

async function defaultCover(album) {
  menu.value = null
  try {
    await api(`/api/albums/${album.id}/cover`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ kind: 'default' }) })
    await loadAlbums(current.value?.id ?? null)
  } catch (e) { error.value = e.message }
}

async function chooseInternal(album) {
  menu.value = null
  try { coverOptions.value = await api(`/api/albums/${album.id}/images`); coverDialog.value = album }
  catch (e) { error.value = e.message }
}

async function setInternal(entry) {
  try {
    await api(`/api/albums/${coverDialog.value.id}/cover`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ kind: 'internal', entry }) })
    coverDialog.value = null; await loadAlbums(current.value?.id ?? null)
  } catch (e) { error.value = e.message }
}

async function setChildAlbumCover(album) {
  try {
    await api(`/api/albums/${coverDialog.value.id}/cover`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ kind: 'album', source_album_id: album.id }) })
    coverDialog.value = null; await loadAlbums(current.value?.id ?? null)
  } catch (e) { error.value = e.message }
}

function chooseUpload(album) { menu.value = null; coverDialog.value = { ...album, upload: true }; nextTick(() => fileInput.value?.click()) }
async function uploadCover(event) {
  const file = event.target.files[0]; if (!file || !coverDialog.value) return
  const form = new FormData(); form.append('file', file)
  try {
    await api(`/api/albums/${coverDialog.value.id}/cover/upload`, { method: 'POST', body: form })
    coverDialog.value = null; await loadAlbums(current.value?.id ?? null)
  } catch (e) { error.value = e.message } finally { event.target.value = '' }
}

function escape() { leftOpen.value = false; rightOpen.value = false; menu.value = null; coverDialog.value = null; preview.value = null }
function closeFloating(event) { if (!event.target.closest('.context-menu')) menu.value = null }
function onKeydown(event) {
  if (preview.value && event.key === 'ArrowLeft') { event.preventDefault(); movePreview(-1) }
  else if (preview.value && event.key === 'ArrowRight') { event.preventDefault(); movePreview(1) }
  else if (preview.value && event.key === 'Enter') { event.preventDefault(); activateAlbum(preview.value) }
  else if (event.key === 'Escape') escape()
}
let searchTimer
watch(query, () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => loadAlbums(current.value?.id ?? null), 220) })
watch([sort, order], () => loadAlbums(current.value?.id ?? null))
onMounted(() => { window.addEventListener('keydown', onKeydown); window.addEventListener('click', closeFloating); initialize() })
onBeforeUnmount(() => { clearTimeout(searchTimer); clearTimeout(clickTimer); window.removeEventListener('keydown', onKeydown); window.removeEventListener('click', closeFloating) })
</script>

<template>
  <div class="app-shell">
    <button class="primary sidebar-trigger left-trigger" @click="leftOpen = !leftOpen" :title="leftOpen ? '关闭目录' : '打开目录'" :aria-label="leftOpen ? '关闭目录' : '打开目录'" :aria-expanded="leftOpen" aria-controls="library-drawer"><svg viewBox="0 0 24 24"><path d="M4 7h16M4 12h16M4 17h16" /></svg></button>
    <button class="primary sidebar-trigger right-trigger" @click="rightOpen = !rightOpen" :title="rightOpen ? '关闭设置' : '打开设置'" :aria-label="rightOpen ? '关闭设置' : '打开设置'" :aria-expanded="rightOpen" aria-controls="settings-drawer"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06-2.83 2.83-.06-.06a1.7 1.7 0 0 0-1.88-.34 1.7 1.7 0 0 0-1.03 1.56V21h-4v-.08A1.7 1.7 0 0 0 9 19.37a1.7 1.7 0 0 0-1.88.34l-.06.06-2.83-2.83.06-.06A1.7 1.7 0 0 0 4.63 15 1.7 1.7 0 0 0 3.08 14H3v-4h.08A1.7 1.7 0 0 0 4.63 9a1.7 1.7 0 0 0-.34-1.88l-.06-.06 2.83-2.83.06.06A1.7 1.7 0 0 0 9 4.63 1.7 1.7 0 0 0 10 3.08V3h4v.08A1.7 1.7 0 0 0 15 4.63a1.7 1.7 0 0 0 1.88-.34l.06-.06 2.83 2.83-.06.06A1.7 1.7 0 0 0 19.37 9 1.7 1.7 0 0 0 20.92 10H21v4h-.08A1.7 1.7 0 0 0 19.4 15Z"/></svg></button>
    <header class="topbar">
      <div class="topbar-left">
        <span class="sidebar-trigger-slot" aria-hidden="true"></span>
        <div class="location">
          <nav class="breadcrumbs" aria-label="当前位置"><button @click="loadAlbums(null)">相册</button><template v-for="crumb in crumbs" :key="crumb.id"><span>›</span><button @click="loadAlbums(crumb.id)">{{ crumb.name }}</button></template></nav>
          <div class="title-row"><button v-if="current" class="back" @click="goBack" title="返回上级" aria-label="返回上级"><svg viewBox="0 0 24 24"><path d="m15 18-6-6 6-6" /></svg></button><h1>{{ current?.name || '全部相册' }}</h1><span>{{ albums.length }}</span></div>
        </div>
      </div>
      <div class="brand"><i></i><span>MYREADER</span></div>
      <div class="header-actions">
        <div class="sort-control"><span>排序</span><label class="sort-select"><select v-model="sort"><option value="name">名称</option><option value="added_at">添加时间</option><option value="mtime">修改时间</option><option value="size">大小</option><option value="file_count">页数</option></select><svg viewBox="0 0 24 24"><path d="m8 10 4 4 4-4" /></svg></label><button class="order" @click="order = order === 'asc' ? 'desc' : 'asc'" :title="order === 'asc' ? '升序' : '降序'"><svg viewBox="0 0 24 24"><path :d="order === 'asc' ? 'm8 7 4-4 4 4M12 3v18' : 'm8 17 4 4 4-4M12 3v18'" /></svg></button></div>
        <div class="view-switch" aria-label="封面布局">
          <button :class="{ active: layout === 'vertical' }" @click="setLayout('vertical')" title="竖向卡片" aria-label="竖向卡片"><svg viewBox="0 0 24 24"><rect x="6" y="3" width="12" height="18" rx="2" /></svg></button>
          <button :class="{ active: layout === 'horizontal' }" @click="setLayout('horizontal')" title="横向卡片" aria-label="横向卡片"><svg viewBox="0 0 24 24"><rect x="3" y="6" width="18" height="12" rx="2" /></svg></button>
        </div>
        <button class="icon-button" :class="{ spinning: loading }" @click="refresh" title="刷新相册" aria-label="刷新相册"><svg viewBox="0 0 24 24"><path d="M20 6v5h-5M4 18v-5h5M18.4 9A7 7 0 0 0 6.8 6.4L4 11m16 2-2.8 4.6A7 7 0 0 1 5.6 15" /></svg></button>
        <span class="sidebar-trigger-slot" aria-hidden="true"></span>
      </div>
    </header>

    <main class="workspace" :aria-busy="loading">
      <section class="gallery-panel">
        <p v-if="error" class="error" @click="error = ''"><span>{{ error }}</span><b>×</b></p>
        <div v-if="loading && !albums.length" class="empty"><i class="loader"></i><b>正在整理相册</b></div>
        <div v-else-if="!albums.length" class="empty"><div class="empty-icon"><svg viewBox="0 0 24 24"><path d="M3 6.5h7l2 2h9v10H3z" /></svg></div><b>这里还没有相册</b><span>添加本地文件夹或 ZIP，封面会自动生成。</span><button class="primary" @click="rightOpen = true">添加路径</button></div>
        <section v-else class="album-grid" :class="[layout, { frosted: cardInfoBackground === 'frosted' }]">
          <article v-for="album in albums" :key="album.id" class="album-card" :class="{ folder: album.type === 'folder' }" @click="previewAlbum(album)" @dblclick.stop.prevent="activateAlbum(album)" @contextmenu="showMenu($event, album)">
            <div class="cover"><div class="cover-placeholder"><svg viewBox="0 0 24 24"><path d="M3 6.5h7l2 2h9v10H3z" /></svg></div><img :src="thumb(album)" :alt="album.name" loading="lazy" decoding="async" @error="$event.target.remove()" /><span class="type" :class="album.type">{{ album.type === 'zip' ? 'ZIP' : '目录' }}</span></div>
            <div class="card-info"><h2 :title="album.name">{{ album.name }}</h2><p><span>{{ album.file_count.toLocaleString('zh-CN') }} 页</span><i></i><span>{{ formatSize(album.size) }}</span></p></div>
          </article>
        </section>
      </section>
    </main>

    <aside id="library-drawer" class="drawer left" :class="{ open: leftOpen }">
      <div class="drawer-title"><div><span>资料库</span><b>相册目录</b></div></div>
      <div class="search-wrap"><svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="6"/><path d="m16 16 4 4"/></svg><input v-model="query" class="search" placeholder="搜索名称或路径" /></div>
      <div class="section-label"><span>目录树</span><em>{{ tree.length }}</em></div>
      <div class="tree"><TreeNode v-for="node in tree" :key="node.album.id" :node="node" :current-id="current?.id" :expand-all="!!query" @enter="album => { enter(album); leftOpen = false }" @menu="showMenu" /><p v-if="!tree.length" class="muted">没有匹配的相册</p></div>
      <section class="task-section"><div class="section-label"><span>任务记录</span><em>{{ events.length }}</em></div><div class="task-log"><p v-if="!events.length" class="muted"><time>--:--:--</time><b>等待</b><span>暂无任务</span></p><p v-for="(event, i) in events" :key="i" :class="event.type"><time>{{ event.time }}</time><b>{{ eventLabels[event.type] }}</b><span>{{ event.text }}</span></p></div></section>
    </aside>
    <aside id="settings-drawer" class="drawer right" :class="{ open: rightOpen }">
      <div class="drawer-title"><div><span>管理</span><b>添加与设置</b></div></div>
      <section class="setting-group"><div class="section-label"><span>扫描路径</span><em>每行一个</em></div><textarea v-model="scanPaths" rows="8" spellcheck="false" placeholder="D:/Pictures&#10;D:/Albums/books.zip"></textarea><label class="check"><input v-model="recursive" type="checkbox" /><i></i><span>递归扫描子目录</span></label><button class="primary wide" :disabled="loading || !scanPaths.trim()" @click="scan"><svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14" /></svg>{{ loading ? '正在处理' : '添加并扫描' }}</button></section>
      <section class="setting-group"><div class="section-label"><span>封面比例</span></div><div class="segment"><button :class="{ active: layout === 'vertical' }" @click="setLayout('vertical')"><i class="portrait"></i><span>竖向<small>3 : 4</small></span></button><button :class="{ active: layout === 'horizontal' }" @click="setLayout('horizontal')"><i class="landscape"></i><span>横向<small>3 : 2</small></span></button></div></section>
      <section class="setting-group"><div class="section-label"><span>信息区背景</span></div><div class="segment compact"><button :class="{ active: cardInfoBackground === 'default' }" @click="setCardInfoBackground('default')">默认</button><button :class="{ active: cardInfoBackground === 'frosted' }" @click="setCardInfoBackground('frosted')">磨砂半透明</button></div></section>
    </aside>

    <div v-if="menu" class="context-menu" :style="{ left: `${menu.x}px`, top: `${menu.y}px` }">
      <small>{{ menu.album.name }}</small><button @click="openExplorer(menu.album)">在资源管理器中打开</button>
      <button @click="openViewer(menu.album)">用 LocalViewer 打开</button><hr />
      <button @click="defaultCover(menu.album)">使用默认封面</button>
      <button @click="chooseInternal(menu.album)">选择内部封面</button>
      <button @click="chooseUpload(menu.album)">上传封面</button>
    </div>
    <div v-if="preview" class="cover-preview" @click.self="preview = null">
      <div class="preview-title">{{ preview.name }}</div>
      <button v-if="albums.length > 1" class="preview-nav previous" @click="movePreview(-1)" title="上一张" aria-label="上一张"><svg viewBox="0 0 24 24"><path d="m15 18-6-6 6-6" /></svg></button>
      <img :key="preview.id" :src="originalCover(preview)" :alt="preview.name" />
      <button v-if="albums.length > 1" class="preview-nav next" @click="movePreview(1)" title="下一张" aria-label="下一张"><svg viewBox="0 0 24 24"><path d="m9 18 6-6-6-6" /></svg></button>
    </div>
    <div v-if="coverDialog && !coverDialog.upload" class="modal-wrap" @click.self="coverDialog = null"><div class="modal"><div class="drawer-title"><b>选择内部封面</b><button @click="coverDialog = null">×</button></div><div class="image-list"><p v-if="coverOptions.items.length" class="option-label">本相册图片</p><button v-for="entry in coverOptions.items" :key="entry" @click="setInternal(entry)">{{ entry }}</button><p v-if="coverOptions.albums.length" class="option-label">下级相册封面</p><button v-for="album in coverOptions.albums" :key="album.id" class="album-option" @click="setChildAlbumCover(album)"><img :src="thumb(album)" :alt="album.name" /><span>{{ album.name }}</span><small>{{ album.file_count }} 页</small></button><p v-if="!coverOptions.items.length && !coverOptions.albums.length">此相册没有可选封面。</p></div></div></div>
    <input ref="fileInput" hidden type="file" accept="image/jpeg,image/png,image/webp,image/gif" @change="uploadCover" />
  </div>
</template>
