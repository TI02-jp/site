(function() {
  function notifyParent() {
    try {
      if (window.parent && window.parent !== window) {
        window.parent.postMessage('session-activity', '*');
      }
    } catch (e) {
      // Ignore errors from cross-origin access
    }
  }
  ['mousemove','keydown','click','scroll','touchstart'].forEach(evt => {
    window.addEventListener(evt, notifyParent, {passive: true});
  });
})();
