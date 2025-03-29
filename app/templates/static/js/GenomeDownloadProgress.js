// Genome Download Progress component
class GenomeDownloadProgress {
  constructor(elementId) {
    this.element = document.getElementById(elementId);
    if (!this.element) {
      console.error(`Element with ID "${elementId}" not found`);
      return;
    }
    
    this.isMinimized = false;
    this.status = null;
    this.interval = null;
    
    this.init();
  }
  
  init() {
    // Create the UI
    this.element.innerHTML = `
      <div class="genome-download-panel">
        <div class="download-header">
          <h4>Reference Genome Downloads</h4>
          <button class="minimize-btn">Minimize</button>
        </div>
        <div class="download-content">
          <div class="overall-progress">
            <span class="progress-label">Overall Progress: 0%</span>
            <div class="progress-bar-container">
              <div class="progress-bar" style="width: 0%"></div>
            </div>
          </div>
          <div class="genome-list"></div>
        </div>
      </div>
    `;
    
    // Add styles
    const style = document.createElement('style');
    style.textContent = `
      .genome-download-panel {
        position: fixed;
        bottom: 20px;
        right: 20px;
        width: 350px;
        background-color: white;
        border-radius: 8px;
        box-shadow: 0 2px 10px rgba(0, 0, 0, 0.1);
        z-index: 1000;
        overflow: hidden;
        transition: all 0.3s ease;
      }
      
      .genome-download-panel.minimized .download-content {
        display: none;
      }
      
      .download-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 10px 15px;
        background-color: #f5f5f5;
        border-bottom: 1px solid #ddd;
      }
      
      .download-header h4 {
        margin: 0;
        font-size: 16px;
        color: #333;
      }
      
      .download-header button {
        border: none;
        background: none;
        color: #0066cc;
        cursor: pointer;
        font-size: 14px;
      }
      
      .download-content {
        padding: 15px;
      }
      
      .overall-progress {
        margin-bottom: 15px;
      }
      
      .progress-bar-container {
        height: 8px;
        background-color: #eee;
        border-radius: 4px;
        margin-top: 5px;
        overflow: hidden;
      }
      
      .progress-bar {
        height: 100%;
        background-color: #4caf50;
        transition: width 0.3s ease;
      }
      
      .genome-list {
        max-height: 200px;
        overflow-y: auto;
      }
      
      .genome-item {
        margin-bottom: 10px;
      }
      
      .genome-info {
        display: flex;
        justify-content: space-between;
        margin-bottom: 5px;
      }
      
      .genome-name {
        font-weight: bold;
      }
      
      .genome-status {
        color: #666;
        text-transform: capitalize;
      }
      
      .genome-size {
        color: #999;
      }
      
      .progress-text {
        text-align: right;
        font-size: 12px;
        color: #666;
        margin-top: 2px;
      }
    `;
    document.head.appendChild(style);
    
    // Add event listeners
    const minimizeBtn = this.element.querySelector('.minimize-btn');
    minimizeBtn.addEventListener('click', () => this.toggleMinimize());
    
    // Start checking status
    this.checkStatus();
    this.interval = setInterval(() => this.checkStatus(), 3000);
  }
  
  toggleMinimize() {
    this.isMinimized = !this.isMinimized;
    const panel = this.element.querySelector('.genome-download-panel');
    const btn = this.element.querySelector('.minimize-btn');
    
    if (this.isMinimized) {
      panel.classList.add('minimized');
      btn.textContent = 'Expand';
    } else {
      panel.classList.remove('minimized');
      btn.textContent = 'Minimize';
    }
  }
  
  async checkStatus() {
    try {
      const response = await fetch('/api/genome-download-status');
      const data = await response.json();
      
      // If we got data and it's different from current status, update UI
      if (data && JSON.stringify(data) !== JSON.stringify(this.status)) {
        this.status = data;
        this.updateUI();
      }
      
      // If download is complete, stop checking
      if (data && data.completed && !data.in_progress) {
        clearInterval(this.interval);
        setTimeout(() => {
          this.element.style.display = 'none';
        }, 5000);
      }
    } catch (error) {
      console.error('Error fetching genome download status:', error);
    }
  }
  
  updateUI() {
    if (!this.status) return;
    
    // Update overall progress
    const overallLabel = this.element.querySelector('.progress-label');
    const overallBar = this.element.querySelector('.progress-bar');
    
    overallLabel.textContent = `Overall Progress: ${Math.round(this.status.overall_progress)}%`;
    overallBar.style.width = `${this.status.overall_progress}%`;
    
    // Update genome list
    const genomeList = this.element.querySelector('.genome-list');
    genomeList.innerHTML = '';
    
    Object.entries(this.status.genomes).forEach(([name, info]) => {
      const genomeItem = document.createElement('div');
      genomeItem.className = 'genome-item';
      genomeItem.innerHTML = `
        <div class="genome-info">
          <span class="genome-name">${name}</span>
          <span class="genome-status">${info.status}</span>
          <span class="genome-size">${info.size_mb} MB</span>
        </div>
        <div class="progress-bar-container">
          <div class="progress-bar" style="width: ${info.progress}%"></div>
        </div>
        <div class="progress-text">${Math.round(info.progress)}%</div>
      `;
      genomeList.appendChild(genomeItem);
    });
    
    // Show or hide based on status
    if (!this.status.in_progress && this.status.completed) {
      setTimeout(() => {
        this.element.style.display = 'none';
      }, 5000);
    } else {
      this.element.style.display = 'block';
    }
  }
  
  startDownload() {
    fetch('/api/start-genome-download', {
      method: 'POST'
    })
    .then(response => response.json())
    .then(data => {
      console.log('Download started:', data);
      this.checkStatus();
    })
    .catch(error => {
      console.error('Error starting download:', error);
    });
  }
}

// Initialize when DOM is loaded
document.addEventListener('DOMContentLoaded', function() {
  // Create a container for the download panel if it doesn't exist
  if (!document.getElementById('genome-download-container')) {
    const container = document.createElement('div');
    container.id = 'genome-download-container';
    document.body.appendChild(container);
  }
  
  // Initialize the download progress component
  window.genomeDownloader = new GenomeDownloadProgress('genome-download-container');
}); 