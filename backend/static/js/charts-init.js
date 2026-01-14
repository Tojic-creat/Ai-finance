/**
 * backend/templates/static/js/charts-init.js
 *
 * Инициализация Chart.js на дашборде FinAssist.
 * - Ищет canvas-элементы с классом `fa-chart` и data-attributes:
 *     data-type="line|doughnut|bar"
 *     data-chart-data='[...]'     (JSON: массив точек / меток)
 *     data-chart-labels='[...]'   (JSON: массив меток)
 *     data-currency="USD"         (опционально — для форматирования)
 *     data-options='{"...":...}'  (опционально — дополнительные параметры)
 * - Цвета берутся из CSS-переменных для лёгкой кастомизации.
 * - Экспортирует глобальный объект `FinAssistCharts` с методами init(), update(), destroy().
 *
 * Требует Chart.js (v3/4). Если Chart не найден — graceful fail (консольное предупреждение).
 *
 * Пример использования в шаблонах:
 *  <canvas id="balanceChart" class="fa-chart" data-type="line"
 *          data-chart-labels='["2025-01-01","2025-01-02"]'
 *          data-chart-data='[1200.5, 1180.0]'
 *          data-currency="EUR"></canvas>
 *
 * Автор: сгенерировано помощником — адаптируйте под ваши данные.
 */

