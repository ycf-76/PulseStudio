## 1. 卡片组件库（5种变体）

适用场景：`卡片` `功能展示` `列表` `核心卖点`

根据信息展示场景选用不同卡片风格。

---

### 1A. 杂志裁切风卡片

适用场景：需要大气场的标题卡、功能展示、首屏核心卖点

```html
<div class="magazine-card">
  <h3 class="card-title">大标题文字</h3>
  <p class="card-desc">描述文字</p>
  <span class="card-aux">AUX LABEL</span>
</div>
```

```css
.magazine-card {
  padding: 32px 24px 20px;
}
.magazine-card .card-title {
  font-family: 'Fraunces', 'Noto Serif SC', serif;
  font-size: 2rem;
  font-weight: 900;
  line-height: 1.15;
  color: var(--ink);
  min-height: 120px;
}
.magazine-card .card-desc {
  font-size: 0.8rem;
  color: var(--ink-light);
  margin-top: 16px;
}
.magazine-card .card-aux {
  font-size: 0.7rem;
  color: var(--blue);
  margin-top: 8px;
  font-weight: 500;
}
```

---

### 1B. 编号主导卡片

适用场景：步骤列表、问题清单、并列要点（需要视觉层次）

```html
<div class="number-card">
  <span class="card-number">01</span>
  <h3 class="card-title">标题</h3>
  <p class="card-desc">描述内容</p>
</div>
```

```css
.number-card {
  position: relative;
  padding: 28px 24px;
  background: #fff;
  border-radius: 12px;
  overflow: hidden;
  box-shadow: 0 1px 4px rgba(0,0,0,0.04);
}
.number-card .card-number {
  position: absolute;
  top: -10px;
  right: 10px;
  font-family: 'Fraunces', serif;
  font-size: 5.5rem;
  font-weight: 900;
  color: rgba(43, 127, 216, 0.08);
  line-height: 1;
  pointer-events: none;
}
.number-card .card-title {
  font-family: 'Noto Serif SC', serif;
  font-size: 1.05rem;
  font-weight: 700;
  margin-bottom: 8px;
  position: relative;
}
.number-card .card-desc {
  font-size: 0.85rem;
  color: var(--ink-light);
  margin-bottom: 12px;
  position: relative;
}
```

**变体**: 编号颜色按品牌三色轮换（蓝/黄/红）
```css
.number-card:nth-child(3n+1) .card-number { color: rgba(43, 127, 216, 0.08); }
.number-card:nth-child(3n+2) .card-number { color: rgba(244, 215, 88, 0.15); }
.number-card:nth-child(3n) .card-number { color: rgba(232, 74, 95, 0.1); }
```

---

### 1C. 标签卡片

适用场景：分类展示、功能矩阵、带标签的信息卡

```html
<div class="tag-card">
  <span class="card-pill card-pill--blue">分类标签</span>
  <h3 class="card-title">标题</h3>
  <p class="card-desc">描述内容</p>
</div>
```

```css
.tag-card {
  background: #fff;
  border-radius: 12px;
  padding: 20px;
  box-shadow: 0 1px 4px rgba(0,0,0,0.04);
}
.tag-card .card-pill {
  display: inline-block;
  padding: 3px 12px;
  border-radius: 20px;
  font-size: 0.7rem;
  font-weight: 500;
  margin-bottom: 14px;
}
/* pill颜色变体 */
.tag-card .card-pill--blue { background: rgba(43,127,216,0.1); color: var(--blue); }
.tag-card .card-pill--yellow { background: rgba(244,215,88,0.25); color: #9a8100; }
.tag-card .card-pill--red { background: rgba(232,74,95,0.1); color: var(--red); }
.tag-card .card-title {
  font-family: 'Noto Serif SC', serif;
  font-size: 1.05rem;
  font-weight: 700;
  margin-bottom: 8px;
}
.tag-card .card-desc {
  font-size: 0.85rem;
  color: var(--ink-light);
}
```

---

### 1D. 侧边icon卡片

适用场景：带图标的功能说明、能力展示、工具介绍

```html
<div class="icon-card">
  <div class="card-icon-area card-icon-area--blue">📦</div>
  <div class="card-content">
    <h3 class="card-title">标题</h3>
    <p class="card-desc">描述内容</p>
  </div>
</div>
```

```css
.icon-card {
  display: flex;
  align-items: stretch;
  background: #fff;
  border-radius: 12px;
  overflow: hidden;
  box-shadow: 0 1px 4px rgba(0,0,0,0.04);
  min-height: 130px;
}
.icon-card .card-icon-area {
  flex: 0 0 33%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 2.8rem;
}
/* icon区颜色变体 */
.icon-card .card-icon-area--blue { background: rgba(43,127,216,0.06); }
.icon-card .card-icon-area--yellow { background: rgba(244,215,88,0.12); }
.icon-card .card-icon-area--red { background: rgba(232,74,95,0.06); }
.icon-card .card-content {
  flex: 1;
  padding: 18px 16px;
  display: flex;
  flex-direction: column;
  justify-content: center;
}
.icon-card .card-title {
  font-family: 'Noto Serif SC', serif;
  font-size: 0.95rem;
  font-weight: 700;
  margin-bottom: 6px;
}
.icon-card .card-desc {
  font-size: 0.8rem;
  color: var(--ink-light);
}
```

