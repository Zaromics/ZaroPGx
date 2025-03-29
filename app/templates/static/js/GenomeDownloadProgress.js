/**
 * Genome Download Progress Component
 * Displays the status of reference genome downloads
 */
class GenomeDownloadProgress {
    constructor() {
        this.container = document.getElementById('genome-download-container');
        if (!this.container) {
            console.warn('Genome download container not found');
            return;
        }
        
        this.minimized = false;
        this.downloadInProgress = false;
        this.init();
        this.checkStatus();
        
        // Check status every 10 seconds
        setInterval(() => this.checkStatus(), 10000);
    }
    
    init() {
        this.container.innerHTML = `
            <div id="genome-download-panel" class="download-panel">
                <div class="download-header">
                    <h3>Reference Genome Downloads</h3>
                    <button id="minimize-download" class="minimize-btn">-</button>
                </div>
                <div id="download-content" class="download-content">
                    <div id="download-status">Checking status...</div>
                    <div id="download-progress" class="progress-container">
                        <div id="progress-bar" class="progress-bar" style="width: 0%"></div>
                    </div>
                    <div id="genome-list" class="genome-list"></div>
                    <button id="start-download" class="download-btn" style="display: none;">Start Download</button>
                </div>
            </div>
        `;
        
        // Add event listeners
        document.getElementById('minimize-download').addEventListener('click', () => this.toggleMinimize());
        document.getElementById('start-download').addEventListener('click', () => this.startDownload());
        
        // Add styles
        const style = document.createElement('style');
        style.textContent = `
            .download-panel {
                position: fixed;
                bottom: 20px;
                right: 20px;
                width: 300px;
                background: white;
                border: 1px solid #ccc;
                border-radius: 5px;
                box-shadow: 0 2px 10px rgba(0,0,0,0.1);
                z-index: 1000;
                transition: height 0.3s;
                overflow: hidden;
            }
            .download-header {
                display: flex;
                justify-content: space-between;
                align-items: center;
                padding: 10px;
                background: #f8f9fa;
                border-bottom: 1px solid #eee;
            }
            .download-header h3 {
                margin: 0;
                font-size: 16px;
            }
            .minimize-btn {
                background: none;
                border: none;
                font-size: 20px;
                cursor: pointer;
            }
            .download-content {
                padding: 15px;
            }
            .progress-container {
                height: 20px;
                background: #f0f0f0;
                border-radius: 10px;
                margin: 10px 0;
                overflow: hidden;
            }
            .progress-bar {
                height: 100%;
                background: #4CAF50;
                width: 0%;
                transition: width 0.5s;
            }
            .genome-list {
                margin-top: 10px;
                max-height: 150px;
                overflow-y: auto;
            }
            .genome-item {
                margin-bottom: 5px;
                padding: 5px;
                border-bottom: 1px solid #eee;
            }
            .download-btn {
                display: block;
                width: 100%;
                padding: 8px;
                margin-top: 10px;
                background: #007bff;
                color: white;
                border: none;
                border-radius: 4px;
                cursor: pointer;
            }
            .minimized .download-content {
                display: none;
            }
        `;
        document.head.appendChild(style);
    }
    
    toggleMinimize() {
        this.minimized = !this.minimized;
        const panel = document.getElementById('genome-download-panel');
        const btn = document.getElementById('minimize-download');
        
        if (this.minimized) {
            panel.classList.add('minimized');
            btn.textContent = '+';
        } else {
            panel.classList.remove('minimized');
            btn.textContent = '-';
        }
    }
    
    async checkStatus() {
        try {
            const response = await fetch('/api/genome-download-status');
            if (response.ok) {
                const data = await response.json();
                this.updateUI(data);
            } else {
                console.error('Failed to fetch download status');
            }
        } catch (error) {
            console.error('Error checking download status:', error);
        }
    }
    
    updateUI(data) {
        const statusEl = document.getElementById('download-status');
        const progressBar = document.getElementById('progress-bar');
        const genomeList = document.getElementById('genome-list');
        const startBtn = document.getElementById('start-download');
        
        // Update overall status
        if (data.downloading) {
            this.downloadInProgress = true;
            statusEl.textContent = `Downloading reference genomes: ${data.overall_progress}%`;
            progressBar.style.width = `${data.overall_progress}%`;
            startBtn.style.display = 'none';
            
            // Make the panel visible if minimized during active download
            if (this.minimized) {
                this.toggleMinimize();
            }
        } else if (data.complete) {
            statusEl.textContent = 'All reference genomes downloaded';
            progressBar.style.width = '100%';
            startBtn.style.display = 'none';
            
            // Auto-minimize after 5 seconds if complete
            if (this.downloadInProgress) {
                this.downloadInProgress = false;
                setTimeout(() => {
                    if (!this.minimized) this.toggleMinimize();
                }, 5000);
            }
        } else {
            statusEl.textContent = 'Reference genomes need to be downloaded';
            progressBar.style.width = '0%';
            startBtn.style.display = 'block';
        }
        
        // Update genome list
        if (data.genomes) {
            genomeList.innerHTML = '';
            Object.entries(data.genomes).forEach(([name, info]) => {
                const item = document.createElement('div');
                item.className = 'genome-item';
                item.innerHTML = `<b>${name}</b>: ${info.status} ${info.progress ? `(${info.progress}%)` : ''}`;
                genomeList.appendChild(item);
            });
        }
    }
    
    async startDownload() {
        try {
            const response = await fetch('/api/start-genome-download', {
                method: 'POST'
            });
            
            if (response.ok) {
                this.checkStatus(); // Immediately check for updated status
            } else {
                console.error('Failed to start download');
            }
        } catch (error) {
            console.error('Error starting download:', error);
        }
    }
}

// Initialize the component when the DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    new GenomeDownloadProgress();
}); 