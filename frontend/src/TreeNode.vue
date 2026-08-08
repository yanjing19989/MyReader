<script setup>
import { ref, watch } from 'vue'

defineOptions({ name: 'TreeNode' })
const props = defineProps({
  node: { type: Object, required: true },
  currentId: Number,
  depth: { type: Number, default: 0 },
  expandAll: Boolean
})
const emit = defineEmits(['enter', 'menu'])
const open = ref(false)
const contains = (id, node = props.node) => node.album.id === id || node.children.some(child => contains(id, child))
const sync = () => { if (props.expandAll || props.depth === 0 || contains(props.currentId)) open.value = true }
watch([() => props.currentId, () => props.expandAll], sync, { immediate: true })
</script>

<template>
  <div class="tree-node">
    <div class="tree-row" :class="{ current: node.album.id === currentId }" @dblclick="emit('enter', node.album)" @contextmenu.prevent="emit('menu', $event, node.album)">
      <button v-if="node.children.length" @click.stop="open = !open">{{ open ? '−' : '+' }}</button><i v-else></i>
      <span class="tree-kind" :class="node.album.type">{{ node.album.type === 'zip' ? 'Z' : 'F' }}</span>
      <label :title="node.path">{{ node.album.name }}</label><small>{{ node.album.file_count }}</small>
    </div>
    <div v-if="open" class="tree-children">
      <TreeNode v-for="child in node.children" :key="child.album.id" :node="child" :current-id="currentId" :depth="depth + 1" :expand-all="expandAll" @enter="emit('enter', $event)" @menu="(...args) => emit('menu', ...args)" />
    </div>
  </div>
</template>