---

### 1E. 观点/洞察卡片（4种变体）

适用场景：观点/洞察展示、Key Insights、重点提炼。根据内容调性选用不同风格。

> ⚠️ 禁止使用 border-left 竖线引用块样式（类似 Notion/飞书的左侧竖条引用块）。

---

#### 1E-A. 杂志大引号 Editorial Quote

白底圆角卡片 + oversized引号，适合长引用和核心洞察。

```html
<div class="quote-editorial">
  <h3 class="quote-title">观点标题</h3>
  <p class="quote-desc">观点描述内容</p>
  <span class="quote-source">— 来源</span>
</div>
```

```css
.quote-editorial {
  position: relative;
  padding: 48px 32px 28px 72px;
  background: white;
  border-radius: 20px;
  box-shadow: 0 8px 40px rgba(0,0,0,0.04);
}
.quote-editorial::before {
  content: '\201C';
  position: absolute;
  top: 12px;
  left: 24px;
  font-family: 'Fraunces', serif;
  font-size: 5rem;
  line-height: 1;
  color: var(--yellow);
  opacity: 0.8;
}
.quote-editorial .quote-title {
  font-family: 'Fraunces', serif;
  font-size: 1.3rem;
  font-weight: 700;
  font-style: italic;
  margin-bottom: 12px;
  color: var(--ink);
}
.quote-editorial .quote-desc {
  font-size: 0.88rem;
  color: var(--ink-light);
  line-height: 1.9;
}
.quote-editorial .quote-source {
  margin-top: 16px;
  font-size: 0.75rem;
  color: var(--ink-faint);
  font-family: 'Fira Code', monospace;
}
```

---

#### 1E-B. 手写批注 Handwritten Note

虚线边框 + Caveat字体标签，适合提示/注意事项/笔记感。

```html
<div class="quote-handwrite">
  <span class="quote-badge">Key Insight ✦</span>
  <h3 class="quote-title">观点标题</h3>
  <p class="quote-desc">观点描述内容</p>
</div>
```

```css
.quote-handwrite {
  position: relative;
  padding: 28px 24px;
  border: 2px dashed rgba(43,127,216,0.25);
  border-radius: 12px;
  background: white;
}
.quote-handwrite .quote-badge {
  position: absolute;
  top: -12px;
  left: 20px;
  font-family: 'Caveat', cursive;
  font-size: 1.1rem;
  font-weight: 700;
  color: var(--blue);
  background: var(--cream);
  padding: 0 10px;
}
.quote-handwrite .quote-title {
  font-family: 'Caveat', cursive;
  font-size: 1.6rem;
  font-weight: 700;
  color: var(--ink);
  margin-bottom: 8px;
  transform: rotate(-0.5deg);
}
.quote-handwrite .quote-desc {
  font-size: 0.88rem;
  color: var(--ink-light);
  line-height: 1.8;
}
```

**变体标签**: `Key Insight ✦` / `注意 ⚡` / `核心原则 🔑`

---

#### 1E-C. 荧光笔高亮 Highlighter

标题自带荧光笔底色，适合强调金句和核心论点。

```html
<div class="quote-highlight">
  <span class="quote-title">核心金句内容</span>
  <p class="quote-desc">补充说明文字</p>
</div>
```

```css
.quote-highlight {
  padding: 24px 28px;
  position: relative;
}
.quote-highlight .quote-title {
  font-family: 'Noto Serif SC', serif;
  font-size: 1.2rem;
  font-weight: 900;
  margin-bottom: 12px;
  display: inline;
  background: linear-gradient(to top, rgba(244,215,88,0.5) 45%, transparent 45%);
  line-height: 1.8;
  padding: 2px 4px;
}
.quote-highlight .quote-desc {
  font-size: 0.88rem;
  color: var(--ink-light);
  line-height: 1.9;
  margin-top: 16px;
}
```

---

#### 1E-D. 终端命令风 Terminal Style

暗色终端风格，适合技术类观点、开发者语境。

```html
<div class="quote-terminal">
  <div class="quote-toolbar">
    <span class="dot"></span><span class="dot"></span><span class="dot"></span>
  </div>
  <div class="quote-body">
    <div class="quote-prompt">$ cola insight --topic=主题</div>
    <h3 class="quote-title">观点标题</h3>
    <p class="quote-desc">观点描述内容</p>
  </div>
</div>
```

