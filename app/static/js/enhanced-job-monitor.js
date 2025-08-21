/**
 * Enhanced Job Monitor for real-time job progress tracking
 * 
 * This class provides robust Server-Sent Events (SSE) monitoring with:
 * - Automatic reconnection on connection loss
 * - Exponential backoff for reconnection attempts
 * - Comprehensive error handling and logging
 * - Event validation and sanitization
 * - Progress tracking and state management
 * - Configurable retry policies
 */

class EnhancedJobMonitor {
    /**
     * Create a new job monitor instance
     * 
     * @param {string} jobId - The UUID of the job to monitor
     * @param {Object} options - Configuration options
     * @param {string} options.baseUrl - Base URL for the API (default: window.location.origin)
     * @param {number} options.reconnectDelay - Initial reconnect delay in ms (default: 1000)
     * @param {number} options.maxReconnectDelay - Maximum reconnect delay in ms (default: 30000)
     * @param {number} options.maxReconnectAttempts - Maximum reconnection attempts (default: 10)
     * @param {number} options.keepaliveInterval - Keepalive check interval in ms (default: 30000)
     * @param {boolean} options.autoReconnect - Whether to automatically reconnect (default: true)
     * @param {Function} options.onProgress - Callback for progress updates
     * @param {Function} options.onComplete - Callback for job completion
     * @param {Function} options.onError - Callback for errors
     * @param {Function} options.onReconnect - Callback for reconnection events
     */
    constructor(jobId, options = {}) {
        // Validate required parameters
        if (!jobId || typeof jobId !== 'string') {
            throw new Error('Job ID is required and must be a string');
        }
        
        // Configuration with defaults
        this.config = {
            baseUrl: options.baseUrl || window.location.origin,
            reconnectDelay: options.reconnectDelay || 1000,
            maxReconnectDelay: options.maxReconnectDelay || 30000,
            maxReconnectAttempts: options.maxReconnectAttempts || 10,
            keepaliveInterval: options.keepaliveInterval || 30000,
            autoReconnect: options.autoReconnect !== false,
            ...options
        };
        
        // Job information
        this.jobId = jobId;
        this.progressUrl = `${this.config.baseUrl}/monitoring/progress/${jobId}`;
        this.statusUrl = `${this.config.baseUrl}/monitoring/jobs/${jobId}`;
        
        // State management
        this.isConnected = false;
        this.isConnecting = false;
        this.isCompleted = false;
        this.reconnectAttempts = 0;
        this.currentReconnectDelay = this.config.reconnectDelay;
        this.lastEventTime = null;
        this.keepaliveTimer = null;
        this.reconnectTimer = null;
        
        // Event source and connection management
        this.eventSource = null;
        this.connectionTimeout = null;
        
        // Progress tracking
        this.currentProgress = 0;
        this.currentStage = null;
        this.currentStatus = null;
        this.lastProgressUpdate = null;
        
        // Callbacks
        this.callbacks = {
            onProgress: options.onProgress || this.defaultProgressCallback.bind(this),
            onComplete: options.onComplete || this.defaultCompleteCallback.bind(this),
            onError: options.onError || this.defaultErrorCallback.bind(this),
            onReconnect: options.onReconnect || this.defaultReconnectCallback.bind(this),
            onStageChange: options.onStageChange || this.defaultStageChangeCallback.bind(this),
            onStatusChange: options.onStatusChange || this.defaultStatusChangeCallback.bind(this)
        };
        
        // Bind methods to preserve context
        this.handleMessage = this.handleMessage.bind(this);
        this.handleError = this.handleError.bind(this);
        this.handleOpen = this.handleOpen.bind(this);
        this.handleClose = this.handleClose.bind(this);
        this.reconnect = this.reconnect.bind(this);
        this.checkKeepalive = this.checkKeepalive.bind(this);
        
        // Initialize logging
        this.logger = this.createLogger();
        
        // Start monitoring
        this.connect();
    }
    
    /**
     * Create a logger instance for this monitor
     * 
     * @returns {Object} Logger with different log levels
     */
    createLogger() {
        const prefix = `[JobMonitor:${this.jobId}]`;
        return {
            debug: (message, ...args) => console.debug(prefix, message, ...args),
            info: (message, ...args) => console.info(prefix, message, ...args),
            warn: (message, ...args) => console.warn(prefix, message, ...args),
            error: (message, ...args) => console.error(prefix, message, ...args)
        };
    }
    
