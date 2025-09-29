/**
 * Workflow Monitor for real-time workflow progress tracking
 * 
 * This class provides WebSocket-based monitoring with:
 * - Real-time workflow progress updates
 * - Step-by-step progress visualization
 * - Automatic reconnection on connection loss
 * - Comprehensive error handling and logging
 * - Workflow management capabilities
 */

class WorkflowMonitor {
    /**
     * Create a new workflow monitor instance
     * 
     * @param {string} workflowId - The UUID of the workflow to monitor
     * @param {Object} options - Configuration options
     * @param {string} options.baseUrl - Base URL for the API (default: window.location.origin)
     * @param {number} options.reconnectDelay - Initial reconnect delay in ms (default: 1000)
     * @param {number} options.maxReconnectDelay - Maximum reconnect delay in ms (default: 30000)
     * @param {number} options.maxReconnectAttempts - Maximum reconnection attempts (default: 10)
     * @param {boolean} options.autoReconnect - Whether to automatically reconnect (default: true)
     * @param {Function} options.onProgress - Callback for progress updates
     * @param {Function} options.onComplete - Callback for workflow completion
     * @param {Function} options.onError - Callback for errors
     * @param {Function} options.onReconnect - Callback for reconnection events
     * @param {Function} options.onStepUpdate - Callback for step updates
     * @param {Function} options.onLogUpdate - Callback for log updates
     */
    constructor(workflowId, options = {}) {
        console.log('WorkflowMonitor constructor called with workflowId:', workflowId, 'options:', options);
        
        // Validate required parameters
        if (!workflowId || typeof workflowId !== 'string') {
            console.error('Invalid workflowId provided:', workflowId);
            throw new Error('Workflow ID is required and must be a string');
        }
        
        // Configuration with defaults
        this.config = {
            baseUrl: options.baseUrl || window.location.origin,
            reconnectDelay: options.reconnectDelay || 1000,
            maxReconnectDelay: options.maxReconnectDelay || 30000,
            maxReconnectAttempts: options.maxReconnectAttempts || 10,
            autoReconnect: options.autoReconnect !== false,
            ...options
        };
        
        // Workflow information
        this.workflowId = workflowId;
        this.wsUrl = this.getWebSocketUrl();
        
        // State management
        this.isConnected = false;
        this.isConnecting = false;
        this.isCompleted = false;
        this.reconnectAttempts = 0;
        this.currentReconnectDelay = this.config.reconnectDelay;
        this.lastMessageTime = null;
        this.reconnectTimer = null;
        
        // WebSocket connection
        this.websocket = null;
        this.connectionTimeout = null;
        
        // Workflow state
        this.workflow = null;
        this.steps = [];
        this.logs = [];
        this.currentProgress = 0;
        this.currentStep = null;
        this.workflowStatus = 'pending';
        
        // Callbacks
        this.callbacks = {
            onProgress: options.onProgress || this.defaultProgressCallback.bind(this),
            onComplete: options.onComplete || this.defaultCompleteCallback.bind(this),
            onError: options.onError || this.defaultErrorCallback.bind(this),
            onReconnect: options.onReconnect || this.defaultReconnectCallback.bind(this),
            onStepUpdate: options.onStepUpdate || this.defaultStepUpdateCallback.bind(this),
            onLogUpdate: options.onLogUpdate || this.defaultLogUpdateCallback.bind(this),
            onWorkflowUpdate: options.onWorkflowUpdate || this.defaultWorkflowUpdateCallback.bind(this)
        };
        
        // Bind methods to preserve context
        this.handleMessage = this.handleMessage.bind(this);
        this.handleError = this.handleError.bind(this);
        this.handleOpen = this.handleOpen.bind(this);
        this.handleClose = this.handleClose.bind(this);
        this.reconnect = this.reconnect.bind(this);
        
        // Initialize logging
        this.logger = this.createLogger();
        
        // Start monitoring
        this.connect();
        
        // Add visibility change detection to refresh progress when tab becomes visible
        this.setupVisibilityRefresh();
    }
    