```css
.quote-terminal {
  background: #1e1e2e;
  border-radius: 12px;
  overflow: hidden;
  font-family: 'Fira Code', monospace;
}
.quote-terminal .quote-toolbar {
  background: #313244;
  padding: 8px 16px;
  display: flex;
  gap: 6px;
}
.quote-terminal .dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
}
.quote-terminal .dot:nth-child(1) { background: var(--red); }
.quote-terminal .dot:nth-child(2) { background: var(--yellow); }
.quote-terminal .dot:nth-child(3) { background: #4ade80; }
.quote-terminal .quote-body {
  padding: 20px 24px;
}
.quote-terminal .quote-prompt {
  color: #4ade80;
  font-size: 0.75rem;
  margin-bottom: 8px;
}
.quote-terminal .quote-title {
  color: #f1f5f9;
  font-size: 0.9rem;
  font-weight: 500;
  margin-bottom: 6px;
}
.quote-terminal .quote-desc {
  color: #94a3b8;
  font-size: 0.8rem;
  line-height: 1.8;
}
```

**使用建议**: 1E-A适合核心洞察/长引用，1E-B适合提示/注意事项，1E-C适合金句强调，1E-D适合技术文章

---

## 2. Section Header（数字+标题组合）


适用场景：`section分隔` `章节标题` `序号引导`
大装饰数字 + 正式标题，制造视觉层级。

```html
<div class="section-header">
  <span class="section-number">01</span>
  <h2 class="section-title">Section标题</h2>
</div>
```

```css
.section-number {
  font-family: 'Fraunces', serif;
  font-style: italic;
  font-size: clamp(1.4rem, 3vw, 2rem);
  color: var(--blue, #2B7FD8);
  opacity: 0.3;
  display: block;
  margin-bottom: 0.25rem;
}
.section-title {
  font-family: 'Noto Serif SC', serif;
  font-weight: 900;
  font-size: clamp(1.4rem, 3vw, 2.2rem);
  color: var(--ink, #1A1A2E);
}
```

---

## 3. 引用块 / Key Insight


适用场景：`引用` `金句` `洞察` `重点提炼`
三种风格可选（不使用传统左色条）：

### 3A. 极简留白式金句

黄色短线分割 + 蓝色大字金句，极简但有力。适合观点页、引用页、总结页。

```html
<div class="key-insight quote-minimal">
  <p class="quote-body">正文段落 / 背景说明</p>
  <div class="quote-rule"></div>
  <p class="quote-conclusion">金句 / 结论 / 核心观点</p>
</div>
```

```css
.quote-minimal {
  text-align: left;
}
.quote-minimal .quote-body {
  font-family: 'Noto Serif SC', serif;
  font-weight: 700;
  font-size: 1.1rem;
  line-height: 2;
  color: var(--ink, #1A1A2E);
  margin-bottom: 2rem;
}
.quote-minimal .quote-body .hl {
  background: linear-gradient(transparent 60%, rgba(244,215,88,0.3) 60%);
  padding: 0 4px;
}
.quote-minimal .quote-rule {
  width: 60px;
  height: 3px;
  background: var(--yellow, #F4D758);
  margin-bottom: 2rem;
}
.quote-minimal .quote-conclusion {
  font-family: 'Noto Serif SC', serif;
  font-size: 1.2rem;
  font-weight: 900;
  color: var(--blue, #2B7FD8);
  line-height: 1.7;
}
```

### 3B. 手写批注风

虚线边框 + Caveat字体标签，笔记批注感。

```html
<div class="key-insight quote-note">
  <span class="quote-note-tag">Key Insight ✦</span>
  <p>重要观点或引用内容</p>
</div>
```

```css
.quote-note {
  position: relative;
  padding: 24px 28px;
  background: white;
  border-radius: 10px;
  border: 1.5px dashed rgba(43, 127, 216, 0.3);
}
.quote-note-tag {
  position: absolute;
  top: -10px;
  left: 20px;
  font-family: 'Caveat', cursive;
  font-size: 1rem;
  color: var(--blue, #2B7FD8);
  background: var(--cream, #fefcf6);
  padding: 0 8px;
  font-weight: 700;
}
.quote-note p {
  font-size: 0.93rem;
  line-height: 1.85;
  color: var(--ink-light, #4A4A5A);
}
```

**变体标签**: `Key Insight ✦` / `注意 ⚡` / `核心原则 🔑`

### 3C. 纯排版强调

极短装饰线 + 斜体加粗，极简，内容即风格。

```html
<div class="key-insight quote-typo">
  <p>重要观点或引用内容</p>
</div>
```

```css
.quote-typo {
  position: relative;
  padding: 20px 28px 20px 40px;
}
.quote-typo::before {
  content: '';
  position: absolute;
  left: 12px;
  top: 50%;
  transform: translateY(-50%);
  width: 3px;
  height: 60%;
  background: var(--yellow, #F4D758);
  border-radius: 2px;
}
.quote-typo p {
  font-size: 1rem;
  line-height: 1.85;
  color: var(--ink, #1A1A2E);
  font-weight: 500;
  font-style: italic;
}
```

**使用建议**: 3A适合长引用/关键洞察，3B适合提示/注意事项，3C适合短金句/核心原则

---

## 5. 代码面板（4种变体）


适用场景：`代码展示` `终端` `技术教程` `CLI`
代码/配置/命令展示块。根据页面调性选用不同风格。

---

### 5A. macOS窗口 Window Frame