    /**
     * Connect to the SSE stream
     * 
     * @returns {Promise<void>}
     */
    async connect() {
        if (this.isConnecting || this.isConnected) {
            this.logger.warn('Already connecting or connected');
            return;
        }
        
        try {
            this.isConnecting = true;
            this.logger.info('Connecting to job progress stream...');
            
            // Create EventSource
            this.eventSource = new EventSource(this.progressUrl);
            
            // Set up event handlers
            this.eventSource.onopen = this.handleOpen;
            this.eventSource.onmessage = this.handleMessage;
            this.eventSource.onerror = this.handleError;
            
            // Set connection timeout
            this.connectionTimeout = setTimeout(() => {
                if (!this.isConnected) {
                    this.logger.warn('Connection timeout, retrying...');
                    this.handleError(new Error('Connection timeout'));
                }
            }, 10000);
            
        } catch (error) {
            this.logger.error('Failed to create EventSource:', error);
            this.handleError(error);
        }
    }
    
    /**
     * Handle connection open event
     */
    handleOpen() {
        this.logger.info('Connection established');
        this.isConnecting = false;
        this.isConnected = true;
        this.reconnectAttempts = 0;
        this.currentReconnectDelay = this.config.reconnectDelay;
        
        // Clear connection timeout
        if (this.connectionTimeout) {
            clearTimeout(this.connectionTimeout);
            this.connectionTimeout = null;
        }
        
        // Start keepalive monitoring
        this.startKeepalive();
        
        // Notify reconnection callback
        if (this.reconnectAttempts > 0) {
            this.callbacks.onReconnect(this.reconnectAttempts);
        }
    }
    
    /**
     * Handle incoming SSE messages
     * 
     * @param {MessageEvent} event - The SSE message event
     */
    handleMessage(event) {
        try {
            this.lastEventTime = Date.now();
            
            // Parse and validate the message
            const data = this.parseMessage(event.data);
            if (!data) {
                this.logger.warn('Invalid message format:', event.data);
                return;
            }
            
            // Handle different message types
            if (data.error) {
                this.handleErrorMessage(data);
            } else if (data.complete) {
                this.handleCompleteMessage(data);
            } else if (data.keepalive) {
                this.handleKeepaliveMessage(data);
            } else {
                this.handleProgressMessage(data);
            }
            
        } catch (error) {
            this.logger.error('Error handling message:', error, 'Message:', event.data);
            this.callbacks.onError(error);
        }
    }
    
    /**
     * Parse and validate SSE message data
     * 
     * @param {string} data - Raw message data
     * @returns {Object|null} Parsed data or null if invalid
     */
    parseMessage(data) {
        try {
            const parsed = JSON.parse(data);
            
            // Basic validation
            if (!parsed || typeof parsed !== 'object') {
                return null;
            }
            
            // Ensure required fields exist
            if (!parsed.job_id && !parsed.error) {
                return null;
            }
            
            return parsed;
            
        } catch (error) {
            this.logger.warn('Failed to parse message:', error);
            return null;
        }
    }
    
    /**
     * Handle progress update messages
     * 
     * @param {Object} data - Parsed message data
     */
    handleProgressMessage(data) {
        // Update internal state
        const previousProgress = this.currentProgress;
        const previousStage = this.currentStage;
        const previousStatus = this.currentStatus;
        
        this.currentProgress = data.progress || 0;
        this.currentStage = data.stage || this.currentStage;
        this.currentStatus = data.status || this.currentStatus;
        this.lastProgressUpdate = Date.now();
        
        // Notify callbacks for changes
        if (this.currentProgress !== previousProgress) {
            this.callbacks.onProgress({
                jobId: data.job_id,
                progress: this.currentProgress,
                stage: this.currentStage,
                status: this.currentStatus,
                message: data.message,
                timestamp: data.timestamp || new Date().toISOString(),
                metadata: data.job_metadata || {}
            });
        }
        
        if (this.currentStage !== previousStage) {
            this.callbacks.onStageChange({
                previousStage,
                currentStage: this.currentStage,
                timestamp: data.timestamp || new Date().toISOString()
            });
        }
        
        if (this.currentStatus !== previousStatus) {
            this.callbacks.onStatusChange({
                previousStatus,
                currentStatus: this.currentStatus,
                timestamp: data.timestamp || new Date().toISOString()
            });
        }
    }
    
