/**
 * Upload Progress Component
 * Displays the progress of the user's file upload
 */
class GenomeDownloadProgress {
    constructor() {
        this.container = document.getElementById('genome-download-container');
        if (!this.container) {
            console.warn('Upload progress container not found');
            return;
        }
        
        this.uploadInProgress = false;
        this.uploadComplete = false;
        this.init();
    }
    
    init() {
        this.container.innerHTML = `
            <div id="upload-progress-panel" class="upload-panel">
                <div class="upload-header">
                    <h3>File Upload Progress</h3>
                    <button id="minimize-upload" class="minimize-btn">-</button>
                </div>
                <div id="upload-content" class="upload-content">
                    <div id="upload-status">Ready to upload</div>
                    <div id="upload-progress" class="progress-container">
                        <div id="upload-progress-bar" class="progress-bar" style="width: 0%">0%</div>
                    </div>
                </div>
            </div>
        `;
        
        // Add event listeners
        document.getElementById('minimize-upload').addEventListener('click', () => this.toggleMinimize());
        
        // Add styles
        const style = document.createElement('style');
        style.textContent = `
            .upload-panel {
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
            .upload-header {
                display: flex;
                justify-content: space-between;
                align-items: center;
                padding: 10px;
                background: #f8f9fa;
                border-bottom: 1px solid #eee;
            }
            .upload-header h3 {
                margin: 0;
                font-size: 16px;
            }
            .minimize-btn {
                background: none;
                border: none;
                font-size: 20px;
                cursor: pointer;
            }
            .upload-content {
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
                transition: width 0.3s;
            }
            .minimized .upload-content {
                display: none;
            }
        `;
        document.head.appendChild(style);
        
        // Override the form submission to use XHR for progress tracking
        this.overrideFormSubmission();
    }
    
    toggleMinimize() {
        const panel = document.getElementById('upload-progress-panel');
        const btn = document.getElementById('minimize-upload');
        
        if (panel.classList.contains('minimized')) {
            panel.classList.remove('minimized');
            btn.textContent = '-';
        } else {
            panel.classList.add('minimized');
            btn.textContent = '+';
        }
    }
    