经典macOS窗口chrome，适合展示真实代码片段。

```html
<div class="code-macos">
  <div class="code-titlebar">
    <div class="code-dots">
      <span class="dot"></span><span class="dot"></span><span class="dot"></span>
    </div>
    <span class="code-filename">filename.ts</span>
  </div>
  <div class="code-body">
    <pre><code>// 代码内容</code></pre>
  </div>
</div>
```

```css
.code-macos {
  border-radius: 12px;
  overflow: hidden;
  box-shadow: 0 12px 48px rgba(0,0,0,0.15);
}
.code-macos .code-titlebar {
  background: #2d2d3a;
  padding: 12px 16px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.code-macos .code-dots { display: flex; gap: 6px; }
.code-macos .dot { width: 12px; height: 12px; border-radius: 50%; }
.code-macos .dot:nth-child(1) { background: #ff5f56; }
.code-macos .dot:nth-child(2) { background: #ffbd2e; }
.code-macos .dot:nth-child(3) { background: #27c93f; }
.code-macos .code-filename {
  flex: 1;
  text-align: center;
  font-family: 'Fira Code', monospace;
  font-size: 0.7rem;
  color: var(--ink-faint);
}
.code-macos .code-body {
  background: #1a1b26;
  padding: 24px;
}
.code-macos pre {
  font-family: 'Fira Code', monospace;
  font-size: 0.82rem;
  line-height: 1.9;
  color: #a9b1d6;
  margin: 0;
}
```

**语法高亮色**: `.kw { color: #bb9af7; }` `.fn { color: #7aa2f7; }` `.str { color: #9ece6a; }` `.cm { color: #565f89; }`

---

### 5B. 笔记本纸 Notebook Paper

温暖纸质感，适合伪代码、Markdown笔记、配置说明。

```html
<div class="code-notebook">
  <div class="code-holes"></div>
  <div class="code-tab">✏️ 文件名</div>
  <pre><code>内容</code></pre>
</div>
```

```css
.code-notebook {
  background: #fffef8;
  border: 1px solid #e8e4d9;
  border-radius: 4px;
  padding: 0;
  position: relative;
  box-shadow: 2px 3px 0 #e8e4d9;
}
.code-notebook .code-holes {
  position: absolute;
  left: 28px;
  top: 0;
  bottom: 0;
  width: 1px;
  background: rgba(232,74,95,0.2);
}
.code-notebook .code-tab {
  padding: 8px 16px 8px 48px;
  border-bottom: 1px solid #e8e4d9;
  font-family: 'Caveat', cursive;
  font-size: 1rem;
  color: var(--blue);
  font-weight: 700;
}
.code-notebook pre {
  font-family: 'Fira Code', monospace;
  font-size: 0.8rem;
  line-height: 2.2;
  color: var(--ink);
  margin: 0;
  padding: 16px 20px 16px 48px;
  background-image: repeating-linear-gradient(
    transparent, transparent 31px, rgba(43,127,216,0.05) 31px, rgba(43,127,216,0.05) 32px
  );
}
```

**语法高亮色**: `.kw { color: var(--blue); font-weight: 700; }` `.str { color: var(--red); }` `.cm { color: var(--ink-faint); }`

---

### 5C. 打字机 Typewriter

复古打字机输出风格，适合流程描述、步骤输出。

```html
<div class="code-typewriter">
  <pre><code>内容
<span class="cursor"></span></code></pre>
</div>
```

```css
.code-typewriter {
  background: #f5f0e8;
  border: 2px solid #d4c9b5;
  padding: 32px 28px;
  position: relative;
}
.code-typewriter::before {
  content: 'TYPEWRITER OUTPUT';
  position: absolute;
  top: -11px;
  left: 20px;
  font-family: 'Fira Code', monospace;
  font-size: 0.6rem;
  letter-spacing: 2px;
  color: var(--ink-faint);
  background: #f5f0e8;
  padding: 0 8px;
}
.code-typewriter pre {
  font-family: 'Fira Code', monospace;
  font-size: 0.82rem;
  line-height: 2;
  color: var(--ink);
  margin: 0;
  letter-spacing: 0.5px;
}
.code-typewriter .kw { color: var(--blue); text-decoration: underline; text-underline-offset: 3px; }
.code-typewriter .str { color: var(--red); }
.code-typewriter .cm { color: var(--ink-faint); font-style: italic; }
.code-typewriter .cursor {
  display: inline-block;
  width: 8px;
  height: 1.1em;
  background: var(--ink);
  vertical-align: middle;
  animation: blink 1s step-end infinite;
}
@keyframes blink { 50% { opacity: 0; } }
```

---

### 5D. 极简白底 Clean White

白色背景极简风，适合轻量代码展示、嵌入正文。

```html
<div class="code-clean">
  <span class="code-corner-tag">JS</span>
  <pre><code>代码内容</code></pre>
</div>
```

