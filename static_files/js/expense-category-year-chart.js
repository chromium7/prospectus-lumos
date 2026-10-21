(function () {
  'use strict';

  var dataElement = document.getElementById('expense-category-year-data');
  var canvas = document.getElementById('expenseCategoryYearChart');
  if (!dataElement || !canvas || typeof Chart === 'undefined') {
    return;
  }

  var chartData;
  try {
    chartData = JSON.parse(dataElement.textContent);
  } catch (error) {
    return;
  }

  var years = chartData.years || [];
  var sourceDatasets = chartData.datasets || [];
  if (years.length === 0 || sourceDatasets.length === 0) {
    return;
  }

  var colors = [
    '#ef4444', '#3b82f6', '#f59e0b', '#10b981', '#8b5cf6', '#ec4899',
    '#06b6d4', '#84cc16', '#f97316', '#6366f1', '#14b8a6', '#a855f7'
  ];
  var currency = new Intl.NumberFormat('id-ID', {
    style: 'currency',
    currency: 'IDR',
    maximumFractionDigits: 0
  });
  var compactCurrency = new Intl.NumberFormat('id-ID', {
    style: 'currency',
    currency: 'IDR',
    notation: 'compact',
    maximumFractionDigits: 1
  });

  var datasets = sourceDatasets.map(function (dataset, index) {
    return {
      label: dataset.label,
      data: (dataset.data || []).map(Number),
      backgroundColor: colors[index % colors.length],
      borderWidth: 1
    };
  });

  var chart = new Chart(canvas, {
    type: 'bar',
    data: { labels: years, datasets: datasets },
    options: {
      responsive: true,
      maintainAspectRatio: true,
      aspectRatio: 1.8,
      interaction: { mode: 'index', intersect: false },
      scales: {
        x: { stacked: true },
        y: {
          stacked: true,
          beginAtZero: true,
          ticks: {
            callback: function (value) {
              return compactCurrency.format(value);
            }
          }
        }
      },
      plugins: {
        legend: { position: 'bottom' },
        tooltip: {
          callbacks: {
            label: function (context) {
              return context.dataset.label + ': ' + currency.format(context.parsed.y);
            }
          }
        }
      }
    }
  });

  var menu = document.getElementById('annual-category-filter-menu');
  var filterLabel = document.getElementById('annual-category-filter-label');
  var checkboxes = document.querySelectorAll('.annual-category-checkbox');
  var selectAll = document.getElementById('annual-category-select-all');
  var selectNone = document.getElementById('annual-category-select-none');

  if (menu) {
    menu.addEventListener('click', function (event) {
      event.stopPropagation();
    });
  }

  function updateChart() {
    var visibleCount = 0;
    checkboxes.forEach(function (checkbox) {
      var datasetIndex = Number(checkbox.dataset.categoryIndex);
      chart.setDatasetVisibility(datasetIndex, checkbox.checked);
      if (checkbox.checked) {
        visibleCount += 1;
      }
    });

    if (filterLabel) {
      filterLabel.textContent = visibleCount === checkboxes.length ? 'All shown' : visibleCount + ' shown';
    }
    chart.update();
  }

  checkboxes.forEach(function (checkbox) {
    checkbox.addEventListener('change', updateChart);
  });

  function setAllCategories(checked) {
    checkboxes.forEach(function (checkbox) {
      checkbox.checked = checked;
    });
    updateChart();
  }

  if (selectAll) {
    selectAll.addEventListener('click', function () {
      setAllCategories(true);
    });
  }
  if (selectNone) {
    selectNone.addEventListener('click', function () {
      setAllCategories(false);
    });
  }
})();
