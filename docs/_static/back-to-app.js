(function(){
  function insertInBreadcrumbs(){
    // RTD theme breadcrumbs list
    var crumbs = document.querySelector('ul.wy-breadcrumbs');
    if (!crumbs) return;

    // Avoid duplicates
    if (document.getElementById('back-to-app-link')) return;

    var link = document.createElement('a');
    link.id = 'back-to-app-link';
    link.className = 'back-to-app-link';
    link.href = '/';
    link.title = 'Back to ZaroPGx';
    link.textContent = 'Back to ZaroPGx';

    var li = document.createElement('li');
    li.className = 'back-to-app-item';
    li.appendChild(link);

    // Insert before the aside (which holds "View page source")
    var aside = crumbs.querySelector('li.wy-breadcrumbs-aside');
    if (aside && aside.parentNode === crumbs) {
      crumbs.insertBefore(li, aside);
    } else {
      crumbs.appendChild(li);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', insertInBreadcrumbs);
  } else {
    insertInBreadcrumbs();
  }
})();