```css
.code-clean {
  background: white;
  border-radius: 16px;
  padding: 32px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.04);
  position: relative;
}
.code-clean .code-corner-tag {
  position: absolute;
  top: 16px;
  right: 20px;
  font-family: 'Fira Code', monospace;
  font-size: 0.6rem;
  color: var(--ink-faint);
  padding: 2px 8px;
  border: 1px solid rgba(0,0,0,0.08);
  border-radius: 4px;
}
.code-clean pre {
  font-family: 'Fira Code', monospace;
  font-size: 0.82rem;
  line-height: 2;
  color: var(--ink);
  margin: 0;
}
.code-clean .kw { color: var(--blue); font-weight: 600; }
.code-clean .fn { color: #7c3aed; }
.code-clean .str { color: var(--red); }
.code-clean .cm { color: #94a3b8; }
.code-clean .highlight-line {
  background: rgba(43,127,216,0.06);
  margin: 0 -32px;
  padding: 0 32px;
  border-left: 3px solid var(--blue);
}
```

**使用建议**: 5A适合真实代码展示，5B适合笔记/伪代码，5C适合流程/步骤输出，5D适合嵌入正文的轻量代码

---

## 6. Pull Quote（大字引用+装饰引号）

用于强调核心金句。


适用场景：`引用` `金句` `单句强调` `人物语录`
```html
<div class="viral-pullquote">
  <p>核心金句内容</p>
</div>
```

```css
.viral-pullquote {
  padding: 36px 40px;
  background: var(--cream, #fefcf6);
  border-radius: 16px;
  border: 2px solid var(--yellow, #F4D758);
  position: relative;
}
.viral-pullquote::before {
  content: '"';
  font-family: 'Fraunces', serif;
  font-size: 5rem;
  color: var(--yellow, #F4D758);
  position: absolute;
  top: -10px; left: 20px;
  opacity: 0.5;
  line-height: 1;
}
.viral-pullquote p {
  font-family: 'Noto Serif SC', serif;
  font-size: clamp(1.1rem, 2vw, 1.4rem);
  font-weight: 700;
  line-height: 1.8;
}
```

---

## 7. 对话气泡组（Chat Bubbles）

适用场景：`对话展示` `AI聊天` `用户反馈` `口语化内容`

用户与AI的对话展示。

```html
<div class="chat-container">

适用场景：`对话展示` `AI聊天` `用户反馈` `口语化内容`
  <div class="chat-bubble user">用户说的话</div>
  <div class="chat-bubble ai">AI回复的内容</div>
</div>
```

```css
.chat-container {
  display: flex;
  flex-direction: column;
  gap: 16px;
  max-width: 640px;
}
.chat-bubble {
  padding: 14px 20px;
  border-radius: 18px;
  max-width: 85%;
  font-size: 0.9rem;
  line-height: 1.6;
}
.chat-bubble.user {
  background: var(--yellow, #F4D758);
  color: var(--ink, #1A1A2E);
  align-self: flex-end;
  border-bottom-right-radius: 4px;
}
.chat-bubble.ai {
  background: var(--blue, #2B7FD8);
  color: #fefcf6;
  align-self: flex-start;
  border-bottom-left-radius: 4px;
}
```

---

## 8. Tip Header（超大装饰数字+标题）

适用场景：`步骤标题` `数字装饰` `章节引导`

用超大半透明数字制造视觉冲击。

```html
<div class="tip-header">
  <span class="tip-number">01</span>
  <h2 class="tip-title">标题内容</h2>

适用场景：`步骤标题` `数字装饰` `章节引导`
</div>
```

```css
.tip-number {
  font-family: 'Fraunces', serif;
  font-size: clamp(3rem, 8vw, 7rem);
  font-weight: 900;
  color: var(--blue, #2B7FD8);
  opacity: 0.15;
  line-height: 0.85;
  display: block;
}
.tip-title {
  font-family: 'Noto Serif SC', serif;
  font-weight: 900;
  font-size: clamp(1.4rem, 3vw, 2.2rem);
  margin-top: -0.5rem;
}
```

---

## 12. Do/Don't对比列表（4种变体）

适用场景：`对比` `规范` `正反例` `注意事项`

红蓝对比的规范列表。根据页面风格选用不同展现形式。

---

### 12A. 卡片分栏 Card Columns

白色卡片 + 彩色底线，适合正式的规范/准则展示。

```html
<div class="compare-cards">
  <div class="compare-block compare-block--dont">
    <div class="compare-header">
      <span class="compare-icon">🚫</span>
      <span class="compare-label">Don't</span>
    </div>
    <ul>
      <li>不要做的事1</li>
      <li>不要做的事2</li>
    </ul>
  </div>
  <div class="compare-block compare-block--do">
    <div class="compare-header">

适用场景：`对比` `规范` `正反例` `注意事项`
      <span class="compare-icon">✅</span>
      <span class="compare-label">Do</span>
    </div>
    <ul>
      <li>应该做的事1</li>
      <li>应该做的事2</li>
    </ul>
  </div>
</div>
```

