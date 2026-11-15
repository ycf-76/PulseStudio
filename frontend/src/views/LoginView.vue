<template>
  <div class="login-page">
    <section class="login-visual" aria-hidden="true">
      <div class="lv-top">
        <div class="lv-kicker">
          <span class="lv-dot"></span>
          <span class="lv-kicker-label">Pulse Studio</span>
          <span class="lv-kicker-line"></span>
          <span class="lv-kicker-tag">多智能体内容工作台</span>
        </div>
        <p class="lv-tagline">从热点洞察到一键发布，内容生产一站搞定</p>
      </div>

      <div class="lv-stage">
        <img class="login-visual-img" src="/icons/pulse_realtime.svg" alt="" />
      </div>

      <div class="lv-bottom">
        <div class="lv-features">
          <div v-for="item in visualFeatures" :key="item.title" class="lv-feature">
            <div class="lv-feature-icon">
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                <path :d="item.icon" />
              </svg>
            </div>
            <div class="lv-feature-title">{{ item.title }}</div>
            <div class="lv-feature-desc">{{ item.desc }}</div>
          </div>
        </div>
        <div class="lv-foot">© 2025 脉冲工作室</div>
      </div>
    </section>
    <section class="login-panel">
    <div class="login-content" :style="floatStyle">
      <div class="login-header">
        <div class="login-logo">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 180" width="30" height="27">
            <defs>
              <filter id="shadow" x="-10%" y="-10%" width="120%" height="130%">
                <feDropShadow dx="0" dy="3" stdDeviation="2.5" flood-color="#000" flood-opacity="0.15"/>
              </filter>
            </defs>
            <path d="M52,86 Q36,76 30,68" stroke="#D94F44" stroke-width="11" stroke-linecap="round" fill="none" filter="url(#shadow)"/>
            <path d="M148,86 Q164,76 170,68" stroke="#D94F44" stroke-width="11" stroke-linecap="round" fill="none" filter="url(#shadow)"/>
            <g filter="url(#shadow)">
              <ellipse cx="26" cy="56" rx="18" ry="11" transform="rotate(-45 26 56)" fill="#E8655A"/>
              <ellipse cx="42" cy="80" rx="16" ry="10" transform="rotate(15 42 80)" fill="#E8655A"/>
            </g>
            <g filter="url(#shadow)">
              <ellipse cx="174" cy="56" rx="18" ry="11" transform="rotate(45 174 56)" fill="#E8655A"/>
              <ellipse cx="158" cy="80" rx="16" ry="10" transform="rotate(-15 158 80)" fill="#E8655A"/>
            </g>
            <ellipse cx="100" cy="105" rx="58" ry="48" fill="#E8655A" filter="url(#shadow)"/>
            <path d="M55,110 Q30,118 22,134" stroke="#E8655A" stroke-width="7" stroke-linecap="round" fill="none" filter="url(#shadow)"/>
            <path d="M52,125 Q28,136 18,154" stroke="#E8655A" stroke-width="7" stroke-linecap="round" fill="none" filter="url(#shadow)"/>
            <path d="M62,138 Q48,152 42,170" stroke="#E8655A" stroke-width="7" stroke-linecap="round" fill="none" filter="url(#shadow)"/>
            <path d="M145,110 Q170,118 178,134" stroke="#E8655A" stroke-width="7" stroke-linecap="round" fill="none" filter="url(#shadow)"/>
            <path d="M148,125 Q172,136 182,154" stroke="#E8655A" stroke-width="7" stroke-linecap="round" fill="none" filter="url(#shadow)"/>
            <path d="M138,138 Q152,152 158,170" stroke="#E8655A" stroke-width="7" stroke-linecap="round" fill="none" filter="url(#shadow)"/>
            <circle cx="30" cy="68" r="8" fill="#D94F44"/>
            <circle cx="170" cy="68" r="8" fill="#D94F44"/>
            <circle cx="78" cy="62" r="16" fill="white" filter="url(#shadow)"/>
            <circle cx="80" cy="62" r="8" fill="var(--rl-text-1)"/>
            <circle cx="83" cy="59" r="3" fill="white"/>
            <circle cx="122" cy="62" r="16" fill="white" filter="url(#shadow)"/>
            <circle cx="120" cy="62" r="8" fill="var(--rl-text-1)"/>
            <circle cx="123" cy="59" r="3" fill="white"/>
          </svg>
        </div>
        <img src="/icons/logo2.svg" alt="Logo" class="login-brand" />
      </div>

      <div class="login-intro">
        <h1 class="login-title">Welcome back</h1>
      </div>

      <div v-if="activeTab === 'home' || activeTab === 'email'" class="login-main">
        <div class="login-email-section">
          <template v-if="emailStep === 'input'">
            <div class="login-input-wrapper">
              <svg class="login-input-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <rect x="2.5" y="4.5" width="19" height="15" rx="3.5"/>
                <path d="M3.5 7.5l7.1 5.1a2.5 2.5 0 0 0 2.8 0l7.1-5.1"/>
              </svg>
              <input
                v-model="emailForm.email"
                type="email"
                class="login-input"
                placeholder="输入邮箱地址"
                @keyup.enter="handleSendCode"
                @focus="isInputFocused = true"
                @blur="isInputFocused = false"
              />
            </div>
            <div class="login-privacy">
              <label class="login-checkbox-label">
                <input v-model="agreedPrivacy" type="checkbox" class="login-checkbox" />
                <span>同意<span class="login-link" @click.stop="showPolicy = 'privacy'">隐私政策</span>和<span class="login-link" @click.stop="showPolicy = 'terms'">服务条款</span></span>
              </label>
            </div>
            <div v-if="errorMsg" class="login-error-msg">{{ errorMsg }}</div>
            <button
              class="login-btn login-btn-code"
              :disabled="!emailForm.email || !agreedPrivacy || sendingCode"
              @click="handleSendCode"
              type="button"
            >
              {{ sendingCode ? '发送中...' : '发送验证码' }}
            </button>
          </template>
          <template v-else>
            <div class="code-sent-to">验证码已发送至 {{ emailForm.email }}</div>
            <div class="code-boxes">
              <div v-for="i in 6" :key="i" class="code-box-wrapper">
                <input
                  :ref="el => { if (el) codeInputs[i - 1] = el as HTMLInputElement }"
                  v-model="codeDigits[i - 1]"
                  type="text"
                  class="code-box"
                  maxlength="1"
                  inputmode="numeric"
                  @input="onCodeInput(i - 1)"
                  @keydown.backspace="onCodeBackspace(i - 1, $event)"
                  @paste="onCodePaste"
                  @focus="isInputFocused = true"
                  @blur="isInputFocused = false"
                />
              </div>
            </div>
            <div v-if="errorMsg" class="login-error-msg">{{ errorMsg }}</div>
            <button
              class="login-btn"
              :disabled="codeDigits.join('').length < 6 || emailLoading"
              @click="handleEmailLogin"
              type="button"
            >
              {{ emailLoading ? '登录中...' : '验证' }}
            </button>
            <div class="code-actions">
              <button
                class="login-link"
                :disabled="countdown > 0"
                @click="handleSendCode"
                type="button"
              >
                {{ countdown > 0 ? `${countdown}s 后重发` : '重新发送' }}
              </button>
              <button class="login-link" @click="emailStep = 'input'; codeDigits = ['', '', '', '', '', '']" type="button">更换邮箱</button>
            </div>
          </template>
        </div>
      </div>

      <Teleport to="body">
      <Transition name="policy-fade">
      <div v-if="showPolicy" class="policy-overlay" @click.self="showPolicy = ''">
        <div class="policy-card">
          <div class="policy-header">
            <h3 class="policy-title">{{ showPolicy === 'privacy' ? '隐私政策' : '服务条款' }}</h3>
            <button class="policy-close" @click="showPolicy = ''" type="button">
              <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
            </button>
          </div>
          <div class="policy-body">
            <template v-if="showPolicy === 'privacy'">
              <p>更新日期：2025年1月1日</p>
              <p>欢迎使用脉冲工作室。我们非常重视您的隐私保护和个人信息保护。</p>
              <h4>一、信息收集</h4>
              <p>我们可能收集以下信息：邮箱地址（用于登录验证）、小红书账号信息（用于内容发布服务）、使用行为数据（用于优化服务体验）。</p>
              <h4>二、信息使用</h4>
              <p>收集的信息仅用于：提供和改善服务、发送服务通知、安全保障。我们不会将您的个人信息出售给第三方。</p>
              <h4>三、信息存储与安全</h4>
              <p>我们采用行业标准的安全措施保护您的信息，包括数据加密、访问控制等。数据存储在中国境内的服务器上。</p>
              <h4>四、Cookie 使用</h4>
              <p>我们使用 Cookie 和类似技术来维护会话状态、记住登录信息和分析使用情况。</p>
              <h4>五、您的权利</h4>
              <p>您有权访问、更正、删除您的个人信息，并可撤回授权同意。如需行使上述权利，请通过应用内设置或联系我们。</p>
              <h4>六、未成年人保护</h4>
              <p>我们非常注重对未成年人个人信息的保护。若您是18周岁以下的未成年人，建议在监护人指导下使用本服务。</p>
              <h4>七、政策更新</h4>
              <p>本政策可能不时更新，更新后将在应用内通知您。继续使用服务即视为同意更新后的政策。</p>
            </template>
            <template v-else>
              <p>更新日期：2025年1月1日</p>
              <p>欢迎使用脉冲工作室。请仔细阅读以下服务条款。</p>
              <h4>一、服务内容</h4>
              <p>脉冲工作室为您提供内容创作与发布辅助服务，包括但不限于：热点搜索、文案生成、标题优化、自动发布等功能。</p>
              <h4>二、用户行为规范</h4>
              <p>您承诺：遵守中华人民共和国相关法律法规；不利用本服务发布违法、违规或侵权内容；不利用本服务进行任何恶意行为，包括但不限于刷量、恶意营销等。</p>
              <h4>三、知识产权</h4>
              <p>您通过本服务生成的内容，其知识产权归您所有。我们不会对您的内容主张任何权利，但有权在必要范围内处理内容以提供服务。</p>
              <h4>四、免责声明</h4>
              <p>因不可抗力、第三方服务故障等原因导致的服务中断，我们不承担责任。您使用本服务产生的法律后果由您自行承担。</p>
              <h4>五、服务变更与终止</h4>
              <p>我们有权根据业务需要变更或终止部分服务，届时将提前通知您。您有权随时停止使用本服务并注销账号。</p>
              <h4>六、争议解决</h4>
              <p>因本条款产生的争议，双方应友好协商解决；协商不成的，任何一方均可向我们所在地有管辖权的人民法院提起诉讼。</p>
            </template>
          </div>
          <div class="policy-footer">
            <button class="policy-agree-btn" @click="showPolicy = ''; agreedPrivacy = true" type="button">我已阅读并同意</button>
          </div>
        </div>
      </div>
    </Transition>
    </Teleport>
    </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { ref, nextTick, onMounted, onUnmounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { authApi, saveLogin } from '@/api/auth'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const route = useRoute()
const authStore = useAuthStore()

const SK_HAS_LOGGED_IN = 'mint_has_logged_in_before'
const SK_FIRST_LOGIN = 'mint_first_login_session'
const SK_WELCOME_AD = 'mint_show_welcome_ad'

function saveLoginAndSync(data: { token: string; refresh_token?: string; user_id: string; nickname: string; avatar_url: string; has_xhs_auth?: boolean; login_method?: string }) {
  saveLogin(data)
  authStore.token = data.token
  authStore.user = {
    user_id: data.user_id,
    nickname: data.nickname,
    avatar_url: data.avatar_url,
    has_xhs_auth: data.has_xhs_auth ?? false,
    login_method: data.login_method || '',
  }
  localStorage.setItem('mint_user_info', JSON.stringify(authStore.user))
}

function markFirstLoginAndPush() {
  if (!localStorage.getItem(SK_HAS_LOGGED_IN)) {
    sessionStorage.setItem(SK_FIRST_LOGIN, '1')
    localStorage.setItem(SK_HAS_LOGGED_IN, '1')
  }
  sessionStorage.setItem(SK_WELCOME_AD, '1')
  const redirect = (route.query.redirect as string) || sessionStorage.getItem('redirect_after_login') || '/chat'
  sessionStorage.removeItem('redirect_after_login')
  router.push(redirect).then(() => {
    window.dispatchEvent(new CustomEvent('app:show-welcome-ad'))
  })
}

const activeTab = ref<'home' | 'email'>('home')
const errorMsg = ref('')
const isInputFocused = ref(false)
const floatStyle = ref<Record<string, string>>({})

const visualFeatures = [
  { title: '热点洞察', desc: '实时捕捉平台趋势', icon: 'M3 16.5 8 11.5 12 15.5 21 6.5 M15.5 6.5 H21 V12' },
  { title: '多智能体创作', desc: '文案 · 标题 · 配图协同', icon: 'M12 2.8 2.6 7.6l9.4 4.9 9.4-4.9L12 2.8Z M2.6 12l9.4 4.9 9.4-4.9 M2.6 16.4l9.4 4.9 9.4-4.9' },
  { title: '一键发布', desc: '多账号定时排期', icon: 'M21.5 2.5 11 13 M21.5 2.5l-7 19.5-3.6-8.6-8.6-3.6L21.5 2.5Z' },
]

const emailStep = ref<'input' | 'code'>('input')
const emailForm = ref({ email: '', code: '' })
const sendingCode = ref(false)
const emailLoading = ref(false)
const agreedPrivacy = ref(false)
const showPolicy = ref<'' | 'privacy' | 'terms'>('')
const countdown = ref(0)
let countdownTimer: number | null = null

const codeDigits = ref<string[]>(['', '', '', '', '', ''])
const codeInputs = ref<HTMLInputElement[]>([])

function onCodeInput(index: number) {
  const val = codeDigits.value[index]
  codeDigits.value[index] = val.replace(/[^0-9]/g, '').slice(0, 1)
  if (codeDigits.value[index] && index < 5) {
    codeInputs.value[index + 1]?.focus()
  }
}

function onCodeBackspace(index: number, e: KeyboardEvent) {
  if (!codeDigits.value[index] && index > 0) {
    codeDigits.value[index - 1] = ''
    codeInputs.value[index - 1]?.focus()
    e.preventDefault()
  }
}

function onCodePaste(e: ClipboardEvent) {
  const text = e.clipboardData?.getData('text')?.replace(/[^0-9]/g, '').slice(0, 6) || ''
  if (text.length > 0) {
    for (let i = 0; i < 6; i++) {
      codeDigits.value[i] = text[i] || ''
    }
    const focusIdx = Math.min(text.length, 5)
    codeInputs.value[focusIdx]?.focus()
  }
  e.preventDefault()
}

async function handleSendCode() {
  const { email } = emailForm.value
  if (!email || !email.includes('@')) {
    errorMsg.value = '请输入正确的邮箱'
    return
  }
  localStorage.removeItem('token')
  localStorage.removeItem('refresh_token')
  sendingCode.value = true
  errorMsg.value = ''
  try {
    await authApi.sendEmailCode(email)
    emailStep.value = 'code'
    codeDigits.value = ['', '', '', '', '', '']
    await nextTick()
    codeInputs.value[0]?.focus()
    startCountdown()
  } catch (e: any) {
    errorMsg.value = e.response?.data?.detail || e.message || '发送验证码失败'
  } finally {
    sendingCode.value = false
  }
}

function startCountdown() {
  countdown.value = 60
  if (countdownTimer) clearInterval(countdownTimer)
  countdownTimer = window.setInterval(() => {
    countdown.value--
    if (countdown.value <= 0) {
      clearInterval(countdownTimer!)
      countdownTimer = null
    }
  }, 1000)
}

async function handleEmailLogin() {
  if (emailLoading.value) return
  const email = emailForm.value.email
  const code = codeDigits.value.join('')
  if (!email || code.length < 6) {
    errorMsg.value = '请输入6位验证码'
    return
  }
  emailLoading.value = true
  errorMsg.value = ''
  try {
    const response = await authApi.emailLogin(email, code)
    if (response && response.data) {
      saveLoginAndSync(response.data)
      markFirstLoginAndPush()
    }
  } catch (e: any) {
    const status = e.response?.status
    const detail = e.response?.data?.detail
    errorMsg.value = detail || e.message || '登录失败'
    if (status === 410) {
      codeDigits.value = ['', '', '', '', '', '']
      codeInputs.value[0]?.focus()
    } else if (status === 429) {
      errorMsg.value = detail || '验证次数过多，请稍后再试'
    } else if (status === 401) {
      localStorage.removeItem('token')
      localStorage.removeItem('refresh_token')
    }
  } finally {
    emailLoading.value = false
  }
}

onMounted(() => {
  let rafId: number | null = null
  let floatStart: number | null = null
  const entryFrom = 120
  const entryDuration = 1000
  const mountTime = performance.now()

  function tick(now: number) {
    const sinceMount = now - mountTime

    if (sinceMount < entryDuration) {
      const t = sinceMount / entryDuration
      const ease = 1 - Math.pow(1 - t, 3)
      const y = entryFrom * (1 - ease)
      floatStyle.value = {
        transform: `translateY(${y}px)`,
        opacity: String(ease),
        transition: 'none',
      }
    } else if (isInputFocused.value) {
      floatStyle.value = {
        transform: 'translateY(0)',
        opacity: '1',
        transition: 'transform 0.5s cubic-bezier(0.16, 1, 0.3, 1)',
      }
    } else {
      if (!floatStart) floatStart = now
      const elapsed = now - floatStart
      const y = Math.sin(elapsed / 800) * 8
      floatStyle.value = {
        transform: `translateY(${y}px)`,
        opacity: '1',
        transition: 'none',
      }
    }

    rafId = requestAnimationFrame(tick)
  }

  rafId = requestAnimationFrame(tick)

  onUnmounted(() => {
    if (rafId) cancelAnimationFrame(rafId)
  })
})

onUnmounted(() => {
  if (countdownTimer) {
    clearInterval(countdownTimer)
    countdownTimer = null
  }
})
</script>

<style scoped>
/* 登录页使用的 rl 令牌在本组件内自带定义：
   全局只定义了 --ma-* 一套，--rl-amber / --rl-text-3 等只在 .rl-chat 作用域内存在，
   登录页直接消费会解析失败（按钮底色失效），因此这里就地声明一份。 */
.login-page {
  --rl-amber: #1A1A1A;
  --rl-text-1: #1A1A1A;
  --rl-text-3: #9A9A9A;
  --rl-border: rgba(0, 0, 0, 0.10);
  --rl-red: #B42318;
  position: relative;
  height: 100vh;
  display: flex;
  align-items: stretch;
  /* 内容整体居中：左区有 max-width 上限，超宽屏多出来的空间被两端平分，
     构图宽度因此锁定，不会随屏幕变宽而越拉越扁 */
  justify-content: center;
  /* 深色画布：白色窗口四周的留白也来自这一层，网格因此能连续地绕过窗口 */
  background: #05070a;
  overflow: hidden;
}

/* 方格网纹：线宽统一 1px，只让亮度从中心向四周递增。
   只有一层，避免两层叠加时出现"半像素相加"导致的加粗错觉；
   遮罩在中心压到 0.42、边缘升到 1.0，于是中心约 0.029、四周 0.07，
   读起来是"中间淡、四周清晰"，但线始终是一条 1px 细线。 */
.login-page::before {
  content: '';
  position: absolute;
  z-index: 0;
  inset: 0;
  pointer-events: none;
  background-image:
    linear-gradient(rgba(255, 255, 255, 0.07) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255, 255, 255, 0.07) 1px, transparent 1px);
  background-size: 58px 58px;
  -webkit-mask-image: radial-gradient(ellipse 62% 58% at 46% 50%, rgba(0, 0, 0, 0.42) 0%, rgba(0, 0, 0, 0.5) 34%, rgba(0, 0, 0, 0.72) 62%, rgba(0, 0, 0, 0.9) 84%, #000 100%);
  mask-image: radial-gradient(ellipse 62% 58% at 46% 50%, rgba(0, 0, 0, 0.42) 0%, rgba(0, 0, 0, 0.5) 34%, rgba(0, 0, 0, 0.72) 62%, rgba(0, 0, 0, 0.9) 84%, #000 100%);
}

/* 左：品牌视觉 —— pulse_realtime.svg 居中呈现，上下留白用品牌信息与功能卡片填满 */
.login-visual {
  --lv-accent: #E8655A;
  --lv-text: rgba(255, 255, 255, 0.92);
  --lv-muted: rgba(255, 255, 255, 0.46);
  position: relative;
  flex: 1 1 auto;
  min-width: 0;
  /* 上限 1000px：左区只在这个宽度内伸展，超宽屏不再把图片和功能卡片
     拉成一片空旷的黑色空地，左右两栏的比例因此在小屏和大屏上基本一致 */
  max-width: 1000px;
  display: flex;
  flex-direction: column;
  gap: clamp(18px, 2.4vh, 30px);
  padding: clamp(26px, 4vh, 46px) clamp(26px, 3.4vw, 54px);
  /* 不铺底色，让整页的方格网纹透上来，左右两区才是同一张底 */
  background: transparent;
  overflow: hidden;
  color: var(--lv-text);
}

/* 左上角辉光：画在整页层级而不是左侧区域里，因为左区现在有 max-width 上限
   并被居中，把光挂在它内部时，屏幕越宽光就离画面真正的左上角越远。
   半径用固定 px 而不是百分比，是为了让光的实际大小恒定 —— 百分比会随容器
   变宽而摊薄，同一份透明度铺到更大面积上，放大后就读不出颜色了。
   两层叠加：小半径亮核给出光源感，大半径暗晕负责过渡。
   两条动画并行：opacity 负责入场淡入，filter 负责呼吸；
   分成两个属性是因为同一个属性没法同时被两条动画驱动。 */
.login-page::after {
  content: '';
  position: absolute;
  z-index: 0;
  inset: 0;
  pointer-events: none;
  background:
    radial-gradient(300px 230px at 0% 0%, rgba(255, 140, 115, 0.15), transparent 70%),
    radial-gradient(680px 520px at 2% 1%, rgba(232, 101, 90, 0.19), transparent 74%);
  /* 周期 2.4s、缓动 ease-in-out、从 0s 起跑，与 .lv-dot 的 lvPulse 完全一致，
     两者因此同相位呼吸，不会各自漂移 */
  animation:
    lvFadeIn 1.1s ease 0s backwards,
    lvGlowBreath 2.4s ease-in-out 0s infinite;
}

.lv-top {
  position: relative;
  z-index: 1;
  display: flex;
  flex-direction: column;
  gap: 10px;
  animation: lvSlideDown 0.72s cubic-bezier(0.16, 1, 0.3, 1) 0.1s backwards;
}

.lv-kicker {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 11px;
  letter-spacing: 0.2em;
  text-transform: uppercase;
  color: rgba(255, 255, 255, 0.3);
}

.lv-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--lv-accent);
  box-shadow: 0 0 0 3px rgba(232, 101, 90, 0.16), 0 0 12px rgba(232, 101, 90, 0.55);
  animation: lvPulse 2.4s ease-in-out infinite;
}