(function () {
  "use strict";

  if (typeof Chart === "undefined") {
    console.warn("Chart.js not found — charts-init.js skipped.");
    window.FinAssistCharts = {
      init: function () {
        console.warn("Chart.js missing");
      },
      update: function () {
        console.warn("Chart.js missing");
      },
      destroy: function () {
        console.warn("Chart.js missing");
      },
    };
    return;
  }

  // ---------- Утилиты ----------
  function parseJSONAttr(el, name, fallback) {
    var v = el.getAttribute(name);
    if (!v) return fallback;
    try {
      return JSON.parse(v);
    } catch (e) {
      console.warn("Invalid JSON in", name, "for", el, e);
      return fallback;
    }
  }

  function cssVar(name, fallback) {
    var val = getComputedStyle(document.documentElement).getPropertyValue(name);
    if (!val) return fallback;
    return val.trim();
  }

  function currencyFormatter(currency) {
    var formatter;
    try {
      formatter = new Intl.NumberFormat(undefined, {
        style: "currency",
        currency: currency || "USD",
        maximumFractionDigits: 2,
      });
    } catch (e) {
      // fallback simple
      formatter = {
        format: function (n) {
          return (currency ? currency + " " : "") + Number(n).toFixed(2);
        },
      };
    }
    return function (n) {
      return formatter.format(n);
    };
  }

  function safeNumber(n) {
    var x = Number(n);
    return isFinite(x) ? x : 0;
  }

  // ---------- Chart factories ----------
  function createLineChart(ctx, labels, data, opts) {
    var root = document.documentElement;
    var primary = cssVar("--color-primary", "#0ea5a4"); // tailwind-teal-500-ish fallback
    var muted = cssVar("--color-muted", "#94a3b8");
    var borderColor = primary;
    var bg = primary + "33"; // with alpha (if var is hex, not guaranteed)
    // Chart.js dataset
    var ds = {
      label: opts.label || "Balance",
      data: data.map(safeNumber),
      fill: true,
      tension: opts.tension != null ? opts.tension : 0.3,
      borderWidth: 2,
      borderColor: borderColor,
      pointRadius: 3,
      pointHoverRadius: 6,
      backgroundColor: opts.backgroundColor || bg,
    };

    var config = {
      type: "line",
      data: {
        labels: labels,
        datasets: [ds],
      },
      options: Object.assign(
        {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { display: !!opts.showLegend },
            tooltip: {
              mode: "index",
              intersect: false,
              callbacks: {
                label: function (context) {
                  var fmt = currencyFormatter(opts.currency);
                  return context.dataset.label + ": " + fmt(context.parsed.y);
                },
              },
            },
          },
          interaction: { mode: "nearest", intersect: false },
          scales: {
            x: {
              display: true,
              grid: { display: false, drawBorder: false },
              ticks: { maxRotation: 0, autoSkip: true },
            },
            y: {
              display: true,
              grid: { color: cssVar("--grid-color", "rgba(2,6,23,0.04)") },
              ticks: {
                callback: function (val) {
                  var fmt = currencyFormatter(opts.currency);
                  return fmt(val);
                },
              },
            },
          },
        },
        opts.extraOptions || {}
      ),
    };

    return new Chart(ctx, config);
  }

  function createDoughnutChart(ctx, labels, data, opts) {
    var palette = opts.palette || [
      cssVar("--color-primary", "#0ea5a4"),
      cssVar("--color-accent", "#8b5cf6"),
      cssVar("--color-warn", "#f97316"),
      cssVar("--color-muted", "#94a3b8"),
    ];

    var bg = data.map(function (_, i) {
      return palette[i % palette.length];
    });
    var border = bg.map(function () {
      return cssVar("--chart-border", "#ffffff");
    });

    var config = {
      type: "doughnut",
      data: {
        labels: labels,
        datasets: [
          {
            data: data.map(safeNumber),
            backgroundColor: bg,
            borderColor: border,
            borderWidth: 1,
          },
        ],
      },
      options: Object.assign(
        {
          responsive: true,
          maintainAspectRatio: false,
          cutout: opts.cutout || "55%",
          plugins: {
            legend: { position: opts.legendPosition || "right" },
            tooltip: {
              callbacks: {
                label: function (ctx) {
                  var fmt = currencyFormatter(opts.currency);
                  var v = ctx.parsed;
                  return ctx.label + ": " + fmt(v);
                },
              },
            },
          },
        },
        opts.extraOptions || {}
      ),
    };

    return new Chart(ctx, config);
  }

  function createBarChart(ctx, labels, datasets, opts) {
    // datasets: [{label, data, stack (optional)} ...]
    var config = {
      type: "bar",
      data: {
        labels: labels,
        datasets: datasets.map(function (ds, idx) {
          return Object.assign(
            {
              backgroundColor:
                ds.backgroundColor || cssVar("--color-primary", "#0ea5a4"),
              borderColor: ds.borderColor || "transparent",
              borderWidth: 1,
            },
            ds
          );
        }),
      },
      options: Object.assign(
        {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { position: opts.legendPosition || "top" },
            tooltip: {
              callbacks: {
                label: function (ctx) {
                  var fmt = currencyFormatter(opts.currency);
                  return ctx.dataset.label + ": " + fmt(ctx.parsed.y);
                },
              },
            },
          },
          scales: {
            x: { stacked: opts.stacked || false },
            y: {
              stacked: opts.stacked || false,
              ticks: {
                callback: function (val) {
                  return currencyFormatter(opts.currency)(val);
                },
              },
            },
          },
        },
        opts.extraOptions || {}
      ),
    };

    return new Chart(ctx, config);
  }

  // ---------- Core manager ----------
  var instances = new Map();

  function initChartForCanvas(canvas) {
    if (!canvas || !canvas.getContext) return null;
    var type = canvas.dataset.type || canvas.getAttribute("data-type");
    var labels = parseJSONAttr(canvas, "data-chart-labels", []);
    var rawData = parseJSONAttr(canvas, "data-chart-data", null);
    var opts = parseJSONAttr(canvas, "data-options", {}) || {};
    var currency =
      canvas.getAttribute("data-currency") || opts.currency || null;

    var ctx = canvas.getContext("2d");
    var chartObj = null;

    try {
      if (type === "line") {
        var data = rawData || [];
        chartObj = createLineChart(
          ctx,
          labels,
          data,
          Object.assign({ currency: currency }, opts)
        );
      } else if (type === "doughnut") {
        var data = rawData || [];
        chartObj = createDoughnutChart(
          ctx,
          labels,
          data,
          Object.assign({ currency: currency }, opts)
        );
      } else if (type === "bar") {
        // rawData can be array or object describing datasets
        var datasets = [];
        if (
          Array.isArray(rawData) &&
          rawData.length &&
          typeof rawData[0] !== "number"
        ) {
          // assume array of dataset objects
          datasets = rawData;
        } else {
          // single dataset => create default
          datasets = [
            {
              label: opts.label || "Series",
              data: rawData || [],
            },
          ];
        }
        chartObj = createBarChart(
          ctx,
          labels,
          datasets,
          Object.assign({ currency: currency }, opts)
        );
      } else {
        console.warn("Unsupported chart type:", type);
      }
    } catch (e) {
      console.error("Error creating chart for", canvas, e);
    }

    if (chartObj) {
      instances.set(canvas, chartObj);
    }
    return chartObj;
  }

  function init(selector) {
    // selector optional: CSS selector or root element. Default: document
    var root = document;
    if (selector instanceof HTMLElement) root = selector;
    else if (typeof selector === "string")
      root = document.querySelector(selector) || document;
    // find canvases
    var canvases = Array.from(
      root.querySelectorAll("canvas.fa-chart, canvas[data-type]")
    );
    canvases.forEach(function (c) {
      // destroy if exists
      destroy(c);
      initChartForCanvas(c);
    });
  }

  function update(canvas, newData, newLabels) {
    if (!canvas) return;
    var chart = instances.get(canvas);
    if (!chart) {
      // try to init
      chart = initChartForCanvas(canvas);
      if (!chart) return;
    }
    if (newLabels) chart.data.labels = newLabels;
    if (newData) {
      // If single dataset
      if (chart.data.datasets.length === 1 && !Array.isArray(newData[0])) {
        chart.data.datasets[0].data = newData;
      } else {
        // map newData onto datasets based on length
        if (
          Array.isArray(newData) &&
          newData.length === chart.data.datasets.length
        ) {
          chart.data.datasets.forEach(function (ds, idx) {
            ds.data = newData[idx];
          });
        } else {
          // fallback: try to set first dataset
          chart.data.datasets[0].data = Array.isArray(newData)
            ? newData
            : chart.data.datasets[0].data;
        }
      }
    }
    chart.update();
  }

  function destroy(canvasOrChart) {
    if (!canvasOrChart) return;
    if (canvasOrChart instanceof Chart) {
      canvasOrChart.destroy();
      // remove from map values
      for (var [k, v] of instances.entries()) {
        if (v === canvasOrChart) instances.delete(k);
      }
      return;
    }
    // assume canvas
    var chart = instances.get(canvasOrChart);
    if (chart) {
      chart.destroy();
      instances.delete(canvasOrChart);
    }
  }

  // Auto-init on DOM ready for elements present
  document.addEventListener("DOMContentLoaded", function () {
    // small delay to allow templates to attach data if needed
    setTimeout(function () {
      init(document);
    }, 30);
  });

  // expose API
  window.FinAssistCharts = {
    init: init,
    update: update,
    destroy: destroy,
    _instances: instances, // for debug
  };
})();