```css
.compare-cards {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 24px;
}
.compare-block {
  background: white;
  border-radius: 16px;
  padding: 24px;
  box-shadow: 0 4px 20px rgba(0,0,0,0.04);
}
.compare-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 20px;
  padding-bottom: 12px;
  border-bottom: 2px solid;
}
.compare-block--dont .compare-header { border-color: var(--red); }
.compare-block--do .compare-header { border-color: var(--blue); }
.compare-header .compare-icon { font-size: 1.3rem; }
.compare-header .compare-label {
  font-family: 'Fraunces', serif;
  font-size: 1rem;
  font-weight: 700;
}
.compare-block--dont .compare-label { color: var(--red); }
.compare-block--do .compare-label { color: var(--blue); }
.compare-block ul {
  list-style: none;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.compare-block li {
  font-size: 0.88rem;
  color: var(--ink-light);
  padding-left: 20px;
  position: relative;
  line-height: 1.6;
}
.compare-block--dont li::before { content: '×'; position: absolute; left: 0; color: var(--red); font-weight: 700; }
.compare-block--do li::before { content: '✓'; position: absolute; left: 0; color: var(--blue); font-weight: 700; }
```

---

### 12B. 手写笔记 Handwritten Notes

Caveat字体标题 + 虚线边框，适合轻松/教程类内容。

```html
<div class="compare-handwrite">
  <span class="compare-annotation">— 主题标注 ✏️</span>
  <div class="compare-titles">
    <div class="compare-col-title compare-col-title--dont">别这样 ✗</div>
    <div class="compare-col-title compare-col-title--do">要这样 ✓</div>
  </div>
  <div class="compare-items">
    <div class="compare-item compare-item--dont">不要做的事</div>
    <div class="compare-item">应该做的事</div>
    <!-- 更多成对条目 -->
  </div>
</div>
```

```css
.compare-handwrite {
  position: relative;
  background: white;
  border-radius: 16px;
  padding: 32px;
  border: 1.5px dashed rgba(0,0,0,0.12);
}
.compare-handwrite .compare-annotation {
  position: absolute;
  top: -12px;
  right: 24px;
  font-family: 'Caveat', cursive;
  font-size: 0.95rem;
  color: var(--ink-faint);
  background: var(--cream);
  padding: 0 8px;
  transform: rotate(2deg);
}
.compare-handwrite .compare-titles {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 24px;
  margin-bottom: 24px;
}
.compare-col-title {
  font-family: 'Caveat', cursive;
  font-size: 1.4rem;
  font-weight: 700;
}
.compare-col-title--dont { color: var(--red); }
.compare-col-title--do { color: var(--blue); }
.compare-handwrite .compare-items {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px 24px;
}
.compare-handwrite .compare-item {
  font-size: 0.88rem;
  color: var(--ink-light);
  line-height: 1.6;
  padding: 8px 0;
  border-bottom: 1px solid rgba(0,0,0,0.04);
}
.compare-handwrite .compare-item--dont { text-decoration: line-through; opacity: 0.6; }
```

---

### 12C. 表格对比 Table View

结构化表格风格，适合规格比较、功能矩阵。

```html
<div class="compare-table">
  <div class="compare-table-header">
    <span>实践</span>
    <span>Don't</span>
    <span>Do</span>
  </div>
  <div class="compare-table-row">
    <span class="compare-practice">具体实践描述</span>
    <span class="compare-status">⛔</span>
    <span class="compare-status">✅</span>
  </div>
  <!-- 更多行 -->
</div>
```

```css
.compare-table {
  border-radius: 12px;
  overflow: hidden;
  box-shadow: 0 2px 16px rgba(0,0,0,0.06);
}
.compare-table-header {
  display: grid;
  grid-template-columns: 1fr 80px 80px;
  background: var(--ink);
  padding: 12px 20px;
}
.compare-table-header span {
  font-family: 'Fira Code', monospace;
  font-size: 0.7rem;
  color: rgba(255,255,255,0.6);
  text-transform: uppercase;
  letter-spacing: 1px;
}
.compare-table-header span:not(:first-child) { text-align: center; }
.compare-table-row {
  display: grid;
  grid-template-columns: 1fr 80px 80px;
  padding: 14px 20px;
  background: white;
  border-bottom: 1px solid rgba(0,0,0,0.04);
  align-items: center;
}
.compare-table-row:last-child { border-bottom: none; }
.compare-table-row .compare-practice {
  font-size: 0.88rem;
  color: var(--ink-light);
}
.compare-table-row .compare-status {
  text-align: center;
  font-size: 1.1rem;
}
```

---

### 12D. 印章边框 Stamped Frame

实线边框 + 居中印章标签，适合正式文档/设计规范。

```html
<div class="compare-stamp">
  <div class="compare-stamp-col compare-stamp-col--dont">
    <span class="compare-stamp-label">AVOID</span>
    <ul>
      <li>不要做的事1</li>
      <li>不要做的事2</li>
    </ul>
  </div>
  <div class="compare-stamp-col compare-stamp-col--do">
    <span class="compare-stamp-label">PREFER</span>
    <ul>
      <li>应该做的事1</li>
      <li>应该做的事2</li>
    </ul>
  </div>
</div>
```

