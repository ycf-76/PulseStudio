// assets/charts.js — 竞品能力雷达图
(function () {
  var style = getComputedStyle(document.documentElement);
  var accent = style.getPropertyValue('--accent').trim();
  var accent2 = style.getPropertyValue('--accent2').trim();
  var ink = style.getPropertyValue('--ink').trim();
  var muted = style.getPropertyValue('--muted').trim();
  var rule = style.getPropertyValue('--rule').trim();

  var el = document.getElementById('chart-radar');
  if (!el) return;
  var chart = echarts.init(el, null, { renderer: 'svg' });
  chart.setOption({
    animation: false,
    tooltip: { appendToBody: true },
    legend: {
      bottom: 0,
      textStyle: { color: ink, fontSize: 13 },
      itemGap: 24
    },
    radar: {
      indicator: [
        { name: '选题前瞻性\n（站外多源）', max: 5 },
        { name: '站内数据深度', max: 5 },
        { name: 'AI 视频/口播\n自动化', max: 5 },
        { name: '数字人克隆', max: 5 },
        { name: '多平台直发', max: 5 },
        { name: '内容-画面\n语义联动', max: 5 },
        { name: '全链闭环', max: 5 }
      ],
      radius: '62%',
      center: ['50%', '48%'],
      axisName: { color: ink, fontSize: 12, lineHeight: 16 },
      splitLine: { lineStyle: { color: rule } },
      splitArea: { show: false },
      axisLine: { lineStyle: { color: rule } }
    },
    series: [{
      type: 'radar',
      data: [
        {
          name: '我们（多智能体平台）',
          value: [5, 2, 4, 1, 1, 5, 4],
          lineStyle: { color: accent, width: 3 },
          itemStyle: { color: accent },
          areaStyle: { color: accent, opacity: 0.12 }
        },
        {
          name: '数据选题类（千瓜/新榜/灰豚）',
          value: [2, 5, 1, 1, 1, 2, 1],
          lineStyle: { color: accent2, width: 2 },
          itemStyle: { color: accent2 },
          areaStyle: { color: accent2, opacity: 0.05 }
        },
        {
          name: 'AI 视频类（剪映/智影/闪剪/HeyGen）',
          value: [1, 1, 5, 5, 2, 3, 2],
          lineStyle: { color: muted, width: 2, type: 'dashed' },
          itemStyle: { color: muted },
          areaStyle: { color: muted, opacity: 0.04 }
        },
        {
          name: '矩阵分发类（易媒/蚁小二/简媒）',
          value: [1, 2, 3, 3, 5, 1, 2],
          lineStyle: { color: accent2, width: 2, type: 'dotted' },
          itemStyle: { color: accent2 },
          areaStyle: { color: accent2, opacity: 0.04 }
        }
      ]
    }]
  });
  window.addEventListener('resize', function () { chart.resize(); });
})();