    /**
     * Handle completion messages
     * 
     * @param {Object} data - Parsed message data
     */
    handleCompleteMessage(data) {
        this.logger.info('Job completed:', data.status);
        this.isCompleted = true;
        this.isConnected = false;
        
        // Stop monitoring
        this.disconnect();
        
        // Notify completion callback
        this.callbacks.onComplete({
            jobId: data.job_id,
            status: data.status,
            finalProgress: data.progress,
            finalStage: data.stage,
            message: data.message,
            timestamp: data.timestamp || new Date().toISOString(),
            metadata: data.job_metadata || {}
        });
    }
    
    /**
     * Handle error messages from the server
     * 
     * @param {Object} data - Parsed message data
     */
    handleErrorMessage(data) {
        this.logger.warn('Server error:', data.error);
        
        if (data.reconnect === false) {
            // Server indicates no reconnection should be attempted
            this.logger.info('Server requested no reconnection');
            this.disconnect();
            this.callbacks.onError(new Error(data.error));
        } else {
            // Attempt reconnection
            this.handleError(new Error(data.error));
        }
    }
    
    /**
     * Handle keepalive messages
     * 
     * @param {Object} data - Parsed message data
     */
    handleKeepaliveMessage(data) {
        this.logger.debug('Keepalive received');
        this.lastEventTime = Date.now();
    }
    
    /**
     * Handle connection errors
     * 
     * @param {Error} error - The error that occurred
     */
    handleError(error) {
        this.logger.error('Connection error:', error);
        
        this.isConnecting = false;
        this.isConnected = false;
        
        // Clear connection timeout
        if (this.connectionTimeout) {
            clearTimeout(this.connectionTimeout);
            this.connectionTimeout = null;
        }
        
        // Stop keepalive monitoring
        this.stopKeepalive();
        
        // Close existing connection
        if (this.eventSource) {
            this.eventSource.close();
            this.eventSource = null;
        }
        
        // Attempt reconnection if enabled and not completed
        if (this.config.autoReconnect && !this.isCompleted && this.reconnectAttempts < this.config.maxReconnectAttempts) {
            this.scheduleReconnect();
        } else if (this.reconnectAttempts >= this.config.maxReconnectAttempts) {
            this.logger.error('Max reconnection attempts reached');
            this.callbacks.onError(new Error('Max reconnection attempts reached'));
        } else {
            this.callbacks.onError(error);
        }
    }
    
    /**
     * Handle connection close events
     */
    handleClose() {
        this.logger.info('Connection closed');
        this.isConnecting = false;
        this.isConnected = false;
        
        // Stop keepalive monitoring
        this.stopKeepalive();
        
        // Attempt reconnection if enabled and not completed
        if (this.config.autoReconnect && !this.isCompleted && this.reconnectAttempts < this.config.maxReconnectAttempts) {
            this.scheduleReconnect();
        }
    }
    
    /**
     * Schedule a reconnection attempt
     */
    scheduleReconnect() {
        this.reconnectAttempts++;
        this.currentReconnectDelay = Math.min(
            this.currentReconnectDelay * 2,
            this.config.maxReconnectDelay
        );
        
        this.logger.info(`Scheduling reconnection attempt ${this.reconnectAttempts} in ${this.currentReconnectDelay}ms`);
        
        this.reconnectTimer = setTimeout(() => {
            this.reconnect();
        }, this.currentReconnectDelay);
    }
    
    /**
     * Attempt to reconnect
     */
    async reconnect() {
        if (this.isConnecting || this.isCompleted) {
            return;
        }
        
        this.logger.info(`Attempting reconnection ${this.reconnectAttempts}/${this.config.maxReconnectAttempts}`);
        
        try {
            await this.connect();
        } catch (error) {
            this.logger.error('Reconnection failed:', error);
            this.handleError(error);
        }
    }
    
