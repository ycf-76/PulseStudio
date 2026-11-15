import { createApp } from 'vue'
import { createPinia } from 'pinia'
import router from './router'
import App from './App.vue'
import './styles/main.css'
import './styles/workbench.css'
// 渲染层共享样式（跨组件 rl-* 规则）：协作面板 / 执行轨迹 / Transition。
// 必须在 App 挂载前加载——ToolCallCell、AssistantMessageCell 协作区在任何路由下都依赖它。
import './components/chat/render-layer.css'

const r = 'color:#ff2442;font-weight:bold'
const c = 'color:#00d4ff;font-weight:bold'
const d = 'color:#888'
console.log(
  `%c
  ██╗  ██╗██╗  ██╗     ██████╗  ██████╗ ███████╗████████╗███████╗██████╗
  ╚██╗██╔╝██║  ██║     ██╔══██╗██╔═══██╗██╔════╝╚══██╔══╝██╔════╝██╔══██╗
   ╚███╔╝ ███████║     ██████╔╝██║   ██║███████╗   ██║   ███████╗██████╔╝
   ██╔██╗ ██╔══██║     ██╔══██╗██║   ██║██╔════╝   ██║   ╚════██║██╔══██╗
  ██╔╝ ██╗██║  ██║     ██║  ██╗╚██████╔╝███████╗   ██║   ███████║██║  ██╗
  ╚═╝  ╚═╝╚═╝  ╚═╝     ╚═╝  ╚═╝ ╚═════╝ ╚══════╝   ╚═╝   ╚══════╝╚═╝  ╚═╝`,
  r,
)
console.log(
  `%cXHS Creator %cv1.0.0 — 多智能体小红书创作平台`,
  r, d,
)
console.log(
  `%c选题 → 企划 → 写稿 → 配图 → 发布，Agent 串起来才叫流水线。`,
  c,
)

const app = createApp(App)

app.use(createPinia())
app.use(router)

app.mount('#app')