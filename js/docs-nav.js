/**
 * Documentation Navigation Script
 * Handles sidebar highlighting and smooth scrolling
 */

// Highlight active nav link based on scroll position
function initDocsNav() {
  const sections = document.querySelectorAll('h2[id], h1[id]');
  const navLinks = document.querySelectorAll('.docs-nav-link');

  if (sections.length === 0 || navLinks.length === 0) return;

  function highlightNavLink() {
    let current = '';
    const scrollPosition = window.scrollY + 120;

    sections.forEach(section => {
      const sectionTop = section.offsetTop;
      if (scrollPosition >= sectionTop) {
        current = section.getAttribute('id');
      }
    });

    navLinks.forEach(link => {
      link.classList.remove('active');
      const href = link.getAttribute('href');
      if (href === '#' + current) {
        link.classList.add('active');
      }
    });
  }

  // Smooth scroll to section
  navLinks.forEach(link => {
    link.addEventListener('click', (e) => {
      const href = link.getAttribute('href');
      if (href && href.startsWith('#')) {
        e.preventDefault();
        const target = document.querySelector(href);
        if (target) {
          const headerOffset = 80;
          const elementPosition = target.getBoundingClientRect().top;
          const offsetPosition = elementPosition + window.pageYOffset - headerOffset;

          window.scrollTo({
            top: offsetPosition,
            behavior: 'smooth'
          });
        }
      }
    });
  });

  // Listen for scroll
  window.addEventListener('scroll', highlightNavLink);

  // Initial highlight
  highlightNavLink();
}

// Mobile sidebar toggle
function initMobileSidebar() {
  const sidebar = document.querySelector('.docs-sidebar');
  const toggleBtn = document.querySelector('.sidebar-toggle');

  if (!sidebar || !toggleBtn) return;

  toggleBtn.addEventListener('click', () => {
    sidebar.classList.toggle('open');
  });

  // Close sidebar when clicking a link on mobile
  const navLinks = document.querySelectorAll('.docs-nav-link');
  navLinks.forEach(link => {
    link.addEventListener('click', () => {
      if (window.innerWidth <= 768) {
        sidebar.classList.remove('open');
      }
    });
  });
}

// Initialize on DOM ready
document.addEventListener('DOMContentLoaded', () => {
  initDocsNav();
  initMobileSidebar();
});