    /**
     * Start keepalive monitoring
     */
    startKeepalive() {
        this.stopKeepalive(); // Clear any existing timer
        
        this.keepaliveTimer = setInterval(() => {
            this.checkKeepalive();
        }, this.config.keepaliveInterval);
    }
    
    /**
     * Stop keepalive monitoring
     */
    stopKeepalive() {
        if (this.keepaliveTimer) {
            clearInterval(this.keepaliveTimer);
            this.keepaliveTimer = null;
        }
    }
    
    /**
     * Check if keepalive is working
     */
    checkKeepalive() {
        if (!this.lastEventTime) {
            return; // No events received yet
        }
        
        const timeSinceLastEvent = Date.now() - this.lastEventTime;
        const keepaliveThreshold = this.config.keepaliveInterval * 2;
        
        if (timeSinceLastEvent > keepaliveThreshold) {
            this.logger.warn('Keepalive timeout, reconnecting...');
            this.handleError(new Error('Keepalive timeout'));
        }
    }
    
    /**
     * Manually refresh job status
     * 
     * @returns {Promise<Object>} Current job status
     */
    async refreshStatus() {
        try {
            const response = await fetch(this.statusUrl);
            
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }
            
            const status = await response.json();
            
            // Update internal state
            this.currentProgress = status.progress || 0;
            this.currentStage = status.stage || this.currentStage;
            this.currentStatus = status.status || this.currentStatus;
            
            return status;
            
        } catch (error) {
            this.logger.error('Failed to refresh status:', error);
            throw error;
        }
    }
    
    /**
     * Disconnect and stop monitoring
     */
    disconnect() {
        this.logger.info('Disconnecting monitor');
        
        // Clear all timers
        if (this.connectionTimeout) {
            clearTimeout(this.connectionTimeout);
            this.connectionTimeout = null;
        }
        
        if (this.reconnectTimer) {
            clearTimeout(this.reconnectTimer);
            this.reconnectTimer = null;
        }
        
        this.stopKeepalive();
        
        // Close EventSource
        if (this.eventSource) {
            this.eventSource.close();
            this.eventSource = null;
        }
        
        // Reset state
        this.isConnecting = false;
        this.isConnected = false;
    }
    
    /**
     * Get current monitor state
     * 
     * @returns {Object} Current state information
     */
    getState() {
        return {
            jobId: this.jobId,
            isConnected: this.isConnected,
            isConnecting: this.isConnecting,
            isCompleted: this.isCompleted,
            reconnectAttempts: this.reconnectAttempts,
            currentProgress: this.currentProgress,
            currentStage: this.currentStage,
            currentStatus: this.currentStatus,
            lastEventTime: this.lastEventTime,
            lastProgressUpdate: this.lastProgressUpdate
        };
    }
    
    /**
     * Default progress callback
     * 
     * @param {Object} data - Progress data
     */
    defaultProgressCallback(data) {
        this.logger.info(`Progress: ${data.progress}% - ${data.stage} - ${data.message || 'No message'}`);
    }
    
    /**
     * Default completion callback
     * 
     * @param {Object} data - Completion data
     */
    defaultCompleteCallback(data) {
        this.logger.info(`Job completed with status: ${data.status}`);
    }
    
    /**
     * Default error callback
     * 
     * @param {Error} error - The error that occurred
     */
    defaultErrorCallback(error) {
        this.logger.error('Monitor error:', error);
    }
    
    /**
     * Default reconnection callback
     * 
     * @param {number} attempt - Reconnection attempt number
     */
    defaultReconnectCallback(attempt) {
        this.logger.info(`Reconnected after ${attempt} attempts`);
    }
    
    /**
     * Default stage change callback
     * 
     * @param {Object} data - Stage change data
     */
    defaultStageChangeCallback(data) {
        this.logger.info(`Stage changed: ${data.previousStage} → ${data.currentStage}`);
    }
    
    /**
     * Default status change callback
     * 
     * @param {Object} data - Status change data
     */
    defaultStatusChangeCallback(data) {
        this.logger.info(`Status changed: ${data.previousStatus} → ${data.currentStatus}`);
    }
}

// Export for use in other modules
if (typeof module !== 'undefined' && module.exports) {
    module.exports = EnhancedJobMonitor;
} else if (typeof window !== 'undefined') {
    window.EnhancedJobMonitor = EnhancedJobMonitor;
}
