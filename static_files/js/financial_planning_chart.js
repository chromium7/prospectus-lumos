(function () {
  'use strict';

  var dataElement = document.getElementById('freedom-timeline-data');
  var canvas = document.getElementById('freedomTimelineChart');
  if (!dataElement || !canvas || typeof Chart === 'undefined') {
    return;
  }

  var chartData;
  try {
    chartData = JSON.parse(dataElement.textContent);
  } catch (error) {
    return;
  }

  var points = chartData.points || [];
  if (points.length === 0) {
    return;
  }

  var labels = points.map(function (point) {
    return point.date;
  });

  // Money is formatted on the server; the browser only positions the points.
  var balanceText = points.map(function (point) {
    return point.balance_text;
  });
  var targetText = points.map(function (point) {
    return point.target_text;
  });
  var eventText = points.map(function (point) {
    return point.event_text;
  });

  new Chart(canvas, {
    type: 'line',
    data: {
      labels: labels,
      datasets: [
        {
          label: 'Estimated investments',
          data: points.map(function (point) {
            return point.balance;
          }),
          borderColor: '#4f46e5',
          backgroundColor: 'rgba(79,70,229,0.12)',
          borderWidth: 2,
          fill: true,
          tension: 0.2,
          // Event months are drawn as larger triangles so they are distinguishable without colour.
          pointRadius: points.map(function (point) {
            return point.event ? 6 : 0;
          }),
          pointStyle: points.map(function (point) {
            return point.event ? 'triangle' : 'circle';
          }),
          pointBackgroundColor: '#4f46e5'
        },
        {
          label: 'Target at that time',
          data: points.map(function (point) {
            return point.target;
          }),
          borderColor: '#64748b',
          borderDash: [6, 4],
          borderWidth: 2,
          fill: false,
          tension: 0.2,
          pointRadius: 0
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      scales: {
        x: { ticks: { maxTicksLimit: 12, autoSkip: true } },
        y: { ticks: { display: false }, grid: { drawTicks: false } }
      },
      plugins: {
        legend: { position: 'bottom' },
        tooltip: {
          callbacks: {
            label: function (context) {
              var index = context.dataIndex;
              if (context.datasetIndex === 0) {
                var line = 'Estimated investments: ' + balanceText[index];
                if (eventText[index]) {
                  line += ' (plan paid: ' + eventText[index] + ')';
                }
                return line;
              }
              return 'Target at that time: ' + targetText[index];
            }
          }
        }
      }
    }
  });
})();