.lv-kicker-line {
  width: 26px;
  height: 1px;
  background: rgba(255, 255, 255, 0.18);
}

.lv-kicker-tag {
  letter-spacing: 0.06em;
  text-transform: none;
  color: rgba(255, 255, 255, 0.4);
}

.lv-tagline {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  letter-spacing: 0.02em;
  color: var(--lv-muted);
}

.lv-stage {
  position: relative;
  z-index: 1;
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  /* 0.32s 内快速就位：SVG 自身带一次性入场编排，容器再慢慢淡入会把那段动画整段盖掉 */
  animation: lvStageIn 0.32s cubic-bezier(0.16, 1, 0.3, 1) 0s backwards;
}

.login-visual-img {
  display: block;
  width: auto;
  height: auto;
  max-width: 100%;
  max-height: 100%;
  border-radius: 18px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  box-shadow: 0 24px 70px -28px rgba(0, 0, 0, 0.95), 0 0 44px -14px rgba(232, 101, 90, 0.22);
}

.lv-bottom {
  position: relative;
  z-index: 1;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.lv-features {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

.lv-feature {
  padding: 13px 14px;
  border-radius: 14px;
  border: 1px solid rgba(255, 255, 255, 0.07);
  background: rgba(255, 255, 255, 0.035);
  transition: background 0.25s ease, border-color 0.25s ease, transform 0.25s ease;
  animation: lvCardIn 0.68s cubic-bezier(0.16, 1, 0.3, 1) backwards;
}

.lv-features > .lv-feature:nth-child(1) { animation-delay: 0.56s; }
.lv-features > .lv-feature:nth-child(2) { animation-delay: 0.66s; }
.lv-features > .lv-feature:nth-child(3) { animation-delay: 0.76s; }

.lv-feature:hover {
  background: rgba(255, 255, 255, 0.06);
  border-color: rgba(255, 255, 255, 0.14);
  transform: translateY(-2px);
}

.lv-feature-icon {
  width: 26px;
  height: 26px;
  margin-bottom: 9px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--lv-accent);
  background: rgba(232, 101, 90, 0.12);
}

.lv-feature-icon svg {
  width: 15px;
  height: 15px;
}

.lv-feature-title {
  margin-bottom: 4px;
  font-size: 13px;
  font-weight: 500;
  letter-spacing: 0.02em;
  color: var(--lv-text);
}

.lv-feature-desc {
  font-size: 11px;
  line-height: 1.5;
  color: rgba(255, 255, 255, 0.4);
}

.lv-foot {
  font-size: 11px;
  letter-spacing: 0.06em;
  color: rgba(255, 255, 255, 0.24);
  animation: lvFadeUp 0.7s cubic-bezier(0.16, 1, 0.3, 1) 0.92s backwards;
}

@keyframes lvPulse {
  0%, 100% { opacity: 0.75; transform: scale(0.9); }
  50% { opacity: 1; transform: scale(1.05); }
}

/* 与 lvPulse 同一节奏（2.4s / ease-in-out / 0%与100%最暗、50%最亮）。
   用 filter 而不是 opacity，是为了不和入场的 lvFadeIn 抢同一个属性；
   brightness 只乘 RGB 不改 alpha，所以是"同一片红变亮变暗"，
   而不是"整块透明度浮动"，不会在深色底上露出底色断层。 */
@keyframes lvGlowBreath {
  0%, 100% { filter: brightness(0.8); }
  50% { filter: brightness(1.18); }
}

@keyframes lvFadeIn {
  from { opacity: 0; }
  to { opacity: 1; }
}

@keyframes lvSlideDown {
  from { opacity: 0; transform: translateY(-16px); }
  to { opacity: 1; transform: translateY(0); }
}

@keyframes lvFadeUp {
  from { opacity: 0; transform: translateY(14px); }
  to { opacity: 1; transform: translateY(0); }
}

@keyframes lvStageIn {
  from { opacity: 0; transform: translateY(26px) scale(0.965); }
  to { opacity: 1; transform: translateY(0) scale(1); }
}

@keyframes lvCardIn {
  from { opacity: 0; transform: translateY(22px); }
  to { opacity: 1; transform: translateY(0); }
}

@media (prefers-reduced-motion: reduce) {
  .lv-dot,
  .lv-top,
  .lv-stage,
  .lv-feature,
  .lv-foot,
  .login-page::after {
    animation: none;
  }

  .login-input-wrapper::after,
  .login-input-wrapper:focus-within::after {
    transition: none;
  }
}

/* 右：登录面板 —— 浮在黑色画布之上的独立窗口
   四边各留 26px，黑色背景完整地环绕窗口一圈（左侧也留），
   这样它读作"一张浮起来的窗口"，而不是原来的右半栏。
   宽度从 clamp(360px,40%,560px) 收窄到 clamp(320px,33%,440px)：
   窗口更瘦，四周的深色留白因此更宽，方格网纹在窗口外围也看得清楚。 */
.login-panel {
  position: relative;
  z-index: 2;
  flex: 0 0 clamp(320px, 33%, 440px);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 36px clamp(22px, 3.2vw, 44px);
  margin: 26px;
  /* 关键：不再随视口高度被拉满。align-self: center 让窗口高度由内容决定，
     于是大屏上它仍是一张"登录卡片"，而不是一条 1000px 高的白色窄条；
     max-height 兜住矮屏，内容超出时内部滚动，外层留白不破 */
  align-self: center;
  max-height: calc(100vh - 52px);
  background: #ffffff;
  overflow-y: auto;
  border-radius: 26px;
  box-shadow:
    0 30px 70px -24px rgba(0, 0, 0, 0.9),
    0 8px 24px -12px rgba(0, 0, 0, 0.55);
}

.login-content {
  width: 100%;
  max-width: 340px;
  display: flex;
  flex-direction: column;
  align-items: center;
  margin: auto;
  opacity: 0;
  transform: translateY(120px);
}

.login-header {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  margin-bottom: 20px;
  margin-top: 0;
}

.login-intro {
  text-align: center;
  margin-bottom: 30px;
}

.login-title {
  font-family: 'Fraunces', Georgia, 'Times New Roman', serif;
  font-weight: 500;
  font-size: 26px;
  letter-spacing: -0.005em;
  line-height: 1.16;
  color: var(--text-primary);
  margin: 0;
  /* SOFT=100 让衬线笔画末端变圆润，WONK=0 关掉字形的怪趣扭斜，保持端庄 */
  font-variation-settings: 'SOFT' 100, 'WONK' 0, 'opsz' 44;
}

/* 用极轻的上下渐变压暗字底，让深色标题有体积感而非一块死黑 */
@supports (background-clip: text) or (-webkit-background-clip: text) {
  .login-title {
    background-image: linear-gradient(180deg, #232427 4%, #77787C 100%);
    -webkit-background-clip: text;
    background-clip: text;
    -webkit-text-fill-color: transparent;
  }
}

/* 窄屏：收起左侧视觉，面板占满，保留可用性 */
@media (max-width: 900px) {
  .login-page {
    height: auto;
    min-height: 100vh;
    overflow: auto;
    /* 窄屏没有深色视觉区衬托，页面回到白底，卡片形态一并取消 */
    background: #ffffff;
  }
  /* 白底上辉光和网纹都会变成脏点，一并关掉 */
  .login-page::before,
  .login-page::after {
    display: none;
  }
  .login-visual {
    display: none;
  }
  .login-panel {
    flex: 1 1 auto;
    padding: 32px 24px;
    margin: 0;
    border-radius: 0;
    box-shadow: none;
    overflow-y: visible;
    /* 窄屏取消卡片形态，把居中的浮层还原成铺满的页面 */
    align-self: stretch;
    max-height: none;
  }
}

.login-logo {
  width: 40px;
  height: 40px;
  flex: 0 0 auto;
  border-radius: 14px;
  background: #ffffff;
  border: 1px solid var(--rl-border);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.05);
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  position: relative;
  z-index: 1;
}

.login-brand {
  height: 40px;
  width: auto;
  flex: 0 0 auto;
}

.login-main {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.login-option {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 14px 18px;
  border: none;
  border-radius: 999px;
  background: transparent !important;
  font-size: 15px;
  font-weight: var(--weight-medium);
  color: var(--text-primary);
  cursor: pointer;
  transition: all 0.2s ease;
  position: relative;
}

.login-option::before {
  content: '';
  position: absolute;
  inset: -1px;
  border-radius: 999px;
  padding: 1.5px;
  background: linear-gradient(90deg,
    transparent 0%,
    rgba(255,255,255,0.2) 10%,
    rgba(255,255,255,0.5) 30%,
    rgba(255,255,255,0.9) 50%,
    rgba(255,255,255,0.5) 70%,
    rgba(255,255,255,0.2) 90%,
    transparent 100%
  );
  mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
  mask-composite: exclude;
  -webkit-mask-composite: xor;
  pointer-events: none;
}

.login-option:hover::before {
  background: linear-gradient(90deg,
    transparent 0%,
    rgba(255,255,255,0.3) 10%,
    rgba(255,255,255,0.65) 30%,
    rgba(255,255,255,1) 50%,
    rgba(255,255,255,0.65) 70%,
    rgba(255,255,255,0.3) 90%,
    transparent 100%
  );
}

.login-option span {
  text-align: center;
}

.login-divider {
  display: flex;
  align-items: center;
  gap: 12px;
  color: var(--rl-text-3);
  font-size: 13px;
}

.login-divider::before {
  content: '';
  flex: 1;
  height: 3px;
  background: linear-gradient(to right,
    transparent 0%,
    rgba(148,163,184,0.1) 20%,
    rgba(148,163,184,0.35) 45%,
    rgba(148,163,184,0.6) 70%,
    rgba(148,163,184,0.7) 100%
  );
  border-radius:4px;
}

.login-divider::after {
  content: '';
  flex: 1;
  height: 3px;
  background: linear-gradient(to left,
    transparent 0%,
    rgba(148,163,184,0.1) 20%,
    rgba(148,163,184,0.35) 45%,
    rgba(148,163,184,0.6) 70%,
    rgba(148,163,184,0.7) 100%
  );
  border-radius:4px;
}

.login-privacy {
  font-size: 12px;
  color: var(--rl-text-3);
  text-align: center;
  line-height: 1.5;
}

.login-checkbox-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
}

.login-checkbox {
  width: 14px;
  height: 14px;
  border-radius:8px;
  accent-color: var(--rl-amber);
  cursor: pointer;
  flex-shrink: 0;
}

.login-privacy .login-link {
  font-size: 12px;
  color: #1A1A1A;
  font-weight: 400;
  text-decoration: underline;
  text-underline-offset: 2px;
  text-decoration-thickness: 1px;
  transition: color 0.2s ease, opacity 0.2s ease;
}

.login-privacy .login-link:hover {
  color: #4B4B4B;
}

.login-email-section {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.login-email-section .login-btn {
  margin-top: 12px;
}

.login-input-wrapper {
  position: relative;
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  max-width: 300px;
  align-self: center;
  padding: 0 2px;
  border-radius: 0;
  background: transparent;
  border: none;
  border-bottom: 1px solid rgba(0, 0, 0, 0.14);
  transition: border-color 0.25s ease;
}

.login-input-wrapper:hover {
  border-bottom-color: rgba(0, 0, 0, 0.26);
}

/* 聚焦时展开一条磨砂玻璃条：半透明墨色渐变 + 顶部白色高光 + 背景模糊，
   第一层是主体，第二层是宽度 2 倍的高光带，靠 background-position 从左扫到右 */
.login-input-wrapper::after {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  bottom: -1px;
  height: 3px;
  border-radius: 3px;
  background-image:
    linear-gradient(100deg,
      rgba(26, 26, 26, 0) 0%,
      rgba(26, 26, 26, 0.18) 15%,
      rgba(26, 26, 26, 0.32) 50%,
      rgba(26, 26, 26, 0.18) 85%,
      rgba(26, 26, 26, 0) 100%),
    linear-gradient(100deg,
      rgba(255, 255, 255, 0) 35%,
      rgba(255, 255, 255, 0.7) 50%,
      rgba(255, 255, 255, 0) 65%);
  background-size: 100% 100%, 200% 100%;
  background-position: 0 0, 160% 0;
  background-repeat: no-repeat;
  -webkit-backdrop-filter: blur(10px) saturate(160%);
  backdrop-filter: blur(10px) saturate(160%);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.55),
    inset 0 -1px 0 rgba(26, 26, 26, 0.06),
    0 2px 10px rgba(26, 26, 26, 0.08);
  transform: scaleX(0);
  transform-origin: center;
  opacity: 0;
  transition: transform 0.38s cubic-bezier(0.16, 1, 0.3, 1), opacity 0.38s ease;
  pointer-events: none;
}

.login-input-icon {
  flex: 0 0 auto;
  width: 18px;
  height: 18px;
  color: var(--rl-text-3);
  transition: color 0.25s ease;
}

.login-input-wrapper:focus-within {
  border-bottom-color: transparent;
}

.login-input-wrapper:focus-within::after {
  transform: scaleX(1);
  opacity: 1;
  background-position: 0 0, -60% 0;
  transition:
    transform 0.38s cubic-bezier(0.16, 1, 0.3, 1),
    opacity 0.38s ease,
    background-position 0.9s cubic-bezier(0.33, 1, 0.68, 1) 0.12s;
}

.login-input-wrapper:focus-within .login-input-icon {
  color: var(--text-primary);
}

.login-input-wrapper .login-input {
  border: none;
  background: transparent !important;
}

/* main.css 有 input:focus 的全局 border-color 与 input[type=email] 的圆角 !important，
   特异性远高于本组件样式，这里必须同样用 !important 把横线形态压回来 */
.login-input,
.login-input:hover,
.login-input:focus,
.login-input:focus-visible,
.login-input:active {
  flex: 1 1 auto;
  min-width: 0;
  width: 100%;
  /* 右侧留出与左侧图标同宽的空白（图标 18px + 间距 10px），
     使居中后的文字在整条下划线上真正居中，而不是偏右 14px */
  padding: 13px 28px 13px 0 !important;
  border: 0 !important;
  border-radius: 0 !important;
  background: transparent !important;
  background-color: transparent !important;
  font-size: 15px;
  font-weight: 500;
  letter-spacing: 0.005em;
  color: var(--text-primary);
  caret-color: #1A1A1A;
  outline: none !important;
  box-shadow: none !important;
  box-sizing: border-box;
  text-align: center;
  appearance: none;
  transition: none;
}

.login-input-wrapper .login-input:focus {
  box-shadow: none;
}

.login-input::placeholder {
  color: #A5A5AA;
  font-weight: 400;
  letter-spacing: 0.01em;
}

.login-input::selection {
  background: rgba(26, 26, 26, 0.12);
  color: var(--text-primary);
}

/* 自动填充的浅黄底会破坏无底色输入框，用大内阴影盖回透明 */
.login-input:-webkit-autofill,
.login-input:-webkit-autofill:hover,
.login-input:-webkit-autofill:focus {
  -webkit-text-fill-color: var(--text-primary);
  -webkit-box-shadow: 0 0 0 1000px #ffffff inset;
  transition: background-color 9999s ease-out 0s;
  caret-color: #1A1A1A;
}

.login-error-msg {
  width: 100%;
  padding: 10px 14px;
  margin-bottom: 12px;
  background: #FEF2F2;
  color: #DC2626;
  font-size: 13px;
  font-weight: 500;
  border-radius:12px;
  text-align: center;
  border: 1px solid #FECACA;
  animation: shake 0.3s ease;
}

@keyframes shake {
  0%, 100% { transform: translateX(0); }
  25% { transform: translateX(-4px); }
  75% { transform: translateX(4px); }
}

.login-btn {
  width: 100%;
  min-width: 120px;
  padding: 14px 24px;
  border: none;
  border-radius: 999px;
  background: #1A1A1A;
  color: #fff;
  font-size: var(--text-body);
  font-weight: var(--weight-medium);
  cursor: pointer;
  transition: background 0.2s ease, opacity 0.2s ease, transform 0.2s ease;
  align-self: stretch;
}

.login-btn:hover:not(:disabled) {
  background: #000000;
}

.login-btn:active:not(:disabled) {
  transform: translateY(1px);
}

.login-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.login-btn-code {
  width: auto;
  min-width: 0;
  align-self: center;
  padding: 10px 22px;
  font-size: 13px;
  letter-spacing: 0.02em;
}

.login-btn-outline {
  background: transparent;
  color: var(--rl-amber);
  border: 1px solid rgba(255, 36, 66, 0.3);
}

.login-btn-outline:hover:not(:disabled) {
  background: rgba(75, 75, 75, 0.06);
}

.login-back {
  text-align: center;
  margin-top: 20px;
}

.login-link {
  background: none;
  border: none;
  color: var(--rl-amber);
  font-size: 13px;
  cursor: pointer;
  padding: 0;
}

.login-link:hover {
  text-decoration: underline;
}

.login-error {
  margin-top: 14px;
  padding: 8px 12px;
  border-radius: 999px;
  background: rgba(239, 68, 68, 0.08);
  color: var(--rl-red);
  font-size: 13px;
  text-align: center;
}

.code-sent-to {
  font-size: 13px;
  color: var(--text-secondary);
  text-align: center;
  margin-bottom: 0;
}

.code-boxes {
  display: flex;
  justify-content: center;
  gap: 8px;
  flex-wrap: nowrap;
  max-width: 320px;
  margin: 0 auto;
}.code-box-wrapper {
  border-radius:10px;
  padding: 0;
  background: #F5F5F7;
  border: 1px solid rgba(0, 0, 0, 0.06);
  flex: 0 0 auto;
  transition: background 0.2s ease, border-color 0.2s ease, box-shadow 0.2s ease;
}

.code-box-wrapper:focus-within {
  background: #ffffff;
  border-color: rgba(0, 0, 0, 0.16);
  box-shadow: 0 0 0 3px rgba(0, 0, 0, 0.05);
}

.code-box-wrapper .code-box {
  border: none;
}

.code-box {
  width: 44px;
  height: 50px;
  border: 1px solid rgba(148, 163, 184, 0.3);
  border-radius:10px;
  background: transparent !important;
  text-align: center;
  font-size: 20px;
  font-weight: var(--weight-medium);
  color: var(--text-primary);
  outline: none;
  transition: border-color 0.2s ease, box-shadow 0.2s ease;
  caret-color: var(--rl-amber);
  letter-spacing: 4px;
}

.code-box-wrapper .code-box:focus {
  box-shadow: none;
}

.code-actions {
  display: flex;
  justify-content: space-between;
  margin-top: 0;
}

.code-actions .login-link:disabled {
  color: var(--rl-text-3);
  cursor: not-allowed;
}

.code-actions .login-link:disabled:hover {
  text-decoration: none;
}

.qr-placeholder {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 180px;
}

.qr-offline {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 180px;
  gap: 8px;
  color: var(--rl-text-3);
}

.qr-offline svg {
  color: var(--rl-red);
  margin-bottom: 4px;
}

.qr-offline-text {
  font-size: 15px;
  font-weight: var(--weight-medium);
  color: var(--text-secondary);
  margin: 0;
}

.qr-offline-hint {
  font-size: 12px;
  color: var(--rl-text-3);
  margin: 0;
}

.qr-offline-cmd {
  font-size: 11px;
  background: rgba(148, 163, 184, 0.1);
  border: 1px solid rgba(148, 163, 184, 0.2);
  border-radius:12px;
  padding: 6px 12px;
  color: var(--rl-text-3);
  font-family: 'Consolas', 'Monaco', monospace;
  word-break: break-all;
}

.qr-spin {
  animation: qr-spin-anim 1s linear infinite;
  color: rgba(255, 36, 66, 0.3);
}

@keyframes qr-spin-anim {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.qr-display {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 14px;
}

.qr-image {
  width: 140px;
  height: 140px;
  border-radius:20px;
  border: 1px solid rgba(253, 186, 116, 0.3);
}

.qr-status {
  text-align: center;
}

.qr-hint {
  font-size: 13px;
  color: var(--text-secondary);
}

.qr-hint-scanned {
  color: var(--rl-amber);
}

.qr-hint-done {
  color: var(--rl-amber);
}

.qr-hint-expired {
  color: var(--rl-text-3);
}

.manual-input {
  margin-top: 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.policy-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.3);
  backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  padding: 20px;
}

.policy-card {
  width: 100%;
  max-width: 380px;
  max-height: 80vh;
  background: rgba(255, 255, 255, 0.95);
  border-radius:24px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.12);
}

