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
    const canvas = document.getElementById('genePhenotypeChart');
    if (!canvas || typeof Chart === 'undefined') {
        return; // Guard: no canvas or Chart.js not loaded
    }
    const ctx = canvas.getContext('2d');
    const pgxData = getPgxData();
    if (!pgxData || !Array.isArray(pgxData.diplotypes) || pgxData.diplotypes.length === 0) {
        return; // Guard: no data
    }

    // Extract gene data
    const genes = [];
    const phenotypes = [];
    const colors = [];

    // Process diplotypes
    pgxData.diplotypes.forEach(d => {
        const gene = d.gene || 'Unknown';
        const phenotype = d.phenotype || 'Unknown';
        genes.push(gene);
        phenotypes.push(phenotype);

        const p = String(phenotype);
        if (p.includes('Normal')) {
            colors.push('#2ecc71'); // success-color
        } else if (p.includes('Poor')) {
            colors.push('#e74c3c'); // danger-color
        } else if (p.includes('Intermediate')) {
            colors.push('#f39c12'); // warning-color
        } else if (p.includes('Rapid') || p.includes('Ultrarapid')) {
            colors.push('#3498db'); // info-color
        } else {
            colors.push('#95a5a6'); // gray
        }
    });

    // Create the chart (Chart.js v3+)
    new Chart(ctx, {
        type: 'bar',
        data: {
            labels: genes,
            datasets: [{
                label: 'Phenotype',
                data: genes.map(() => 1), // constant height; color conveys phenotype
                backgroundColor: colors
            }]
        },
        options: {
            indexAxis: 'y', // horizontal bars
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                title: { display: true, text: 'Gene Phenotypes' },
                tooltip: {
                    callbacks: {
                        label: function(context) {
                            return phenotypes[context.dataIndex];
                        }
                    }
                }
            },
            scales: {
                x: { display: false, beginAtZero: true },
                y: { grid: { display: false } }
            }
        }
    });
}

/**
 * Recommendations by what they change. This sorted on words in the text: "Use 60%
 * of the standard dose" contains "standard" and counted as standard dosing. The
 * guideline's own prescribing tags say it (see PRESCRIBING_CHANGE_COLORS).
 */
