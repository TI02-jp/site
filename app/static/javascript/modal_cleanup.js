(function () {
  function cleanupModalArtifacts() {
    const openModals = document.querySelectorAll('.modal.show');
    if (openModals.length === 0) {
      document.body.classList.remove('modal-open');
      document.body.style.removeProperty('padding-right');
      document.querySelectorAll('.modal-backdrop').forEach((backdrop) => {
        if (backdrop && backdrop.parentNode) {
          backdrop.parentNode.removeChild(backdrop);
        }
      });
    }
  }

  const scheduleCleanup =
    typeof queueMicrotask === 'function'
      ? queueMicrotask
      : (callback) => setTimeout(callback, 0);

  function handleModalHide(event) {
    // Allow Bootstrap to finish its hide transition before cleaning up
    const target = event.target;
    if (target && target.classList.contains('show')) {
      let timeoutId;
      function onTransitionEnd() {
        if (timeoutId) {
          clearTimeout(timeoutId);
        }
        target.removeEventListener('transitionend', onTransitionEnd);
        cleanupModalArtifacts();
      }

      target.addEventListener('transitionend', onTransitionEnd, { once: true });

      timeoutId = setTimeout(() => {
        target.removeEventListener('transitionend', onTransitionEnd);
        cleanupModalArtifacts();
      }, 500);
    } else {
      // Fallback for browsers without transitions or if modal is already hidden
      scheduleCleanup(cleanupModalArtifacts);
    }
  }

  document.addEventListener('hidden.bs.modal', cleanupModalArtifacts);
  document.addEventListener('hide.bs.modal', handleModalHide);

  ['pagehide', 'unload', 'beforeunload', 'visibilitychange'].forEach((evt) => {
    window.addEventListener(evt, cleanupModalArtifacts, { passive: true });
  });

  document.addEventListener('DOMContentLoaded', cleanupModalArtifacts);
})();