    /**
     * Create a logger instance for this monitor
     * 
     * @returns {Object} Logger with different log levels
     */
    createLogger() {
        const prefix = `[WorkflowMonitor:${this.workflowId.substring(0, 8)}]`;
        return {
            debug: (message, ...args) => console.debug(prefix, message, ...args),
            info: (message, ...args) => console.info(prefix, message, ...args),
            warn: (message, ...args) => console.warn(prefix, message, ...args),
            error: (message, ...args) => console.error(prefix, message, ...args)
        };
    }
    
    /**
     * Set up simple visibility refresh - just refresh progress when tab becomes visible
     */
    setupVisibilityRefresh() {
        // Listen for visibility changes
        document.addEventListener('visibilitychange', () => {
            if (!document.hidden && !this.isCompleted) {
                // Tab became visible - refresh progress to catch up on missed updates
                this.logger.info('Tab became visible, refreshing progress...');
                this.refreshProgress();
            }
        });
    }
    
    /**
     * Refresh progress by making a direct API call to catch up on missed updates
     */
    async refreshProgress() {
        try {
            const response = await fetch(`${this.config.baseUrl}/api/v1/upload/status/${this.workflowId}`);
            if (response.ok) {
                const data = await response.json();
                this.logger.debug('Progress refresh received data:', data);
                
                // Update progress if we got new data
                if (data.progress_percentage !== undefined) {
                    this.currentProgress = data.progress_percentage;
                    this.workflowStatus = data.status;
                    
                    // Trigger progress callback to update UI
                    this.callbacks.onProgress(data.progress_percentage, data);
                    
                    // Check if completed while tab was hidden
                    if (data.status === 'completed' || data.status === 'failed' || data.status === 'cancelled') {
                        this.isCompleted = true;
                        this.callbacks.onComplete(data);
                    }
                }
            }
        } catch (error) {
            this.logger.warn('Progress refresh failed:', error);
        }
    }
    