.policy-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 18px 20px 14px;
  border-bottom: 1px solid rgba(148, 163, 184, 0.15);
}

.policy-title {
  font-size: 15px;
  font-weight: var(--weight-medium);
  color: var(--text-primary);
  margin: 0;
}

.policy-close {
  background: none;
  border: none;
  color: var(--rl-text-3);
  cursor: pointer;
  padding: 4px;
  border-radius:12px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: color 0.2s, background 0.2s;
}

.policy-close:hover {
  color: var(--rl-text-3);
  background: rgba(148, 163, 184, 0.1);
}

.policy-body {
  flex: 1;
  overflow-y: auto;
  padding: 16px 20px;
  font-size: 13px;
  color: var(--rl-text-3);
  line-height: 1.7;
}

.policy-body h4 {
  font-size: 13px;
  font-weight: var(--weight-medium);
  color: var(--text-primary);
  margin: 14px 0 6px;
}

.policy-body h4:first-child {
  margin-top: 0;
}

.policy-body p {
  margin: 4px 0;
}

.policy-footer {
  padding: 14px 20px 18px;
  border-top: 1px solid rgba(148, 163, 184, 0.15);
  text-align: center;
}

.policy-agree-btn {
  padding: 10px 32px;
  border: none;
  border-radius: 999px;
  background: var(--rl-amber);
  color: #fff;
  font-size: var(--text-body);
  font-weight: var(--weight-medium);
  cursor: pointer;
  transition: background 0.2s;
}

.policy-agree-btn:hover {
  background: #000000;
}

.policy-fade-enter-active,
.policy-fade-leave-active {
  transition: opacity 0.25s ease;
}

.policy-fade-enter-active .policy-card,
.policy-fade-leave-active .policy-card {
  transition: transform 0.25s ease, opacity 0.25s ease;
}

.policy-fade-enter-from {
  opacity: 0;
}

.policy-fade-enter-from .policy-card {
  transform: translateY(20px) scale(0.97);
  opacity: 0;
}

.policy-fade-leave-to {
  opacity: 0;
}

.policy-fade-leave-to .policy-card {
  transform: translateY(10px) scale(0.98);
  opacity: 0;
}

</style>