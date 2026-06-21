(function() {
  var overlay = document.getElementById('loading-overlay');
  var loadingText = document.getElementById('loading-text');
  if (!overlay) return;

  function showLoading(text) {
    if (loadingText) {
      loadingText.textContent = text || 'Loading...';
    }
    overlay.setAttribute('aria-hidden', 'false');
    overlay.classList.add('is-active');
  }

  document.addEventListener('submit', function(e) {
    var form = e.target;
    if (!form.dataset.loading) return;
    showLoading(form.dataset.loadingText || null);
  });

  document.querySelectorAll('a[data-loading]').forEach(function(link) {
    link.addEventListener('click', function(e) {
      showLoading(link.dataset.loadingText || null);
    });
  });
})();
