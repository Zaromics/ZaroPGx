/**
 * PGx Report JavaScript
 * Provides interactive functionality for pharmacogenomic reports
 */

// Wait for the document to be fully loaded
document.addEventListener('DOMContentLoaded', function() {
    // Extract data from the data attributes
    const pgxDataElement = document.getElementById('pgxData');
    
    // Initialize the pgxData object
    const pgxData = {
        patientId: pgxDataElement.dataset.patientId,
        reportId: pgxDataElement.dataset.reportId,
        reportDate: pgxDataElement.dataset.reportDate,
        organizedRecommendations: JSON.parse(pgxDataElement.dataset.organizedRecommendations || '{}'),
        diplotypes: [],
        recommendations: []
    };
    
    // Extract diplotypes data
    document.querySelectorAll('.diplotype-data').forEach(element => {
        pgxData.diplotypes.push({
            gene: element.dataset.gene,
            diplotype: element.dataset.diplotype,
            phenotype: element.dataset.phenotype,
            activityScore: element.dataset.activityScore
        });
    });
    
    // Extract recommendations data
    document.querySelectorAll('.recommendation-data').forEach(element => {
        pgxData.recommendations.push({
            drug: element.dataset.drug,
            gene: element.dataset.gene,
            recommendation: element.dataset.recommendation,
            classification: element.dataset.classification,
            literatureReferences: JSON.parse(element.dataset.literatureReferences || '[]')
        });
    });
    
    // Initialize the tabs, charts, and visualizations
    initTabSystem();
    createGenePhenotypeChart();
    createRecommendationChart();
    createNetworkVisualization();
    createGeneInfoSection();
});

/**
 * Tab functionality
 */
function initTabSystem() {
    // Add event listeners to tabs
    document.querySelectorAll('.tab').forEach(tab => {
        tab.addEventListener('click', function() {
            const tabId = this.getAttribute('onclick').match(/'([^']+)'/)[1];
            showTab(tabId);
        });
    });
}

function showTab(tabId) {
    // Hide all tab contents
    document.querySelectorAll('.tab-content').forEach(content => {
        content.classList.remove('active');
    });
    
    // Deactivate all tabs
    document.querySelectorAll('.tab').forEach(tab => {
        tab.classList.remove('active');
    });
    
    // Activate the selected tab and content
    document.getElementById(tabId).classList.add('active');
    document.querySelector(`.tab[onclick="showTab('${tabId}')"]`).classList.add('active');
}

/**
 * Create gene-phenotype chart
 */