    /**
     * Get WebSocket URL for the workflow
     * 
     * @returns {string} WebSocket URL
     */
    getWebSocketUrl() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const host = window.location.host;
        return `${protocol}//${host}/api/v1/workflows/${this.workflowId}/ws`;
    }
    
    /**
     * Connect to the WebSocket stream
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
            this.logger.info('Connecting to workflow WebSocket...');
            console.log('WorkflowMonitor: Attempting to connect to WebSocket:', this.wsUrl);
            
            // Create WebSocket connection
            this.websocket = new WebSocket(this.wsUrl);
            
            // Set up event handlers
            this.websocket.onopen = this.handleOpen;
            this.websocket.onmessage = this.handleMessage;
            this.websocket.onerror = this.handleError;
            this.websocket.onclose = this.handleClose;
            
            // Set connection timeout
            this.connectionTimeout = setTimeout(() => {
                if (this.isConnecting && !this.isConnected) {
                    this.logger.error('Connection timeout');
                    this.handleError(new Error('Connection timeout'));
                }
            }, 10000);
            
        } catch (error) {
            this.logger.error('Failed to create WebSocket:', error);
            this.handleError(error);
        }
    }
    
    /**
     * Handle WebSocket connection open
     */
    handleOpen() {
        console.log('WorkflowMonitor: WebSocket connected successfully');
        console.log('WorkflowMonitor: WebSocket URL:', this.wsUrl);
        console.log('WorkflowMonitor: WebSocket readyState:', this.websocket.readyState);
        this.logger.info('WebSocket connected');
        this.isConnected = true;
        this.isConnecting = false;
        this.reconnectAttempts = 0;
        this.currentReconnectDelay = this.config.reconnectDelay;
        
        // Clear connection timeout
        if (this.connectionTimeout) {
            clearTimeout(this.connectionTimeout);
            this.connectionTimeout = null;
        }
        
        // Send ping to keep connection alive
        this.sendPing();
    }
    
    /**
     * Handle incoming WebSocket messages
     * 
     * @param {MessageEvent} event - The WebSocket message event
     */
    handleMessage(event) {
        try {
            this.lastMessageTime = Date.now();
            
            const data = JSON.parse(event.data);
            console.log('WorkflowMonitor: Received raw message:', data);
            console.log('WorkflowMonitor: Message type:', data.type);
            console.log('WorkflowMonitor: Message data:', data.data);
            this.logger.debug('Received message:', data);
            
            // Validate message structure
            if (!data.type) {
                this.logger.warn('Received message without type:', data);
                return;
            }
            
            // Handle different message types
            switch (data.type) {
                case 'initial_status':
                    this.handleInitialStatus(data.data);
                    break;
                case 'workflow_update':
                    // Check if this is a progress update or a nested message
                    if (data.data && data.data.type) {
                        // This is a nested message (step_update, log_update, etc.)
                        this.handleNestedMessage(data.data);
                    } else {
                        // This is a direct workflow progress update
                        this.handleWorkflowUpdate(data.data);
                    }
                    break;
                case 'step_update':
                    this.handleStepUpdate(data.data);
                    break;
                case 'log_update':
                    this.handleLogUpdate(data.data);
                    break;
                case 'error_notification':
                    this.handleErrorNotification(data);
                    break;
                case 'pong':
                    this.handlePong(data);
                    break;
                case 'heartbeat':
                    this.handleHeartbeat(data);
                    break;
                default:
                    this.logger.warn('Unknown message type:', data.type);
            }
            
        } catch (error) {
            this.logger.error('Error handling message:', error);
            this.callbacks.onError(error);
        }
    }
    
    /**
     * Handle initial workflow status
     * 
     * @param {Object} data - Initial status data
     */
    handleInitialStatus(data) {
        this.logger.info('Received initial workflow status');
        this.workflow = data;
        this.workflowStatus = data.status;
        this.currentProgress = data.progress_percentage || 0;
        
        this.callbacks.onWorkflowUpdate(data);
        this.callbacks.onProgress(data.progress_percentage || 0, data);
    }
    
    /**
     * Handle nested messages within workflow updates
     * 
     * @param {Object} data - Nested message data
     */
    handleNestedMessage(data) {
        console.log('WorkflowMonitor: Nested message received:', data);
        // Route nested messages to their appropriate handlers
        switch (data.type) {
            case 'step_update':
                this.handleStepUpdate(data.data || data);
                break;
            case 'log_update':
                this.handleLogUpdate(data.data || data);
                break;
            case 'workflow_update':
                // Handle nested workflow updates (progress updates)
                this.handleWorkflowUpdate(data.data || data);
                break;
            default:
                console.log('WorkflowMonitor: Unknown nested message type:', data.type);
        }
    }

    /**
     * Handle workflow update
     * 
     * @param {Object} data - Workflow update data
     */
    handleWorkflowUpdate(data) {
        console.log('WorkflowMonitor: Workflow update received:', data);
        console.log('WorkflowMonitor: Data keys:', Object.keys(data));
        console.log('WorkflowMonitor: Data values:', {
            workflow_id: data.workflow_id,
            status: data.status,
            progress_percentage: data.progress_percentage,
            current_step: data.current_step,
            message: data.message
        });
        this.logger.debug('Workflow update received');
        
        if (data.workflow_id) {
            this.workflow = { ...this.workflow, ...data };
            this.workflowStatus = data.status || this.workflowStatus;
            this.currentProgress = data.progress_percentage || this.currentProgress;
            
            console.log('WorkflowMonitor: Updated workflow state:', {
                status: this.workflowStatus,
                progress: this.currentProgress,
                currentStep: data.current_step
            });
            
            this.callbacks.onWorkflowUpdate(data);
            this.callbacks.onProgress(data.progress_percentage || this.currentProgress, data);
            
            // Check if workflow is completed
            if (data.status === 'completed' || data.status === 'failed' || data.status === 'cancelled') {
                this.isCompleted = true;
                this.callbacks.onComplete(data);
            }
        } else {
            console.warn('WorkflowMonitor: No workflow_id in data:', data);
        }
    }
    
    /**
     * Handle step update
     * 
     * @param {Object} data - Step update data
     */
    handleStepUpdate(data) {
        console.log('WorkflowMonitor: Step update received:', data);
        this.logger.debug('Step update received:', data.step_name);
        
        // Extract the actual step data from nested structure
        const stepData = data.data || data;
        
        // Update or add step
        const stepIndex = this.steps.findIndex(step => step.step_name === stepData.step_name);
        if (stepIndex >= 0) {
            this.steps[stepIndex] = { ...this.steps[stepIndex], ...stepData };
        } else {
            this.steps.push(stepData);
        }
        
        // Update current step if it's running
        if (stepData.status === 'running') {
            this.currentStep = stepData.step_name;
        }
        
        console.log('WorkflowMonitor: Calling onStepUpdate callback with data:', stepData);
        this.callbacks.onStepUpdate(stepData);
    }
    
    /**
     * Handle log update
     * 
     * @param {Object} data - Log update data
     */
    handleLogUpdate(data) {
        console.log('WorkflowMonitor: Log update received:', data);
        this.logger.debug('Log update received');
        
        // Extract the actual log data from nested structure
        const logData = data.data || data;
        
        // Ensure we have the required fields
        const processedLogData = {
            message: logData.message || logData.data?.message || 'No message',
            log_level: logData.log_level || logData.data?.log_level || 'info',
            timestamp: logData.timestamp || logData.data?.timestamp || new Date().toISOString(),
            metadata: logData.metadata || logData.data?.metadata || {}
        };
        
        // Add log entry
        this.logs.unshift(processedLogData);
        
        // Keep only last 100 logs
        if (this.logs.length > 100) {
            this.logs = this.logs.slice(0, 100);
        }
        
        console.log('WorkflowMonitor: Calling onLogUpdate callback with data:', processedLogData);
        this.callbacks.onLogUpdate(processedLogData);
    }
    
    /**
     * Handle error notification
     * 
     * @param {Object} data - Error notification data
     */
    handleErrorNotification(data) {
        this.logger.error('Error notification received:', data.error_message);
        this.callbacks.onError(new Error(data.error_message));
    }
    
    /**
     * Handle pong response
     * 
     * @param {Object} data - Pong data
     */
    handlePong(data) {
        this.logger.debug('Pong received');
    }
    
    /**
     * Handle heartbeat
     * 
     * @param {Object} data - Heartbeat data
     */
    handleHeartbeat(data) {
        this.logger.debug('Heartbeat received');
    }
    
    /**
     * Handle WebSocket errors
     * 
     * @param {Event|Error} error - Error event or error object
     */
    handleError(error) {
        console.error('WorkflowMonitor: WebSocket error:', error);
        this.logger.error('WebSocket error:', error);
        this.isConnecting = false;
        this.isConnected = false;
        
        // Clear connection timeout
        if (this.connectionTimeout) {
            clearTimeout(this.connectionTimeout);
            this.connectionTimeout = null;
        }
        
        // Check if this is a 422 error (workflow not found)
        if (error.code === 4004 || (error.reason && error.reason.includes('not found'))) {
            this.logger.warn('Workflow not found, will retry in 5 seconds');
            this.callbacks.onError(new Error('Workflow not found, retrying...'));
            
            // Wait 5 seconds before retrying for workflow not found
            if (this.config.autoReconnect && !this.isCompleted && this.reconnectAttempts < this.config.maxReconnectAttempts) {
                setTimeout(() => {
                    this.scheduleReconnect();
                }, 5000);
            }
        } else {
            this.callbacks.onError(error);
            
            // Attempt reconnection if enabled and not completed
            if (this.config.autoReconnect && !this.isCompleted && this.reconnectAttempts < this.config.maxReconnectAttempts) {
                this.scheduleReconnect();
            }
        }
    }
    
    /**
     * Handle WebSocket close
     * 
     * @param {CloseEvent} event - Close event
     */
    handleClose(event) {
        console.log('WorkflowMonitor: WebSocket closed:', event.code, event.reason);
        this.logger.info('WebSocket closed:', event.code, event.reason);
        this.isConnected = false;
        this.isConnecting = false;
        
        // Clear connection timeout
        if (this.connectionTimeout) {
            clearTimeout(this.connectionTimeout);
            this.connectionTimeout = null;
        }
        
        // Handle specific close codes
        if (event.code === 4004) {
            // Workflow not found - wait longer before retrying
            this.logger.warn('Workflow not found, will retry in 5 seconds');
            this.callbacks.onError(new Error('Workflow not found, retrying...'));
            
            if (this.config.autoReconnect && !this.isCompleted && this.reconnectAttempts < this.config.maxReconnectAttempts) {
                setTimeout(() => {
                    this.scheduleReconnect();
                }, 5000);
            }
        } else if (event.code === 4000) {
            // Invalid workflow ID format - don't retry
            this.logger.error('Invalid workflow ID format, not retrying');
            this.callbacks.onError(new Error('Invalid workflow ID format'));
        } else {
            // Attempt reconnection if enabled and not completed
            if (this.config.autoReconnect && !this.isCompleted && this.reconnectAttempts < this.config.maxReconnectAttempts) {
                this.scheduleReconnect();
            }
        }
    }
    
    /**
     * Schedule reconnection attempt
     */
    scheduleReconnect() {
        if (this.reconnectTimer) {
            clearTimeout(this.reconnectTimer);
        }
        
        this.reconnectAttempts++;
        this.logger.info(`Scheduling reconnection attempt ${this.reconnectAttempts}/${this.config.maxReconnectAttempts} in ${this.currentReconnectDelay}ms`);
        
        this.reconnectTimer = setTimeout(() => {
            this.reconnect();
        }, this.currentReconnectDelay);
        
        // Increase delay for next attempt (exponential backoff)
        this.currentReconnectDelay = Math.min(
            this.currentReconnectDelay * 2,
            this.config.maxReconnectDelay
        );
        
        this.callbacks.onReconnect(this.reconnectAttempts);
    }
    
    /**
     * Attempt to reconnect
     */
    reconnect() {
        if (this.isCompleted) {
            this.logger.info('Workflow completed, not reconnecting');
            return;
        }
        
        this.logger.info('Attempting to reconnect...');
        this.connect();
    }
    
    /**
     * Send ping to keep connection alive
     */
    sendPing() {
        if (this.websocket && this.websocket.readyState === WebSocket.OPEN) {
            this.websocket.send(JSON.stringify({
                type: 'ping',
                timestamp: Date.now()
            }));
        }
    }
    
    /**
     * Send message to WebSocket
     * 
     * @param {Object} message - Message to send
     */
    sendMessage(message) {
        if (this.websocket && this.websocket.readyState === WebSocket.OPEN) {
            this.websocket.send(JSON.stringify(message));
        } else {
            this.logger.warn('WebSocket not connected, cannot send message');
        }
    }
    
    /**
     * Disconnect from WebSocket
     */
    disconnect() {
        this.logger.info('Disconnecting from WebSocket');
        this.isCompleted = true;
        
        // Clear timers
        if (this.reconnectTimer) {
            clearTimeout(this.reconnectTimer);
            this.reconnectTimer = null;
        }
        
        if (this.connectionTimeout) {
            clearTimeout(this.connectionTimeout);
            this.connectionTimeout = null;
        }
        
        // Close WebSocket
        if (this.websocket) {
            this.websocket.close();
            this.websocket = null;
        }
        
        this.isConnected = false;
        this.isConnecting = false;
    }
    
    /**
     * Get current workflow status
     * 
     * @returns {Object} Current workflow status
     */
    getStatus() {
        return {
            workflowId: this.workflowId,
            isConnected: this.isConnected,
            isCompleted: this.isCompleted,
            workflow: this.workflow,
            steps: this.steps,
            logs: this.logs,
            currentProgress: this.currentProgress,
            currentStep: this.currentStep,
            workflowStatus: this.workflowStatus,
            reconnectAttempts: this.reconnectAttempts
        };
    }
    
    // Default callback implementations
    defaultProgressCallback(progress, data) {
        this.logger.info(`Progress: ${progress}%`);
    }
    
    defaultCompleteCallback(data) {
        this.logger.info('Workflow completed:', data);
    }
    
    defaultErrorCallback(error) {
        this.logger.error('Error occurred:', error);
    }
    
    defaultReconnectCallback(attempts) {
        this.logger.info(`Reconnection attempt ${attempts}`);
    }
    
    defaultStepUpdateCallback(data) {
        this.logger.info(`Step update: ${data.step_name} - ${data.status}`);
    }
    
    defaultLogUpdateCallback(data) {
        this.logger.info(`Log: ${data.message}`);
    }
    
    defaultWorkflowUpdateCallback(data) {
        this.logger.info('Workflow updated:', data);
    }
}

// Export for use in other modules
if (typeof module !== 'undefined' && module.exports) {
    module.exports = WorkflowMonitor;
} else if (typeof window !== 'undefined') {
    window.WorkflowMonitor = WorkflowMonitor;
}
