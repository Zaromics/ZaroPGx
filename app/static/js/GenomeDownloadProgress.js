/**
 * Upload Progress Component
 * Displays the progress of the user's file upload
 */
class GenomeDownloadProgress {
    constructor() {
        console.log("Initializing GenomeDownloadProgress component");
        // Instead of using a separate container, we'll insert into the form
        this.form = document.getElementById('uploadForm');
        if (!this.form) {
            console.warn('Upload form not found');
            return;
        }
        
        this.uploadInProgress = false;
        this.uploadComplete = false;
        this.init();
    }
    
    init() {
        console.log("Creating upload progress UI");
        
        // Create the progress element to inject into the form
        const progressDiv = document.createElement('div');
        progressDiv.id = 'file-upload-progress';
        progressDiv.className = 'mb-3 d-none'; // Initially hidden
        progressDiv.innerHTML = `
            <label class="form-label">File Upload Progress</label>
            <div class="progress mb-2" style="height: 25px;">
                <div id="upload-progress-bar" class="progress-bar progress-bar-striped progress-bar-animated bg-primary" 
                     role="progressbar" aria-valuenow="0" aria-valuemin="0" aria-valuemax="100" style="width: 0%">
                    0%
                </div>
            </div>
            <div id="upload-status" class="form-text mb-2">Ready to upload</div>
            <div id="upload-size-info" class="form-text small text-muted">0 KB / 0 KB</div>
        `;
        
        // Insert after the file input element
        const fileInput = document.getElementById('genomicFile');
        if (fileInput && fileInput.parentNode) {
            fileInput.parentNode.appendChild(progressDiv);
        } else {
            // Fallback - insert at the beginning of the form
            this.form.insertBefore(progressDiv, this.form.firstChild);
        }
        
        // Override the form submission to use XHR for progress tracking
        this.overrideFormSubmission();
        console.log("Upload progress component initialized");
    }
    
    formatBytes(bytes, decimals = 2) {
        if (bytes === 0) return '0 Bytes';
        
        const k = 1024;
        const dm = decimals < 0 ? 0 : decimals;
        const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB'];
        
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        
        return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
    }
    
