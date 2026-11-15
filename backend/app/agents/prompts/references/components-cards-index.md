## 1. 卡片组件库（5种变体）

适用场景：`卡片` `功能展示` `列表` `核心卖点`

根据信息展示场景选用不同卡片风格。

---

### 1A. 杂志裁切风卡片


```html
```

```css
```


### 1B. 编号主导卡片


```html
```

```css
```

**变体**: 编号颜色按品牌三色轮换（蓝/黄/红）
```css
```


### 1C. 标签卡片


```html
```

```css
```


### 1D. 侧边icon卡片


```html
```

```css
```


### 1E. 观点/洞察卡片（4种变体）




#### 1E-A. 杂志大引号 Editorial Quote


```html
```

```css
```


#### 1E-B. 手写批注 Handwritten Note


```html
```

```css
```

**变体标签**: `Key Insight ✦` / `注意 ⚡` / `核心原则 🔑`


#### 1E-C. 荧光笔高亮 Highlighter


```html
```

```css
```


#### 1E-D. 终端命令风 Terminal Style


```html
```

```css
```

**使用建议**: 1E-A适合核心洞察/长引用，1E-B适合提示/注意事项，1E-C适合金句强调，1E-D适合技术文章


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


```html
```

```css
```

### 3B. 手写批注风


```html
```

```css
```

**变体标签**: `Key Insight ✦` / `注意 ⚡` / `核心原则 🔑`

### 3C. 纯排版强调


```html
```

```css
```

**使用建议**: 3A适合长引用/关键洞察，3B适合提示/注意事项，3C适合短金句/核心原则


## 5. 代码面板（4种变体）


适用场景：`代码展示` `终端` `技术教程` `CLI`
代码/配置/命令展示块。根据页面调性选用不同风格。

---

### 5A. macOS窗口 Window Frame


```html
```

```css
```

**语法高亮色**: `.kw { color: #bb9af7; }` `.fn { color: #7aa2f7; }` `.str { color: #9ece6a; }` `.cm { color: #565f89; }`


### 5B. 笔记本纸 Notebook Paper


```html
```

```css
```

**语法高亮色**: `.kw { color: var(--blue); font-weight: 700; }` `.str { color: var(--red); }` `.cm { color: var(--ink-faint); }`


### 5C. 打字机 Typewriter


```html
```

```css
```


### 5D. 极简白底 Clean White


```html
```

```css
```

**使用建议**: 5A适合真实代码展示，5B适合笔记/伪代码，5C适合流程/步骤输出，5D适合嵌入正文的轻量代码


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


```html

```

```css
```


### 12B. 手写笔记 Handwritten Notes


```html
```

```css
```


### 12C. 表格对比 Table View


```html
```

```css
```


### 12D. 印章边框 Stamped Frame


```html
```

```css
```

**使用建议**: 12A适合正式规范/准则，12B适合教程/轻松语境，12C适合多维度对比，12D适合设计文档/正式规范


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
