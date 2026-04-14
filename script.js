/* Dude, Where's My Package? — Landing Page Scripts */

document.addEventListener('DOMContentLoaded', () => {

  /* ── Tab Switchers ─────────────────────────── */

  function initTabs(tabSelector, panelSelector, frameSelector) {
    const tabs = document.querySelectorAll(tabSelector);
    const panels = document.querySelectorAll(panelSelector);
    const frame = frameSelector ? document.querySelector(frameSelector) : null;

    tabs.forEach(tab => {
      tab.addEventListener('click', () => {
        const target = tab.dataset.target;

        tabs.forEach(t => t.classList.remove('active'));
        tab.classList.add('active');

        panels.forEach(p => {
          p.classList.remove('active');
          if (p.id === target) p.classList.add('active');
        });

        // Update frame class for extension sizing
        if (frame) {
          if (target === 'mockup-extension') {
            frame.classList.add('extension-frame');
          } else {
            frame.classList.remove('extension-frame');
          }
        }

        // Update mockup URL bar
        const urlBar = document.querySelector('.mockup-url');
        if (urlBar && tab.dataset.url) {
          urlBar.textContent = tab.dataset.url;
        }
      });
    });
  }

  initTabs('.mockup-tab', '.mockup-viewport', '.mockup-frame');
  initTabs('.install-tab', '.install-panel', null);

  /* ── Copy to Clipboard ─────────────────────── */

  document.querySelectorAll('.copy-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const code = btn.closest('.code-block').querySelector('pre').textContent;
      navigator.clipboard.writeText(code).then(() => {
        btn.textContent = 'Copied!';
        btn.classList.add('copied');
        setTimeout(() => {
          btn.textContent = 'Copy';
          btn.classList.remove('copied');
        }, 2000);
      });
    });
  });

  /* ── Scroll Fade-In Animations ─────────────── */

  const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        entry.target.classList.add('visible');
        observer.unobserve(entry.target);
      }
    });
  }, { threshold: 0.1 });

  document.querySelectorAll('.fade-in').forEach(el => observer.observe(el));

  /* ── Smooth Scroll for Anchor Links ────────── */

  document.querySelectorAll('a[href^="#"]').forEach(link => {
    link.addEventListener('click', (e) => {
      const target = document.querySelector(link.getAttribute('href'));
      if (target) {
        e.preventDefault();
        target.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    });
  });
});