function createRecommendationChart() {
    const canvas = document.getElementById('recommendationChart');
    if (!canvas || typeof Chart === 'undefined') return;
    const pgxData = getPgxData();

    const counts = {};
    Object.keys(PRESCRIBING_CHANGE_COLORS).forEach(c => { counts[c] = 0; });
    counts['no change'] = 0;
    pgxData.recommendations.forEach(rec => {
        const first = (rec.prescribingChanges || []).find(c => c in PRESCRIBING_CHANGE_COLORS);
        counts[first || 'no change'] += 1;
    });

    new Chart(canvas.getContext('2d'), {
        type: 'doughnut',
        data: {
            labels: Object.keys(counts),
            datasets: [{
                data: Object.values(counts),
                backgroundColor: [...Object.values(PRESCRIBING_CHANGE_COLORS), '#2ecc71']
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { position: 'right' } }
        }
    });
}

/**
 * What a recommendation changes, most decisive first -- the order and labels of
 * app/reports/evidence.py's PRESCRIBING_CHANGES, which reads them off PharmCAT's
 * annotations. A recommendation with none keeps the standard dose and drug.
 */
const PRESCRIBING_CHANGE_COLORS = {
    'alternative drug': '#e74c3c',
    'dose change': '#f39c12',
    'other guidance': '#3498db'
};
const NETWORK_FONT_PX = 12; // .chart-container svg text forces 12px

/**
 * The drug-gene network: only the drugs whose guidance changes prescribing for
 * this genotype, each tied to the gene(s) that guidance rests on. Genes are hubs.
 */
function actionableNetwork(recommendations, diplotypes) {
    const order = Object.keys(PRESCRIBING_CHANGE_COLORS);
    const phenotypeOf = new Map(diplotypes.map(d => [d.gene, d.phenotype || '']));
    const pairs = new Map();
    recommendations.forEach(rec => {
        const changes = (rec.prescribingChanges || []).filter(c => order.includes(c));
        if (!changes.length || !rec.drug) return;
        // The genes the change comes from: every gene the recommendation rests on
        // (CPIC sertraline: CYP2B6 and CYP2C19) less a risk screen's all-clear
        // (phenytoin's HLA-B *15:02 negative; app/reports/evidence.py).
        const from = rec.changeGenes && rec.changeGenes.length ? rec.changeGenes
            : rec.genes && rec.genes.length ? rec.genes : [rec.gene];
        const genes = from.filter(g => g && g !== 'Unknown');
        genes.forEach(gene => {
            const key = gene + '\u0000' + rec.drug;
            if (!pairs.has(key)) pairs.set(key, { gene: gene, drug: rec.drug, changes: new Set() });
            changes.forEach(c => pairs.get(key).changes.add(c));
        });
    });

    const genes = new Map();
    const drugs = new Map();
    const links = [];
    pairs.forEach(p => {
        if (!genes.has(p.gene)) {
            genes.set(p.gene, { id: 'gene:' + p.gene, label: p.gene, type: 'gene',
                                phenotype: phenotypeOf.get(p.gene) || '', drugs: [] });
        }
        if (!drugs.has(p.drug)) {
            drugs.set(p.drug, { id: 'drug:' + p.drug, label: p.drug, type: 'drug', genes: [] });
        }
        genes.get(p.gene).drugs.push(p.drug);
        drugs.get(p.drug).genes.push(p.gene);
        const changes = order.filter(c => p.changes.has(c));
        links.push({ source: 'gene:' + p.gene, target: 'drug:' + p.drug, gene: p.gene,
                     drug: p.drug, change: changes[0], changes: changes });
    });
    return { genes: [...genes.values()], drugs: [...drugs.values()], links: links };
}

function geneColor(phenotype) {
    const p = String(phenotype || '');
    if (p.includes('Poor')) return '#e74c3c';
    if (p.includes('Intermediate')) return '#f39c12';
    if (p.includes('Rapid') || p.includes('Ultrarapid')) return '#3498db';
    if (p.includes('Normal')) return '#2ecc71';
    return '#95a5a6';
}

/**
 * Lay the network out and size it to its card. The old version ran a live force
 * layout of every gene and drug in a fixed 300 px box, drawn while its tab was
 * hidden (0 px wide): at whole-genome scale 213 of 217 nodes ended off-canvas.
 * Here a gene's own drugs sit on a ring around it, labels pointing outward;
 * drugs led to by the same genes are listed together between those hubs. The
 * finished layout sets the SVG's viewBox, so the whole picture fits the card at
 * any width, and nothing in it depends on the tab being visible when drawn.
 */
function createNetworkVisualization() {
    const container = document.getElementById('networkVisualization');
    if (!container || typeof d3 === 'undefined') return;
    const pgxData = getPgxData();
    const net = actionableNetwork(pgxData.recommendations, pgxData.diplotypes);
    const allDrugs = new Set(pgxData.recommendations.map(r => r.drug).filter(Boolean));

    const note = document.createElement('p');
    note.className = 'network-note';
    container.appendChild(note);
    if (!net.links.length) {
        note.textContent = 'No guideline in this report recommends a different drug, ' +
            'a dose change or other prescribing guidance for this genotype.';
        return;
    }
    note.textContent = `${net.drugs.length} of the ${allDrugs.size} drugs with guidance in ` +
        `this report have a recommendation that changes prescribing for this genotype ` +
        `(a different drug, a dose change, or other guidance such as monitoring), ` +
        `through ${net.genes.length} gene${net.genes.length === 1 ? '' : 's'}. ` +
        `Select a drug to open its recommendations; Ctrl+scroll or pinch zooms, drag ` +
        `pans, double-click resets.`;

    const measure = document.createElement('canvas').getContext('2d');
    measure.font = `${NETWORK_FONT_PX}px ${getComputedStyle(container).fontFamily || 'sans-serif'}`;
    const textWidth = s => measure.measureText(s).width;

    const GENE_R = 18;
    const DRUG_R = 4;
    const SLOT = NETWORK_FONT_PX + 3; // arc length per drug on a ring
    const LABEL_GAP = 6;
    const RADIAL_FROM = 10; // rings this full turn their labels outward

    // Each hub's ring holds the drugs only it leads to, far enough out to clear
    // the gene's name and phenotype printed under it.
    net.genes.forEach(g => {
        g.ring = g.drugs.filter(name => net.drugs.find(d => d.label === name).genes.length === 1);
        const labelHalf = Math.max(textWidth(g.label), textWidth(g.phenotype)) / 2;
        const clearance = Math.hypot(labelHalf, GENE_R + 2 * NETWORK_FONT_PX + 6) + DRUG_R + LABEL_GAP;
        g.ringRadius = Math.max(clearance, (g.ring.length * SLOT) / (2 * Math.PI));
        const longest = Math.max(0, ...g.ring.map(textWidth));
        g.reach = g.ringRadius + (g.ring.length ? DRUG_R + LABEL_GAP + longest : 0);
    });
    // Drugs led to by several genes are listed together, one list per set of
    // genes (the volatile anaesthetics all rest on CACNA1S and RYR1), placed
    // between those genes' hubs.
    const groups = new Map();
    net.drugs.filter(d => d.genes.length > 1).forEach(d => {
        const key = [...d.genes].sort().join('+');
        if (!groups.has(key)) groups.set(key, { id: 'group:' + key, type: 'group', genes: [...d.genes].sort(), drugs: [] });
        groups.get(key).drugs.push(d);
    });
    groups.forEach(grp => {
        grp.drugs.sort((a, b) => a.label.localeCompare(b.label));
        grp.w = DRUG_R * 2 + LABEL_GAP + Math.max(...grp.drugs.map(d => textWidth(d.label)));
        grp.h = grp.drugs.length * SLOT;
        grp.reach = Math.hypot(grp.w / 2, grp.h / 2) + 4;
    });

    // Hubs and lists settle under a force layout, run to completion here rather
    // than animated; each drug's place is set from them afterwards.
    const nodes = [...net.genes, ...groups.values()];
    const groupLinks = [];
    groups.forEach(grp => grp.genes.forEach(gene => groupLinks.push({ source: 'gene:' + gene, target: grp.id })));
    const simulation = d3.forceSimulation(nodes)
        .force('link', d3.forceLink(groupLinks).id(d => d.id)
            .distance(l => l.source.reach + l.target.reach + 10).strength(0.5))
        .force('charge', d3.forceManyBody().strength(-40))
        .force('collide', d3.forceCollide(d => d.reach + 8).iterations(4))
        .force('x', d3.forceX(0).strength(0.12))
        .force('y', d3.forceY(0).strength(0.18)) // a card is wider than tall
        .stop();
    for (let i = 0; i < 500; i++) simulation.tick();

    // A list's dots face its genes, its labels the other way.
    const hubX = new Map(net.genes.map(g => [g.label, g.x]));
    groups.forEach(grp => {
        const towards = grp.genes.reduce((sum, g) => sum + hubX.get(g), 0) / grp.genes.length;
        const right = towards > grp.x;
        grp.drugs.forEach((d, i) => {
            d.labelLeft = right;
            d.x = right ? grp.x + grp.w / 2 - DRUG_R : grp.x - grp.w / 2 + DRUG_R;
            d.y = grp.y - grp.h / 2 + SLOT * (i + 0.5);
        });
    });
    const position = new Map(net.genes.map(n => [n.id, n]));
    groups.forEach(grp => grp.drugs.forEach(d => position.set(d.id, d)));
    net.genes.forEach(g => {
        g.ring.forEach((name, i) => {
            const d = net.drugs.find(x => x.label === name);
            d.angle = (2 * Math.PI * i) / g.ring.length - Math.PI / 2;
            d.x = g.x + g.ringRadius * Math.cos(d.angle);
            d.y = g.y + g.ringRadius * Math.sin(d.angle);
            position.set(d.id, d);
        });
    });

    const radial = d => d.angle !== undefined &&
        net.genes.find(g => g.label === d.genes[0]).ring.length >= RADIAL_FROM;

    // Bounds of everything drawn, each label at its own length.
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    const grow = (x, y) => {
        x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y);
    };
    const half = NETWORK_FONT_PX / 2 + 1;
    net.genes.forEach(g => {
        const w = Math.max(textWidth(g.label), textWidth(g.phenotype)) / 2;
        grow(g.x - Math.max(GENE_R, w), g.y - GENE_R);
        grow(g.x + Math.max(GENE_R, w), g.y + GENE_R + NETWORK_FONT_PX * 2 + 6);
    });
    net.drugs.forEach(d => {
        const w = textWidth(d.label), r = DRUG_R + LABEL_GAP;
        grow(d.x - DRUG_R, d.y - DRUG_R); grow(d.x + DRUG_R, d.y + DRUG_R);
        if (d.angle === undefined) {
            const end = d.labelLeft ? d.x - r - w : d.x + r + w;
            grow(end, d.y - half); grow(end, d.y + half);
            return;
        }
        const c = Math.cos(d.angle), sn = Math.sin(d.angle);
        if (radial(d)) {
            [-half, half].forEach(o => {
                grow(d.x + (r + w) * c - o * sn, d.y + (r + w) * sn + o * c);
                grow(d.x + r * c - o * sn, d.y + r * sn + o * c);
            });
            return;
        }
        // Level labels, as placed below.
        const dx = Math.abs(c) > 0.3 ? Math.sign(c) * r : 0;
        const dy = Math.abs(c) > 0.3 ? 0 : (sn < 0 ? -1 : 1) * (NETWORK_FONT_PX * 0.5 + r);
        const left = Math.abs(c) > 0.3 ? (c > 0 ? 0 : -w) : -w / 2;
        grow(d.x + dx + left, d.y + dy - half); grow(d.x + dx + left + w, d.y + dy + half);
    });
    const pad = 10;
    const vb = [x0 - pad, y0 - pad, x1 - x0 + 2 * pad, y1 - y0 + 2 * pad];

    const svg = d3.select(container).append('svg')
        .attr('viewBox', vb.join(' '))
        .attr('role', 'img')
        .attr('aria-label', 'Genes and the drugs whose guidance they change')
        .style('width', '100%')
        .style('max-width', `${Math.ceil(vb[2])}px`)
        .style('height', 'auto')
        .style('display', 'block')
        .style('margin', '0 auto');

    const view = svg.append('g');
    // Ctrl/Cmd + wheel zooms and a plain wheel scrolls the page; on a touch screen
    // one finger scrolls the page and two pinch and pan. Double-click resets through
    // this same behaviour: a fresh d3.zoom() has no 'zoom' listener to move the view.
    const zoom = d3.zoom()
        .scaleExtent([1, 6])
        .filter(event => event.type === 'wheel' ? event.ctrlKey || event.metaKey
            : event.type.startsWith('touch') ? event.touches.length > 1
            : !event.button)
        .on('zoom', event => view.attr('transform', event.transform));
    svg.call(zoom)
        .on('dblclick.zoom', () => svg.transition().call(zoom.transform, d3.zoomIdentity));

    const link = view.append('g').selectAll('line')
        .data(net.links).enter().append('line')
        .attr('x1', l => position.get(l.source).x).attr('y1', l => position.get(l.source).y)
        .attr('x2', l => position.get(l.target).x).attr('y2', l => position.get(l.target).y)
        .attr('stroke', l => PRESCRIBING_CHANGE_COLORS[l.change])
        .attr('stroke-width', 1.5)
        .attr('stroke-opacity', 0.7);

    // A white edge under every label, so a line passing behind it cannot cross it out.
    const outline = sel => sel.style('paint-order', 'stroke').style('stroke', '#fff')
        .style('stroke-width', '4px').style('stroke-linejoin', 'round');
    const drug = view.append('g').selectAll('a')
        .data(net.drugs).enter().append('a')
        .attr('href', d => '#drug-rec-' + d.label.toLowerCase().split(' ').join('-'))
        .on('click', event => { if (typeof switchToRecsTab === 'function') switchToRecsTab(event); });
    drug.append('title').text(d => {
        const lines = net.links.filter(l => l.drug === d.label)
            .map(l => `${l.gene}: ${l.changes.join(', ')}`);
        return `${d.label}\n${lines.join('\n')}`;
    });
    drug.append('circle')
        .attr('cx', d => d.x).attr('cy', d => d.y).attr('r', DRUG_R)
        .attr('fill', '#2c3e50');
    drug.append('text')
        .text(d => d.label)
        .call(outline)
        .attr('dominant-baseline', 'middle')
        .attr('text-anchor', d => {
            if (d.angle === undefined) return d.labelLeft ? 'end' : 'start';
            const c = Math.cos(d.angle);
            if (radial(d)) return c < 0 ? 'end' : 'start';
            return c > 0.3 ? 'start' : c < -0.3 ? 'end' : 'middle';
        })
        .attr('transform', d => {
            const r = DRUG_R + LABEL_GAP;
            if (d.angle === undefined) return `translate(${d.labelLeft ? d.x - r : d.x + r},${d.y})`;
            const c = Math.cos(d.angle), sn = Math.sin(d.angle);
            if (!radial(d)) {
                // Few drugs: level labels, set off on the side facing away from the gene.
                const dy = Math.abs(c) > 0.3 ? 0 : (sn < 0 ? -1 : 1) * (NETWORK_FONT_PX * 0.5 + r);
                const dx = Math.abs(c) > 0.3 ? Math.sign(c) * r : 0;
                return `translate(${d.x + dx},${d.y + dy})`;
            }
            // Radial labels, turned so none reads upside down.
            const deg = (d.angle * 180) / Math.PI + (c < 0 ? 180 : 0);
            return `translate(${d.x + r * c},${d.y + r * sn}) rotate(${deg})`;
        });

    const gene = view.append('g').selectAll('g')
        .data(net.genes).enter().append('g')
        .attr('transform', g => `translate(${g.x},${g.y})`)
        .style('cursor', 'default');
    gene.append('title').text(g => `${g.label}${g.phenotype ? ' — ' + g.phenotype : ''}\n` +
        `${g.drugs.length} drug${g.drugs.length === 1 ? '' : 's'} with changed guidance`);
    gene.append('circle').attr('r', GENE_R)
        .attr('fill', g => geneColor(g.phenotype))
        .attr('stroke', '#fff').attr('stroke-width', 2);
    gene.append('text').text(g => g.label).call(outline)
        .attr('text-anchor', 'middle').attr('y', GENE_R + NETWORK_FONT_PX)
        .style('font-weight', 'bold');
    gene.append('text').text(g => g.phenotype).call(outline)
        .attr('text-anchor', 'middle').attr('y', GENE_R + NETWORK_FONT_PX * 2 + 2);

    // Hovering a gene picks out its drugs.
    gene.on('mouseenter', (event, g) => {
        link.attr('stroke-opacity', l => (l.gene === g.label ? 0.9 : 0.08));
        drug.style('opacity', d => (d.genes.includes(g.label) ? 1 : 0.2));
    }).on('mouseleave', () => {
        link.attr('stroke-opacity', 0.7);
        drug.style('opacity', 1);
    });

    const legend = document.createElement('p');
    legend.className = 'network-legend';
    legend.innerHTML = Object.entries(PRESCRIBING_CHANGE_COLORS)
        .map(([label, color]) => `<span style="color:${color}">&#9644;</span> ${label}`)
        .join(' &nbsp; ') +
        ' &nbsp; (a line takes the colour of the most decisive change any guideline makes for that pair)';
    container.appendChild(legend);
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
            literatureReferences: JSON.parse(element.dataset.literatureReferences || '[]'),
            prescribingChanges: JSON.parse(element.dataset.prescribingChanges || '[]'),
            changeGenes: JSON.parse(element.dataset.changeGenes || '[]'),
            genes: JSON.parse(element.dataset.recommendationGenes || '[]')
        });
    });
    
    return pgxData;
} 