    overrideFormSubmission() {
        console.log("Setting up form submission override");
        const form = this.form;
        const fileInput = document.getElementById('genomicFile');
        
        if (!form || !fileInput) {
            console.warn('Upload form or file input not found');
            return;
        }
        
        console.log("Found upload form:", form.id);
        
        // Reset progress when file selection changes
        fileInput.addEventListener('change', () => {
            console.log("File selection changed");
            this.resetProgress();
            
            // Show file size info when a file is selected
            if (fileInput.files && fileInput.files[0]) {
                const fileSize = fileInput.files[0].size;
                const formattedSize = this.formatBytes(fileSize);
                document.getElementById('upload-size-info').textContent = `0 KB / ${formattedSize}`;
                this.showStatus('File selected, ready to upload');
            } else {
                document.getElementById('upload-size-info').textContent = '0 KB / 0 KB';
                this.showStatus('No file selected');
            }
        });
        
        // Remove any existing submit handlers to avoid conflicts
        const oldSubmit = form.onsubmit;
        form.onsubmit = null;
        
        // Replace the submit handler
        form.onsubmit = (e) => {
            console.log("Form submit triggered", e);
            // Prevent the default form submission
            e.preventDefault();
            e.stopPropagation();
            
            // Check if a file is selected
            if (!fileInput.files || fileInput.files.length === 0) {
                console.warn("No file selected");
                const addLogMessage = window.addLogMessage || console.log;
                addLogMessage('Please select a file to upload', 'warning');
                return false;
            }
            
            console.log("Default form submission prevented");
            
            // Get form data
            const formData = new FormData(form);
            const uploadButton = document.getElementById('uploadButton');
            
            console.log("Form data prepared, starting upload");
            
            // Disable the upload button
            uploadButton.disabled = true;
            uploadButton.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Uploading...';
            
            // Start progress tracking
            this.startProgressTracking();
            
            // Create XHR request
            const xhr = new XMLHttpRequest();
            
            // Debug - log all XHR state changes
            xhr.onreadystatechange = () => {
                console.log(`XHR state changed: ${xhr.readyState}, status: ${xhr.status}`);
            };
            
            // Setup progress event
            xhr.upload.addEventListener('progress', (event) => {
                console.log(`Upload progress: ${event.loaded} / ${event.total}`);
                if (event.lengthComputable) {
                    const percentComplete = Math.round((event.loaded / event.total) * 100);
                    console.log(`Upload progress: ${percentComplete}%`);
                    this.updateProgress(percentComplete, event.loaded, event.total);
                    
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
                console.log(`XHR load event: status ${xhr.status}, response length: ${xhr.responseText.length}`);
                
                if (xhr.status >= 200 && xhr.status < 300) {
                    // Upload successful
                    this.completeUpload();
                    
                    // Process the response
                    try {
                        const response = JSON.parse(xhr.responseText);
                        console.log("Response data:", response);
                        
                        // Add log message
                        const addLogMessage = window.addLogMessage || console.log;
                        addLogMessage('File uploaded successfully', 'success');
                        
                        // Update file analysis if the function exists
                        if (window.updateFileAnalysis) {
                            console.log("Calling updateFileAnalysis with response");
                            window.updateFileAnalysis(response);
                        } else {
                            console.warn("updateFileAnalysis function not found");
                        }
                        
                        // Start progress monitoring if the function exists
                        if (window.monitorProgress) {
                            console.log("Starting pipeline progress monitoring with job_id:", response.job_id);
                            window.monitorProgress(response.job_id);
                        } else {
                            console.warn("monitorProgress function not found");
                        }
                        
                        // If this is a VCF file and we have the auto-polling function, start it
                        if (window.isVcfFile && window.startVcfAutoPolling) {
                            console.log("Starting VCF auto-polling for job_id:", response.job_id);
                            window.startVcfAutoPolling(response.job_id);
                        }
                        
                        // Dispatch success event
                        document.dispatchEvent(new Event('uploadSuccess'));
                    } catch (error) {
                        console.error('Error parsing response:', error);
                        console.log("Raw response:", xhr.responseText.substring(0, 1000) + "...");
                    }
                } else {
                    // Upload failed with HTTP error
                    console.error("Upload failed with status:", xhr.status);
                    this.uploadError();
                    
                    let errorMessage = "Upload failed";
                    try {
                        const errorData = JSON.parse(xhr.responseText);
                        errorMessage = errorData.detail || xhr.statusText;
                        console.error("Error response:", errorData);
                    } catch (e) {
                        console.error("Could not parse error response:", e);
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
                console.error("XHR error event triggered");
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
                console.warn("XHR abort event triggered");
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
            console.log("Opening XHR request to /upload/genomic-data");
            xhr.open('POST', '/upload/genomic-data', true);
            xhr.send(formData);
            console.log("XHR request sent");
            
            // Return false to prevent normal form submission
            return false;
        };
        
        console.log("Form submit override completed");
    }
    
    startProgressTracking() {
        console.log("Starting upload progress tracking");
        this.uploadInProgress = true;
        this.uploadComplete = false;
        
        // Show the progress element
        const progressDiv = document.getElementById('file-upload-progress');
        if (progressDiv) {
            progressDiv.classList.remove('d-none');
        }
        
        // Show initial status
        this.showStatus('Preparing upload...');
        this.updateProgress(0, 0, 1);
    }
    
    completeUpload() {
        console.log("Upload complete");
        this.uploadComplete = true;
        this.uploadInProgress = false;
        
        // Get file size info from the file input
        const fileInput = document.getElementById('genomicFile');
        let totalSize = 0;
        if (fileInput && fileInput.files && fileInput.files[0]) {
            totalSize = fileInput.files[0].size;
        }
        
        this.updateProgress(100, totalSize, totalSize);
        this.showStatus('Upload complete');
    }
    
    uploadError() {
        console.error("Upload error");
        this.uploadComplete = true;
        this.uploadInProgress = false;
        this.showStatus('Upload failed!');
        
        // Update progress bar to show error
        const progressBar = document.getElementById('upload-progress-bar');
        if (progressBar) {
            progressBar.style.width = '100%';
            progressBar.classList.remove('bg-primary');
            progressBar.classList.add('bg-danger');
            progressBar.textContent = 'Error';
        }
    }
    
    updateProgress(percent, loaded, total) {
        const progressBar = document.getElementById('upload-progress-bar');
        const sizeInfo = document.getElementById('upload-size-info');
        
        if (progressBar) {
            progressBar.style.width = `${percent}%`;
            progressBar.textContent = `${percent}%`;
            progressBar.setAttribute('aria-valuenow', percent);
        }
        
        if (sizeInfo) {
            const loadedFormatted = this.formatBytes(loaded);
            const totalFormatted = this.formatBytes(total);
            sizeInfo.textContent = `${loadedFormatted} / ${totalFormatted}`;
        }
    }
    
    showStatus(message) {
        const statusEl = document.getElementById('upload-status');
        if (statusEl) {
            statusEl.textContent = message;
        }
    }
    
    resetProgress() {
        console.log("Resetting upload progress");
        this.uploadInProgress = false;
        this.uploadComplete = false;
        
        // Hide the progress element
        const progressDiv = document.getElementById('file-upload-progress');
        if (progressDiv) {
            progressDiv.classList.add('d-none');
        }
        
        // Reset progress bar
        const progressBar = document.getElementById('upload-progress-bar');
        if (progressBar) {
            progressBar.style.width = '0%';
            progressBar.textContent = '0%';
            progressBar.setAttribute('aria-valuenow', 0);
            progressBar.classList.remove('bg-danger');
            progressBar.classList.add('bg-primary');
        }
        
        this.showStatus('Ready to upload');
    }
}

// Initialize the component when the DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    console.log("DOM loaded, initializing GenomeDownloadProgress");
    new GenomeDownloadProgress();
}); 