```css
.compare-stamp {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 24px;
}
.compare-stamp-col {
  background: white;
  border: 2px solid;
  padding: 28px 24px;
  position: relative;
  border-radius: 4px;
}
.compare-stamp-col--dont { border-color: var(--red); }
.compare-stamp-col--do { border-color: var(--blue); }
.compare-stamp-col .compare-stamp-label {
  position: absolute;
  top: -14px;
  left: 50%;
  transform: translateX(-50%);
  font-family: 'Fraunces', serif;
  font-size: 0.75rem;
  font-weight: 900;
  letter-spacing: 2px;
  text-transform: uppercase;
  padding: 2px 16px;
  background: var(--cream);
}
.compare-stamp-col--dont .compare-stamp-label { color: var(--red); }
.compare-stamp-col--do .compare-stamp-label { color: var(--blue); }
.compare-stamp-col ul {
  list-style: none;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-top: 8px;
}
.compare-stamp-col li {
  font-size: 0.85rem;
  color: var(--ink-light);
  line-height: 1.6;
  padding-left: 24px;
  position: relative;
}
.compare-stamp-col--dont li::before {
  content: '✕';
  position: absolute;
  left: 0;
  font-weight: 700;
  font-size: 0.8rem;
  color: var(--red);
}
.compare-stamp-col--do li::before {
  content: '◆';
  position: absolute;
  left: 0;
  font-size: 0.6rem;
  top: 4px;
  color: var(--blue);
}
```

**使用建议**: 12A适合正式规范/准则，12B适合教程/轻松语境，12C适合多维度对比，12D适合设计文档/正式规范

---

## 19. 对比表A：杂志Editorial对比

不对称grid布局（左0.35fr 右1fr），左边放大标题区（sticky），右边每行是Fraunces大号编号+两列对比值。暖色背景，无深色。

适用场景：两个事物的对比分析、before/after、竞品对比、架构拆解类页面

```html
<section class="section-editorial">
  <div class="container">
    <div class="editorial-layout">
      <div class="editorial-side">
        <span class="big-label">VS</span>
        <span class="section-number">04</span>
        <h2 class="section-title">对比标题</h2>
        <p class="editorial-subtitle">补充说明文字</p>
      </div>
      <div class="editorial-rows">
        <div class="editorial-row">
          <div class="editorial-num">01</div>
          <div class="editorial-col">
            <h4>对象A</h4>
            <p>值A</p>
          </div>
          <div class="editorial-col">
            <h4>对象B</h4>
            <p>值B</p>
          </div>
        </div>
        <!-- 更多行... -->
        <div style="margin-top: 8px;">
          <span class="editorial-dim">维度1 → 维度2 → 维度3</span>
        </div>
      </div>
    </div>
  </div>
</section>
```

```css
/* 杂志Editorial对比 */
.section-editorial {
  background: #faf6eb;
  padding: clamp(80px, 12vh, 160px) 0;
}
.section-editorial .section-number {
  font-family: 'Fraunces', serif;
  font-style: italic;
  font-size: clamp(1.4rem, 3vw, 2rem);
  color: var(--blue, #2B7FD8);
  opacity: 0.3;
  display: block;
  margin-bottom: 0.25rem;
}
.section-editorial .section-title {
  font-family: 'Noto Serif SC', serif;
  font-weight: 900;
  font-size: clamp(1.4rem, 3vw, 2.2rem);
  color: var(--ink, #1A1A2E);
}
.editorial-layout {
  display: grid;
  grid-template-columns: 0.35fr 1fr;
  gap: clamp(40px, 6vw, 100px);
  align-items: start;
}
.editorial-side {
  position: sticky;
  top: 100px;
}
.editorial-side .big-label {
  font-family: 'Fraunces', serif;
  font-size: clamp(3rem, 8vw, 6rem);
  font-weight: 900;
  color: var(--blue, #2B7FD8);
  opacity: 0.1;
  line-height: 0.85;
  display: block;
}
.editorial-side .editorial-subtitle {
  font-size: 0.9rem;
  color: var(--ink-light, #4A4A5A);
  margin-top: 1rem;
  line-height: 1.7;
}
.editorial-rows {
  display: flex;
  flex-direction: column;
}
.editorial-row {
  display: grid;
  grid-template-columns: 80px 1fr 1fr;
  gap: 16px;
  align-items: start;
  padding: 28px 0;
  border-bottom: 1px solid rgba(0,0,0,0.05);
}
.editorial-row:last-child { border-bottom: none; }
.editorial-num {
  font-family: 'Fraunces', serif;
  font-size: 2.5rem;
  font-weight: 700;
  color: var(--yellow, #F4D758);
  line-height: 1;
}
.editorial-col h4 {
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 1.5px;
  color: var(--ink-faint, #8A8A9A);
  margin-bottom: 4px;
}
.editorial-col p {
  font-size: 0.95rem;
  color: var(--ink, #1A1A2E);
}
.editorial-dim {
  font-family: 'Caveat', cursive;
  font-size: 1.1rem;
  color: var(--ink-faint, #8A8A9A);
  margin-top: 8px;
}

@media (max-width: 640px) {
  .editorial-layout {
    grid-template-columns: 1fr;
  }
  .editorial-side {
    position: static;
  }
  .editorial-row {
    grid-template-columns: 50px 1fr;
  }
  .editorial-row .editorial-col:last-child {
    grid-column: 2;
  }
}
```