    overrideFormSubmission() {
        const form = document.getElementById('uploadForm');
        const fileInput = document.getElementById('genomicFile');
        
        if (!form || !fileInput) {
            console.warn('Upload form or file input not found');
            return;
        }
        
        // Reset progress when file selection changes
        fileInput.addEventListener('change', () => {
            this.resetProgress();
            this.showStatus('File selected, ready to upload');
        });
        
        // Use capture phase to ensure our handler runs first
        form.addEventListener('submit', (e) => {
            console.log('Upload form submit intercepted by GenomeDownloadProgress');
            // Prevent the default form submission - MUST DO THIS FIRST
            e.preventDefault();
            e.stopPropagation();
            
            // Get form data
            const formData = new FormData(form);
            const uploadButton = document.getElementById('uploadButton');
            
            // Disable the upload button
            uploadButton.disabled = true;
            uploadButton.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Uploading...';
            
            // Start progress tracking
            this.startProgressTracking();
            
            // Create XHR request
            const xhr = new XMLHttpRequest();
            
            // Setup progress event
            xhr.upload.addEventListener('progress', (event) => {
                if (event.lengthComputable) {
                    const percentComplete = Math.round((event.loaded / event.total) * 100);
                    this.updateProgress(percentComplete);
                    
                    // Update status message based on percentage
                    if (percentComplete < 25) {
                        this.showStatus('Starting upload...');
                    } else if (percentComplete < 50) {
                        this.showStatus('Uploading file data...');
                    } else if (percentComplete < 75) {
                        this.showStatus('Processing data chunks...');
                    } else if (percentComplete < 100) {
                        this.showStatus('Finalizing upload...');
                    }
                }
            });
            
            // Setup load event (upload completed successfully)
            xhr.addEventListener('load', () => {
                if (xhr.status >= 200 && xhr.status < 300) {
                    // Upload successful
                    this.completeUpload();
                    
                    // Process the response
                    try {
                        const response = JSON.parse(xhr.responseText);
                        
                        // Add log message
                        const addLogMessage = window.addLogMessage || console.log;
                        addLogMessage('File uploaded successfully', 'success');
                        
                        // Update file analysis if the function exists
                        if (window.updateFileAnalysis) {
                            window.updateFileAnalysis(response);
                        }
                        
                        // Start progress monitoring if the function exists
                        if (window.monitorProgress) {
                            window.monitorProgress(response.job_id);
                        }
                        
                        // Dispatch success event
                        document.dispatchEvent(new Event('uploadSuccess'));
                    } catch (error) {
                        console.error('Error parsing response:', error);
                    }
                } else {
                    // Upload failed with HTTP error
                    this.uploadError();
                    
                    let errorMessage = "Upload failed";
                    try {
                        const errorData = JSON.parse(xhr.responseText);
                        errorMessage = errorData.detail || xhr.statusText;
                    } catch {
                        errorMessage = xhr.statusText || "Unknown error";
                    }
                    
                    // Add log message
                    const addLogMessage = window.addLogMessage || console.log;
                    addLogMessage(errorMessage, 'danger');
                    
                    // Dispatch error event
                    document.dispatchEvent(new Event('uploadError'));
                }
                
                // Re-enable the upload button
                uploadButton.disabled = false;
                uploadButton.textContent = 'Upload';
            });
            
            // Setup error event
            xhr.addEventListener('error', () => {
                this.uploadError();
                
                // Add log message
                const addLogMessage = window.addLogMessage || console.log;
                addLogMessage('Network error during upload', 'danger');
                
                // Re-enable the upload button
                uploadButton.disabled = false;
                uploadButton.textContent = 'Upload';
                
                // Dispatch error event
                document.dispatchEvent(new Event('uploadError'));
            });
            
            // Setup abort event
            xhr.addEventListener('abort', () => {
                this.uploadError();
                
                // Add log message
                const addLogMessage = window.addLogMessage || console.log;
                addLogMessage('Upload aborted', 'warning');
                
                // Re-enable the upload button
                uploadButton.disabled = false;
                uploadButton.textContent = 'Upload';
                
                // Dispatch error event
                document.dispatchEvent(new Event('uploadError'));
            });
            
            // Open and send the request
            xhr.open('POST', '/upload/genomic-data', true);
            xhr.send(formData);
            
            // Return false for good measure
            return false;
        }, true); // true for capture phase - ensures our handler runs first
    }
    
    startProgressTracking() {
        this.uploadInProgress = true;
        this.uploadComplete = false;
        
        // Make sure the panel is visible
        const panel = document.getElementById('upload-progress-panel');
        panel.classList.remove('minimized');
        
        // Show initial status
        this.showStatus('Preparing upload...');
        this.updateProgress(0);
    }
    
    completeUpload() {
        this.uploadComplete = true;
        this.uploadInProgress = false;
        this.updateProgress(100);
        this.showStatus('Upload complete!');
        
        // Auto-hide after 5 seconds
        setTimeout(() => {
            const panel = document.getElementById('upload-progress-panel');
            panel.classList.add('minimized');
        }, 5000);
    }
    
    uploadError() {
        this.uploadComplete = true;
        this.uploadInProgress = false;
        this.showStatus('Upload failed!');
        
        // Update progress bar to show error
        const progressBar = document.getElementById('upload-progress-bar');
        if (progressBar) {
            progressBar.style.width = '100%';
            progressBar.style.background = '#dc3545';
            progressBar.textContent = 'Error';
        }
    }
    
    updateProgress(percent) {
        const progressBar = document.getElementById('upload-progress-bar');
        if (progressBar) {
            progressBar.style.width = `${percent}%`;
            progressBar.textContent = `${percent}%`;
        }
    }
    
    showStatus(message) {
        const statusEl = document.getElementById('upload-status');
        if (statusEl) {
            statusEl.textContent = message;
        }
    }
    
    resetProgress() {
        this.uploadInProgress = false;
        this.uploadComplete = false;
        this.updateProgress(0);
        this.showStatus('Ready to upload');
    }
}

// Initialize the component when the DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    new GenomeDownloadProgress();
}); 