// assets/charts.js — 图 3：技能目录体积演进
(function () {
  var style = getComputedStyle(document.documentElement);
  var accent = style.getPropertyValue('--accent').trim();
  var accent2 = style.getPropertyValue('--accent2').trim();
  var ink = style.getPropertyValue('--ink').trim();
  var muted = style.getPropertyValue('--muted').trim();
  var rule = style.getPropertyValue('--rule').trim();

  var el = document.getElementById('chart-volume');
  if (!el || typeof echarts === 'undefined') return;

  var chart = echarts.init(el, null, { renderer: 'svg' });

  var stages = ['优化前（全量注入）', 'P0 · 去重修复', 'P1 · 路由裁池', 'P2 · 二层披露'];
  var values = [43590, 21726, 8153, 5435];
  var colors = [muted, accent2, accent2, accent];

  chart.setOption({
    animation: false,
    grid: { top: 46, left: 70, right: 40, bottom: 60 },
    tooltip: {
      trigger: 'axis',
      appendToBody: true,
      axisPointer: { type: 'shadow' },
      formatter: function (params) {
        var p = params[0];
        var drop = p.dataIndex === 0 ? '' :
          '（较上一阶段 -' + Math.round((1 - values[p.dataIndex] / values[p.dataIndex - 1]) * 100) + '%）';
        return p.name + '<br/>' + p.marker + ' 目录体积：<b>' +
          Number(p.value).toLocaleString() + '</b> 字符 ' + drop;
      }
    },
    xAxis: {
      type: 'category',
      data: stages,
      axisLine: { lineStyle: { color: rule } },
      axisTick: { show: false },
      axisLabel: { color: ink, fontSize: 12.5, interval: 0 }
    },
    yAxis: {
      type: 'value',
      name: '字符数',
      nameTextStyle: { color: muted, fontSize: 12 },
      splitLine: { lineStyle: { color: rule } },
      axisLabel: { color: muted, formatter: function (v) { return v / 1000 + 'k'; } }
    },
    series: [{
      type: 'bar',
      data: values.map(function (v, i) {
        return { value: v, itemStyle: { color: colors[i], borderRadius: [6, 6, 0, 0] } };
      }),
      barWidth: '42%',
      label: {
        show: true,
        position: 'top',
        color: ink,
        fontWeight: 700,
        fontSize: 13,
        formatter: function (p) { return Number(p.value).toLocaleString(); }
      },
      markLine: {
        silent: true,
        symbol: 'none',
        lineStyle: { color: accent, type: 'dashed' },
        label: { color: accent, formatter: 'P1 路由上限：业界 40 工具精度线' },
        data: [{ yAxis: 8153 }]
      }
    }]
  });

  window.addEventListener('resize', function () { chart.resize(); });
})();
