/**
 * PGx FHIR Export JavaScript
 * Handles exporting pharmacogenomic reports to FHIR servers
 */

// Wait for document to be fully loaded
document.addEventListener('DOMContentLoaded', function() {
    // Initialize FHIR export
    initFhirExport();
});

/**
 * Initialize FHIR export functionality
 */
function initFhirExport() {
    const exportBtn = document.getElementById('fhirExportBtn');
    if (exportBtn) {
        exportBtn.addEventListener('click', exportToFhir);
    }
}

/**
 * Export report to FHIR server
 */
function exportToFhir() {
    // Get report ID from the page data
    const pgxDataElement = document.getElementById('pgxData');
    const reportId = pgxDataElement ? pgxDataElement.dataset.reportId : '';
    
    // Show loading state
    const exportBtn = document.getElementById('fhirExportBtn');
    const exportResult = document.getElementById('exportResult');
    
    exportBtn.disabled = true;
    exportBtn.textContent = 'Exporting...';
    
    if (exportResult) {
        exportResult.style.display = 'block';
        exportResult.innerHTML = '<div class="alert alert-info">Processing your request...</div>';
    }
    
    // Gather form data
    const fhirServerUrl = document.getElementById('fhirServerUrl').value;
    const patientFamilyName = document.getElementById('patientFamilyName').value;
    const patientGivenName = document.getElementById('patientGivenName').value;
    const patientGender = document.getElementById('patientGender').value;
    const patientBirthDate = document.getElementById('patientBirthDate').value;
    
    // Prepare patient info
    const patientInfo = {};
    
    if (patientFamilyName || patientGivenName) {
        patientInfo.name = {};
        if (patientFamilyName) patientInfo.name.family = patientFamilyName;
        if (patientGivenName) patientInfo.name.given = [patientGivenName];
    }
    
    if (patientGender) {
        patientInfo.gender = patientGender;
    }
    
    if (patientBirthDate) {
        patientInfo.birthDate = patientBirthDate;
    }
    
    // Call the API to export to FHIR
    fetch('/reports/' + reportId + '/export-to-fhir', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({
            target_fhir_url: fhirServerUrl || null,
            patient_info: patientInfo
        })
    })
    .then(function(response) {
        if (!response.ok) {
            throw new Error('Export failed: ' + response.statusText);
        }
        return response.json();
    })
    .then(function(data) {
        // Display success result
        if (exportResult) {
            exportResult.style.display = 'block';
            var successHtml = '<div class="alert alert-success">' +
                '<h5>Export Successful!</h5>' +
                '<p>Your pharmacogenomic report has been successfully exported to the EHR system.</p>' +
                '<ul>' +
                '<li>Patient ID: ' + data.patient_id + '</li>' +
                '<li>Report ID: ' + data.report_id + '</li>' +
                '<li>Resources Created: ' + data.resources_created + '</li>' +
                '<li>FHIR Server: ' + data.fhir_server + '</li>' +
                '</ul>' +
                '</div>';
            exportResult.innerHTML = successHtml;
        }
    })
    .catch(function(error) {
        // Display error
        console.error('Export error:', error);
        if (exportResult) {
            exportResult.style.display = 'block';
            var errorHtml = '<div class="alert alert-danger">' +
                '<h5>Export Failed</h5>' +
                '<p>' + error.message + '</p>' +
                '<p>Please check the FHIR server URL and try again, or contact support for assistance.</p>' +
                '</div>';
            exportResult.innerHTML = errorHtml;
        }
    })
    .finally(function() {
        // Reset button state
        exportBtn.disabled = false;
        exportBtn.textContent = 'Export to EHR';
    });
} 