function createGenePhenotypeChart() {
    const ctx = document.getElementById('genePhenotypeChart').getContext('2d');
    const pgxData = getPgxData();
    
    // Extract gene data
    const genes = [];
    const phenotypes = [];
    const colors = [];
    
    // Process diplotypes
    pgxData.diplotypes.forEach(d => {
        genes.push(d.gene);
        phenotypes.push(d.phenotype);
        
        if (d.phenotype.includes('Normal')) {
            colors.push('#2ecc71'); // success-color
        } else if (d.phenotype.includes('Poor')) {
            colors.push('#e74c3c'); // danger-color
        } else if (d.phenotype.includes('Intermediate')) {
            colors.push('#f39c12'); // warning-color
        } else if (d.phenotype.includes('Rapid') || d.phenotype.includes('Ultrarapid')) {
            colors.push('#3498db'); // info-color
        } else {
            colors.push('#95a5a6'); // gray
        }
    });
    
    // Create the chart
    new Chart(ctx, {
        type: 'horizontalBar',
        data: {
            labels: genes,
            datasets: [{
                label: 'Phenotype',
                data: genes.map(() => 1), // Just to show the bars
                backgroundColor: colors
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            legend: {
                display: false
            },
            title: {
                display: true,
                text: 'Gene Phenotypes'
            },
            scales: {
                xAxes: [{
                    display: false,
                    ticks: {
                        beginAtZero: true
                    }
                }],
                yAxes: [{
                    gridLines: {
                        display: false
                    }
                }]
            },
            tooltips: {
                callbacks: {
                    label: function(tooltipItem, data) {
                        return phenotypes[tooltipItem.index];
                    }
                }
            }
        }
    });
}

/**
 * Create recommendation chart
 */
function createRecommendationChart() {
    const ctx = document.getElementById('recommendationChart').getContext('2d');
    const pgxData = getPgxData();
    
    // Count recommendations by type
    const recommendationTypes = {
        'Standard/Normal': 0,
        'Consider Alternative': 0,
        'Avoid': 0,
        'Other': 0
    };
    
    // Process recommendations
    pgxData.recommendations.forEach(rec => {
        const r = rec.recommendation.toLowerCase();
        if (r.includes('standard') || r.includes('normal')) {
            recommendationTypes['Standard/Normal']++;
        } else if (r.includes('avoid')) {
            recommendationTypes['Avoid']++;
        } else if (r.includes('consider') || r.includes('alternative')) {
            recommendationTypes['Consider Alternative']++;
        } else {
            recommendationTypes['Other']++;
        }
    });
    
    // Create the chart
    new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: Object.keys(recommendationTypes),
            datasets: [{
                data: Object.values(recommendationTypes),
                backgroundColor: [
                    '#2ecc71', // success-color
                    '#f39c12', // warning-color
                    '#e74c3c', // danger-color
                    '#3498db'  // info-color
                ]
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            title: {
                display: true,
                text: 'Drug Recommendations by Type'
            },
            legend: {
                position: 'right'
            }
        }
    });
}

/**
 * Create network visualization
 */
function createNetworkVisualization() {
    const container = document.getElementById('networkVisualization');
    const pgxData = getPgxData();
    
    // Set up SVG
    const width = container.clientWidth;
    const height = 300;
    
    const svg = d3.select('#networkVisualization')
        .append('svg')
        .attr('width', width)
        .attr('height', height);
    
    // Create nodes for genes and drugs
    const nodes = [];
    const links = [];
    
    // Add gene nodes
    pgxData.diplotypes.forEach(d => {
        nodes.push({
            id: d.gene,
            type: "gene",
            phenotype: d.phenotype
        });
    });
    
    // Add drug nodes and links
    pgxData.recommendations.forEach(rec => {
        // Check if the drug is already in nodes
        if (!nodes.some(node => node.id === rec.drug)) {
            nodes.push({
                id: rec.drug,
                type: "drug"
            });
        }
        
        // Add the link
        links.push({
            source: rec.gene,
            target: rec.drug,
            recommendation: rec.recommendation
        });
    });
    
    // Create the force simulation
    const simulation = d3.forceSimulation(nodes)
        .force('link', d3.forceLink(links).id(d => d.id).distance(100))
        .force('charge', d3.forceManyBody().strength(-400))
        .force('center', d3.forceCenter(width / 2, height / 2));
    
    // Create links
    const link = svg.append('g')
        .selectAll('line')
        .data(links)
        .enter()
        .append('line')
        .attr('stroke', d => {
            if (d.recommendation.toLowerCase().includes('avoid')) {
                return '#e74c3c'; // danger-color
            } else if (d.recommendation.toLowerCase().includes('consider') || 
                      d.recommendation.toLowerCase().includes('alternative')) {
                return '#f39c12'; // warning-color
            } else if (d.recommendation.toLowerCase().includes('standard') || 
                      d.recommendation.toLowerCase().includes('normal')) {
                return '#2ecc71'; // success-color
            } else {
                return '#3498db'; // info-color
            }
        })
        .attr('stroke-width', 2);
    
    // Create nodes
    const node = svg.append('g')
        .selectAll('circle')
        .data(nodes)
        .enter()
        .append('circle')
        .attr('r', 10)
        .attr('fill', d => {
            if (d.type === 'gene') {
                if (d.phenotype.includes('Normal')) {
                    return '#2ecc71'; // success-color
                } else if (d.phenotype.includes('Poor')) {
                    return '#e74c3c'; // danger-color
                } else if (d.phenotype.includes('Intermediate')) {
                    return '#f39c12'; // warning-color
                } else if (d.phenotype.includes('Rapid') || d.phenotype.includes('Ultrarapid')) {
                    return '#3498db'; // info-color
                } else {
                    return '#95a5a6'; // gray
                }
            } else {
                return '#2c3e50'; // primary-color
            }
        })
        .call(d3.drag()
            .on('start', dragstarted)
            .on('drag', dragged)
            .on('end', dragended));
    
    // Add node labels
    const label = svg.append('g')
        .selectAll('text')
        .data(nodes)
        .enter()
        .append('text')
        .text(d => d.id)
        .attr('font-size', 12)
        .attr('dx', 15)
        .attr('dy', 4);
    
    // Update positions
    simulation.on('tick', () => {
        link
            .attr('x1', d => d.source.x)
            .attr('y1', d => d.source.y)
            .attr('x2', d => d.target.x)
            .attr('y2', d => d.target.y);
        
        node
            .attr('cx', d => d.x)
            .attr('cy', d => d.y);
        
        label
            .attr('x', d => d.x)
            .attr('y', d => d.y);
    });
    
    // Drag functions
    function dragstarted(event, d) {
        if (!event.active) simulation.alphaTarget(0.3).restart();
        d.fx = d.x;
        d.fy = d.y;
    }
    
    function dragged(event, d) {
        d.fx = event.x;
        d.fy = event.y;
    }
    
    function dragended(event, d) {
        if (!event.active) simulation.alphaTarget(0);
        d.fx = null;
        d.fy = null;
    }
}

/**
 * Create detailed gene information section
 */
function createGeneInfoSection() {
    const geneInfoSection = document.getElementById('geneInfoSection');
    const pgxData = getPgxData();
    
    pgxData.diplotypes.forEach(d => {
        const geneInfo = document.createElement('div');
        geneInfo.className = 'section';
        
        let geneDescription = '';
        if (d.gene === 'CYP2D6') {
            geneDescription = 'Cytochrome P450 Family a Subfamily D Member 6 is responsible for metabolizing approximately 25% of clinically used drugs.';
        } else if (d.gene === 'CYP2C19') {
            geneDescription = 'Cytochrome P450 Family 2 Subfamily C Member 19 is involved in the metabolism of several important drug classes including proton pump inhibitors and antidepressants.';
        } else if (d.gene === 'SLCO1B1') {
            geneDescription = 'Solute Carrier Organic Anion Transporter Family Member 1B1 mediates the sodium-independent uptake of numerous drugs and endogenous compounds.';
        } else {
            geneDescription = 'A gene involved in drug metabolism.';
        }
        
        let clinicalImplications = '';
        if (d.phenotype.includes('Normal')) {
            clinicalImplications = `This patient has normal ${d.gene} activity, which means they should respond normally to medications metabolized by ${d.gene}.`;
        } else if (d.phenotype.includes('Poor')) {
            clinicalImplications = `This patient has reduced ${d.gene} activity, which may result in reduced metabolism of drugs processed by ${d.gene}. This could lead to increased drug concentrations and potential adverse effects at standard doses.`;
        } else if (d.phenotype.includes('Intermediate')) {
            clinicalImplications = `This patient has moderately reduced ${d.gene} activity, which may result in somewhat reduced metabolism of drugs processed by ${d.gene}. Careful monitoring may be required when using medications metabolized by ${d.gene}.`;
        } else if (d.phenotype.includes('Rapid') || d.phenotype.includes('Ultrarapid')) {
            clinicalImplications = `This patient has increased ${d.gene} activity, which may result in faster metabolism of drugs processed by ${d.gene}. This could lead to reduced drug concentrations and potential loss of efficacy at standard doses.`;
        } else {
            clinicalImplications = `The clinical implications of this ${d.gene} phenotype should be evaluated by a healthcare professional.`;
        }
        
        let relatedMedications = '';
        if (d.gene === 'CYP2D6') {
            relatedMedications = `
                <li>Codeine (pain relief)</li>
                <li>Tamoxifen (cancer treatment)</li>
                <li>Antidepressants (fluoxetine, paroxetine)</li>
                <li>Antipsychotics (haloperidol, risperidone)</li>
            `;
        } else if (d.gene === 'CYP2C19') {
            relatedMedications = `
                <li>Clopidogrel (antiplatelet)</li>
                <li>Proton pump inhibitors (omeprazole)</li>
                <li>Antidepressants (citalopram, escitalopram)</li>
                <li>Antifungals (voriconazole)</li>
            `;
        } else if (d.gene === 'SLCO1B1') {
            relatedMedications = `
                <li>Statins (simvastatin, atorvastatin)</li>
                <li>Methotrexate</li>
                <li>Repaglinide</li>
            `;
        } else {
            relatedMedications = `<li>Various medications</li>`;
        }
        
        geneInfo.innerHTML = `
            <h3>${d.gene} Information</h3>
            <p><strong>Description:</strong> ${geneDescription}</p>
            <p><strong>Diplotype:</strong> ${d.diplotype}</p>
            <p><strong>Phenotype:</strong> ${d.phenotype}</p>
            <p><strong>Activity Score:</strong> ${d.activityScore}</p>
            <h4>Clinical Implications</h4>
            <p>${clinicalImplications}</p>
            
            <h4>Related Medications</h4>
            <ul>${relatedMedications}</ul>
        `;
        
        geneInfoSection.appendChild(geneInfo);
    });
}

/**
 * Helper function to get the PGx data from the hidden element
 */
function getPgxData() {
    const pgxDataElement = document.getElementById('pgxData');
    
    const pgxData = {
        patientId: pgxDataElement.dataset.patientId,
        reportId: pgxDataElement.dataset.reportId,
        reportDate: pgxDataElement.dataset.reportDate,
        organizedRecommendations: JSON.parse(pgxDataElement.dataset.organizedRecommendations || '{}'),
        diplotypes: [],
        recommendations: []
    };
    
    // Extract diplotypes data
    document.querySelectorAll('.diplotype-data').forEach(element => {
        pgxData.diplotypes.push({
            gene: element.dataset.gene,
            diplotype: element.dataset.diplotype,
            phenotype: element.dataset.phenotype,
            activityScore: element.dataset.activityScore
        });
    });
    
    // Extract recommendations data
    document.querySelectorAll('.recommendation-data').forEach(element => {
        pgxData.recommendations.push({
            drug: element.dataset.drug,
            gene: element.dataset.gene,
            recommendation: element.dataset.recommendation,
            classification: element.dataset.classification,
            literatureReferences: JSON.parse(element.dataset.literatureReferences || '[]')
        });
    });
    
    return pgxData;
} 