---

## 20. 对比表B：圆点标识表

极简风格，无边框无线条。每行前面一个品牌色圆点做标识，三列：维度 | 值A | 值B。只靠字体粗细和颜色区分层次。

适用场景：简洁数据对比、功能列表、规格比较、sidebar信息面板

```html
<div class="dot-table">
  <div class="dot-row">
    <div class="dot-dim">维度名</div>
    <div class="dot-val">值A</div>
    <div class="dot-val">值B</div>
  </div>
  <div class="dot-row">
    <div class="dot-dim">维度名</div>
    <div class="dot-val">值A</div>
    <div class="dot-val">值B</div>
  </div>
  <!-- 更多行... -->
</div>
```

```css
/* 圆点标识表 — inspired by milan morning light */
.dot-table {
  display: flex;
  flex-direction: column;
}
.dot-row {
  display: grid;
  grid-template-columns: 120px 1fr 1fr;
  gap: 16px;
  padding: 16px 0;
  align-items: baseline;
}
.dot-dim {
  font-size: 0.8rem;
  font-weight: 500;
  color: var(--ink-faint, #8A8A9A);
  position: relative;
  padding-left: 16px;
}
.dot-dim::before {
  content: '';
  position: absolute;
  left: 0;
  top: 50%;
  transform: translateY(-50%);
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--yellow, #F4D758);
}
.dot-row:nth-child(2n) .dot-dim::before { background: var(--blue, #2B7FD8); }
.dot-row:nth-child(3n) .dot-dim::before { background: var(--red, #E84A5F); }
.dot-val {
  font-size: 0.9rem;
  color: var(--ink, #1A1A2E);
}

@media (max-width: 640px) {
  .dot-row {
    grid-template-columns: 1fr;
    gap: 4px;
    padding: 12px 0 12px 16px;
    border-left: 2px solid rgba(0,0,0,0.05);
  }
}
```

---

## 25. 打字机/终端风

适用场景：技术向内容、代码展示、CLI演示、极客感叙事、markdown风格呈现。

```html
<section class="typewriter-section">
  <div class="type-header">cat_care_guide.md</div>
  <div class="type-content">
    <p>> 品种：英国短毛猫</p>
    <p>> 性格：温顺、独立、不粘人但恋家</p>
    <p>> 体重：公猫 4-8kg</p>
    <p>> 寿命：12-20 年</p>
    <p class="type-blank"></p>
    <p>## 饮食</p>
    <p>- 蛋白质 30-40%（动物蛋白）</p>
    <p>- 湿粮占比 50-70%</p>
    <p>- 每日 180-300 大卡</p>
    <p class="type-blank"></p>
    <p class="type-warn">⚠️ 极易发胖，运动=续命</p>
    <span class="type-cursor"></span>
  </div>
</section>
```

```css
.typewriter-section {
  background: #faf8f5;
  color: #2a2a2a;
  padding: clamp(60px, 10vh, 120px) clamp(40px, 6vw, 100px);
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  justify-content: center;
  /* 可选：网格纸纹理背景 */
  background-image: url("data:image/svg+xml,%3Csvg width='100' height='100' xmlns='http://www.w3.org/2000/svg'%3E%3Crect width='100' height='100' fill='none' stroke='%23e8e4dc' stroke-width='0.5'/%3E%3C/svg%3E");
}
.type-header {
  font-family: 'DM Mono', 'Fira Code', monospace;
  font-size: 1.8rem;
  font-weight: 400;
  border-bottom: 2px solid #333;
  display: inline-block;
  padding-bottom: 4px;
  margin-bottom: 2rem;
}
.type-content {
  font-family: 'DM Mono', 'Fira Code', monospace;
  font-size: 0.88rem;
  line-height: 2.4;
  max-width: 650px;
}
.type-content p {
  margin: 0;
}
.type-blank {
  height: 1.2em;
}
.type-warn {
  color: var(--red, #E84A5F);
  font-weight: 700;
}
.type-cursor {
  display: inline-block;
  width: 8px;
  height: 1.2em;
  background: #333;
  animation: cursor-blink 1s infinite;
  vertical-align: middle;
  margin-left: 4px;
}
@keyframes cursor-blink {
  0%, 50% { opacity: 1; }
  51%, 100% { opacity: 0; }
}
```

**使用原则**：
- 等宽字体只用 `DM Mono` 或 `Fira Code`
- 行距宽松（line-height ≥ 2），营造逐行阅读的节奏感
- 用 `>` 前缀模拟引用/输入，用 `##` 模拟标题层级
- 闪烁光标用CSS animation实现，增加“正在输入”的动态感
- 适合技术教程、开发者向内容、CLI风格展示、极客叙事

---
