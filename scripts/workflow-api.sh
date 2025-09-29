#!/bin/bash
# Workflow API utilities for vanilla containers
# This script provides shell functions for communicating with the workflow monitoring API

# Configuration
WORKFLOW_API_BASE=${WORKFLOW_API_BASE:-"http://app:8000/api/v1"}
WORKFLOW_ID=${WORKFLOW_ID}
STEP_NAME=${STEP_NAME}

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

# Check if required environment variables are set
check_environment() {
    if [ -z "$WORKFLOW_ID" ]; then
        log_error "WORKFLOW_ID environment variable is required"
        return 1
    fi
    
    if [ -z "$STEP_NAME" ]; then
        log_error "STEP_NAME environment variable is required"
        return 1
    fi
    
    return 0
}

# Make HTTP request with retry logic
make_request() {
    local method=$1
    local endpoint=$2
    local data=$3
    local max_retries=${4:-3}
    local retry_delay=${5:-1}
    
    local url="${WORKFLOW_API_BASE}${endpoint}"
    local attempt=1
    
    while [ $attempt -le $max_retries ]; do
        local response
        local http_code
        
        if [ -n "$data" ]; then
            response=$(curl -s -w "\n%{http_code}" -X "$method" \
                -H "Content-Type: application/json" \
                -d "$data" \
                "$url" 2>/dev/null)
        else
            response=$(curl -s -w "\n%{http_code}" -X "$method" "$url" 2>/dev/null)
        fi
        
        http_code=$(echo "$response" | tail -n1)
        response_body=$(echo "$response" | head -n -1)
        
        if [ "$http_code" -ge 200 ] && [ "$http_code" -lt 300 ]; then
            echo "$response_body"
            return 0
        else
            log_warn "HTTP request failed (attempt $attempt/$max_retries): HTTP $http_code"
            if [ $attempt -eq $max_retries ]; then
                log_error "Failed to make request after $max_retries attempts"
                return 1
            fi
            
            # Exponential backoff
            sleep $((retry_delay * (2 ** (attempt - 1))))
            attempt=$((attempt + 1))
        fi
    done
    
    return 1
}

# Update step status
update_step_status() {
    local status=$1
    local message=$2
    local output_data=$3
    local error_details=$4
    
    if ! check_environment; then
        return 1
    fi
    
    local data="{\"status\": \"$status\""
    
    if [ -n "$message" ]; then
        data="${data}, \"message\": \"$message\""
    fi
    
    if [ -n "$output_data" ]; then
        data="${data}, \"output_data\": $output_data"
    else
        data="${data}, \"output_data\": {}"
    fi
    
    if [ -n "$error_details" ]; then
        data="${data}, \"error_details\": $error_details"
    else
        data="${data}, \"error_details\": {}"
    fi
    
    data="${data}}"
    
    if make_request "PUT" "/workflows/${WORKFLOW_ID}/steps/${STEP_NAME}" "$data"; then
        log_success "Updated step $STEP_NAME status to $status"
        return 0
    else
        log_error "Failed to update step status"
        return 1
    fi
}

# Log workflow event
log_workflow_event() {
    local level=$1
    local message=$2
    local metadata=$3
    
    if ! check_environment; then
        return 1
    fi
    
    local data="{\"step_name\": \"$STEP_NAME\", \"log_level\": \"$level\", \"message\": \"$message\""
    
    if [ -n "$metadata" ]; then
        data="${data}, \"metadata\": $metadata"
    else
        data="${data}, \"metadata\": {}"
    fi
    
    data="${data}}"
    
    if make_request "POST" "/workflows/${WORKFLOW_ID}/logs" "$data"; then
        log_info "Logged $level event: $message"
        return 0
    else
        log_error "Failed to log event"
        return 1
    fi
}

# Convenience functions for common operations
start_step() {
    local message=$1
    update_step_status "running" "${message:-Step $STEP_NAME started}"
}

complete_step() {
    local message=$1
    local output_data=$2
    update_step_status "completed" "${message:-Step $STEP_NAME completed}" "$output_data"
}

fail_step() {
    local error_message=$1
    local error_details=$2
    update_step_status "failed" "$error_message" "" "$error_details"
}

skip_step() {
    local reason=$1
    update_step_status "skipped" "${reason:-Step $STEP_NAME skipped}"
}

log_progress() {
    local message=$1
    local metadata=$2
    log_workflow_event "info" "$message" "$metadata"
}

log_warning() {
    local message=$1
    local metadata=$2
    log_workflow_event "warn" "$message" "$metadata"
}

log_error() {
    local message=$1
    local metadata=$2
    log_workflow_event "error" "$message" "$metadata"
}

log_debug() {
    local message=$1
    local metadata=$2
    log_workflow_event "debug" "$message" "$metadata"
}

# Get workflow status
get_workflow_status() {
    if ! check_environment; then
        return 1
    fi
    
    make_request "GET" "/workflows/${WORKFLOW_ID}"
}

# Get workflow progress
get_workflow_progress() {
    if ! check_environment; then
        return 1
    fi
    
    make_request "GET" "/workflows/${WORKFLOW_ID}/progress"
}

# Example usage function
show_usage() {
    echo "Workflow API Shell Script"
    echo "Usage: source workflow-api.sh"
    echo ""
    echo "Environment Variables:"
    echo "  WORKFLOW_ID    - ID of the workflow to track"
    echo "  STEP_NAME      - Name of the step being executed"
    echo "  WORKFLOW_API_BASE - Base URL for the workflow API (default: http://app:8000/api/v1)"
    echo ""
    echo "Available Functions:"
    echo "  start_step [message]                    - Mark step as started"
    echo "  complete_step [message] [output_data]   - Mark step as completed"
    echo "  fail_step <error_message> [error_details] - Mark step as failed"
    echo "  skip_step [reason]                      - Mark step as skipped"
    echo "  log_progress <message> [metadata]       - Log progress information"
    echo "  log_warning <message> [metadata]        - Log warning"
    echo "  log_error <message> [metadata]          - Log error"
    echo "  log_debug <message> [metadata]          - Log debug information"
    echo "  get_workflow_status                     - Get workflow status"
    echo "  get_workflow_progress                   - Get workflow progress"
    echo ""
    echo "Example:"
    echo "  source workflow-api.sh"
    echo "  start_step 'Processing file'"
    echo "  log_progress 'File processed successfully'"
    echo "  complete_step 'File processing completed' '{\"files_processed\": 1}'"
}

# If script is run directly, show usage
if [ "${BASH_SOURCE[0]}" == "${0}" ]; then
    show_usage
fi
