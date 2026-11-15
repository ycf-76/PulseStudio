/* Studio P0 spike 场景驱动（Task 6 fixture）。
 *
 * 契约：
 *   - window.seekTo(t) 把画面设到第 t 秒的确切状态。纯函数：禁 Math.random /
 *     Date / performance.now 参与画面计算。返回 Promise 等底帧/人物两层素材
 *     decode 完成（Playwright evaluate 会 await 返回的 Promise，保证渲染器
 *     逐帧截图不会拍到半解码的旧图）。
 *   - 动效全部按 t 手写数学插值（easeOutCubic），禁用 CSS transition/animation
 *     —— 渲染态下 transition 依赖真实流逝时间，逐帧截图会产生不确定帧。
 *   - 渲染模式（URL 无参数）：只暴露 seekTo，不启动任何循环。
 *   - 预览模式（URL 带 ?preview）：rAF 实时循环调 seekTo（仅人工预览用，
 *     performance.now 只出现在预览循环里，不进 seekTo）。
 */
(function () {
  "use strict";
  var M = window.STUDIO_SCENE_MANIFEST;
  var world = document.getElementById("world");
  var baseEl = document.getElementById("base");
  var personEl = document.getElementById("person");
  var titleEl = document.getElementById("title");
  var titleText = document.getElementById("title_text");
  var cardEl = document.getElementById("card");
  var cardText = document.getElementById("card_text");

  var TITLE_IN = 0.4;  // 标题条滑入时长（秒），动效参数来自任务书
  var CARD_IN = 0.3;   // 卡片弹入时长（秒）
  var CARD_OUT = 0.3;  // 卡片弹出时长（秒）

  // cover 缩放：人物与底帧同容器同几何，天然对齐无重影
  var scale = Math.max(M.canvas.w / M.base.w, M.canvas.h / M.base.h);
  world.style.width = M.base.w + "px";
  world.style.height = M.base.h + "px";
  world.style.transform = "translate(-50%, -50%) scale(" + scale + ")";
  baseEl.style.width = M.base.w + "px";
  baseEl.style.height = M.base.h + "px";
  personEl.style.width = M.person.w + "px";
  personEl.style.height = M.person.h + "px";

  function clamp01(x) { return x < 0 ? 0 : (x > 1 ? 1 : x); }
  function easeOutCubic(p) { var q = 1 - p; return 1 - q * q * q; }
  function pad6(i) { return ("00000" + i).slice(-6); }
  function frameSrc(dir, pattern, i) { return dir + "/" + pattern.replace("%06d", pad6(i)); }

  var curBase = -1;
  var curPerson = -1;
  var curTitleBeat = null;
  var curCard = null;

  window.seekTo = function (t) {
    if (t < 0) t = 0;
    if (t > M.duration) t = M.duration;

    // 1) 底帧层：index = floor(t*fps)，clamp 到 [0, count-1]
    var bi = Math.floor(t * M.base.fps);
    bi = Math.max(0, Math.min(M.base.count - 1, bi));
    if (bi !== curBase) { baseEl.src = frameSrc(M.base.dir, M.base.pattern, bi); curBase = bi; }

    // 2) 人物层：取与底帧同一视频时刻的抠像帧（round(bi*person.fps/base.fps)），
    //    两层像素同一瞬 → 人物层与底帧同步无重影
    var pi = Math.round(bi * M.person.fps / M.base.fps);
    pi = Math.max(0, Math.min(M.person.count - 1, pi));
    if (pi !== curPerson) { personEl.src = frameSrc(M.person.dir, M.person.pattern, pi); curPerson = pi; }

    // 3) 身后层：当前 beat 的标题条，按 t 手写 easeOutCubic 滑入（无 CSS transition）
    var beat = null;
    for (var k = 0; k < M.beats.length; k++) {
      if (t >= M.beats[k].t_start) beat = M.beats[k];
    }
    if (beat) {
      if (curTitleBeat !== beat) { titleText.textContent = beat.title; curTitleBeat = beat; }
      var e = easeOutCubic(clamp01((t - beat.t_start) / TITLE_IN));
      titleEl.style.transform = "translateX(" + (-108 * (1 - e)) + "%)";
      titleEl.style.opacity = String(e);
    } else {
      titleEl.style.opacity = "0";
      curTitleBeat = null;
    }

    // 4) 前景层：触发卡片（t≥trigger.t 弹入，所属 beat 结束前弹出），同手写插值
    var trig = null;
    for (var j = 0; j < M.triggers.length; j++) {
      if (t >= M.triggers[j].t && t < M.triggers[j].until) trig = M.triggers[j];
    }
    if (trig) {
      if (curCard !== trig) { cardText.textContent = trig.text; curCard = trig; }
      var pin = easeOutCubic(clamp01((t - trig.t) / CARD_IN));
      var pout = clamp01((t - (trig.until - CARD_OUT)) / CARD_OUT);
      var dy = 26 * (1 - pin) - 30 * pout;
      cardEl.style.transform = "translate(-50%, " + dy + "px) scale(" + (0.8 + 0.2 * pin) + ")";
      cardEl.style.opacity = String(pin * (1 - pout));
    } else {
      cardEl.style.opacity = "0";
      curCard = null;
    }

    // 等两层素材 decode 完成（渲染器逐帧截图前必须拿到完整像素）
    var d1 = baseEl.decode ? baseEl.decode() : Promise.resolve();
    var d2 = personEl.decode ? personEl.decode() : Promise.resolve();
    return Promise.all([d1, d2]);
  };

  window.seekTo(0);

  // 预览模式（?preview）：rAF 实时循环，循环播放
  if (new URLSearchParams(window.location.search).has("preview")) {
    var t0 = performance.now();
    var loop = function (now) {
      window.seekTo(((now - t0) / 1000) % M.duration);
      requestAnimationFrame(loop);
    };
    requestAnimationFrame(loop);
  }
})();
