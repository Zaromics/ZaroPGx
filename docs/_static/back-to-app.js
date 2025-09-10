(function(){
  function createBtn(){
    var btn = document.createElement('button');
    btn.id = 'back-to-app';
    btn.type = 'button';
    btn.title = 'Back to ZaroPGx';
    btn.textContent = 'Back to ZaroPGx';
    btn.addEventListener('click', function(){
      window.location.href = '/';
    });
    document.body.appendChild(btn);
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', createBtn);
  } else {
    createBtn();
  }
})();
