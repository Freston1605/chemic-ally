function levelIndex() {
    return {
        levels: [],
        loading: true,

        async loadLevels() {
            try {
                const response = await fetch('/hplc/api/levels/');
                this.levels = await response.json();
            } catch (error) {
                console.error('Failed to load levels:', error);
            } finally {
                this.loading = false;
            }
        },

        difficultyBadge(difficulty) {
            const classes = {
                beginner: 'badge-beginner',
                intermediate: 'badge-intermediate',
                advanced: 'badge-advanced',
                expert: 'badge-advanced',
            };
            return classes[difficulty] || 'badge-beginner';
        },
    };
}

function hplcSimulator() {
    return {
        level: {},
        availableColumns: ['C18', 'C8'],
        openSections: {
            column: true,
            mobilePhase: false,
            operation: false,
            advanced: false,
        },
        params: {
            is_gradient: true,
            start_b: 5,
            end_b: 95,
            ramp_time: 20,
            ph: 3.0,
            column_chemistry: 'C18',
            column_length_mm: 150,
            column_id_mm: 4.6,
            particle_size_um: 5.0,
            flow_rate_ml_min: 1.0,
            temperature_c: 30,
            injection_volume_ul: 10,
            dwell_time_min: 1.0,
        },
        running: false,
        results: {
            chromatogram: { time: [], signal: [] },
            peaks: [],
            metrics: {
                total_run_time: 0,
                max_pressure_bar: 0,
                min_resolution: 0,
                critical_pair: null,
            },
            score: 0,
            success: false,
            overpressure: false,
            message: '',
        },
        hasResults: false,
        viewMode: 'auto',
        plotInstance: null,
        errorMessage: '',
        fieldErrors: {},

        async loadLevel(slug) {
            try {
                const response = await fetch(`/hplc/api/levels/${slug}/`);
                this.level = await response.json();
                this.availableColumns = this.level.constraints.available_columns;

                this.level.max_pressure_bar =
                    this.level.constraints.max_pressure_bar;

                if (
                    !this.availableColumns.includes(
                        this.params.column_chemistry,
                    )
                ) {
                    this.params.column_chemistry = this.availableColumns[0];
                }
            } catch (error) {
                console.error('Failed to load level:', error);
            }
        },

        resetParams() {
            this.params = {
                is_gradient: true,
                start_b: 5,
                end_b: 95,
                ramp_time: 20,
                ph: 3.0,
                column_chemistry: this.availableColumns[0],
                column_length_mm: 150,
                column_id_mm: 4.6,
                particle_size_um: 5.0,
                flow_rate_ml_min: 1.0,
                temperature_c: 30,
                injection_volume_ul: 10,
                dwell_time_min: 1.0,
            };
            this.errorMessage = '';
            this.fieldErrors = {};
        },

        toggleAccordion(section) {
            this.openSections[section] = !this.openSections[section];
        },

        updateSliderFill(event) {
            const el = event.target;
            const min = parseFloat(el.min) || 0;
            const max = parseFloat(el.max) || 100;
            const val = parseFloat(el.value);
            const pct = ((val - min) / (max - min)) * 100;
            el.style.setProperty('--fill', pct + '%');
        },

        estimatePressure() {
            const p = this.params;
            const tempK = p.temperature_c + 273.15;
            const refVisc = 0.001 * Math.exp(1800 * (1 / tempK - 1 / 298.15));
            const refVisc30 = 0.001 * Math.exp(1800 * (1 / 303.15 - 1 / 298.15));
            return 120 * (p.column_length_mm / 150) * (p.flow_rate_ml_min / 1.0)
                * Math.pow(5.0 / p.particle_size_um, 2)
                * Math.pow(4.6 / p.column_id_mm, 2)
                * (refVisc / refVisc30);
        },

        validateParams() {
            const errors = {};
            const p = this.params;

            if (p.start_b < 0 || p.start_b > 100) errors.start_b = 'Must be 0-100%';
            if (p.end_b < 0 || p.end_b > 100) errors.end_b = 'Must be 0-100%';
            if (p.end_b < p.start_b) errors.end_b = 'End B% must be >= Start B%';
            if (p.ramp_time <= 0) errors.ramp_time = 'Must be positive';
            if (p.ph < 2.0 || p.ph > 8.0) errors.ph = 'Must be 2.0-8.0';
            if (p.flow_rate_ml_min <= 0) errors.flow_rate_ml_min = 'Must be > 0';
            if (p.temperature_c < 20 || p.temperature_c > 80) errors.temperature_c = 'Must be 20-80 C';
            if (p.injection_volume_ul < 1 || p.injection_volume_ul > 100) errors.injection_volume_ul = 'Must be 1-100 uL';
            if (p.dwell_time_min < 0.1 || p.dwell_time_min > 10.0) errors.dwell_time_min = 'Must be 0.1-10.0 min';

            if (['C18', 'C8', 'C4'].includes(p.column_chemistry) && p.ph > 7.5) {
                errors.ph = 'pH ' + p.ph + ' may damage ' + p.column_chemistry + ' silica columns';
            }

            if (p.particle_size_um < 2.0) {
                const estPressure = this.estimatePressure();
                const limit = this.level.max_pressure_bar || 400;
                if (estPressure > limit) {
                    errors.pressure_warning = 'Estimated pressure ' + estPressure.toFixed(0) + ' bar exceeds ' + limit + ' bar limit';
                }
            }

            return errors;
        },

        async runSimulation() {
            this.fieldErrors = {};
            const errors = this.validateParams();
            if (Object.keys(errors).length > 0) {
                this.fieldErrors = errors;
                this.errorMessage = Object.values(errors).join('; ');
                return;
            }

            this.running = true;
            this.hasResults = false;
            this.errorMessage = '';

            const payload = {
                level_id: this.level.id,
                mobile_phase: {
                    start_b: parseFloat(this.params.start_b),
                    end_b: parseFloat(this.params.end_b),
                    ramp_time: parseFloat(this.params.ramp_time),
                    ph: parseFloat(this.params.ph),
                    buffer_concentration_mm: 10,
                    is_gradient: this.params.is_gradient,
                },
                column: {
                    chemistry: this.params.column_chemistry,
                    length_mm: parseFloat(this.params.column_length_mm),
                    id_mm: parseFloat(this.params.column_id_mm),
                    particle_size_um: parseFloat(
                        this.params.particle_size_um,
                    ),
                },
                operation: {
                    flow_rate_ml_min: parseFloat(
                        this.params.flow_rate_ml_min,
                    ),
                    temperature_c: parseFloat(this.params.temperature_c),
                    injection_volume_ul: parseFloat(
                        this.params.injection_volume_ul,
                    ),
                    dwell_time_min: parseFloat(this.params.dwell_time_min),
                },
            };

            try {
                const response = await fetch('/hplc/api/simulate/', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': this.getCookie('csrftoken'),
                    },
                    body: JSON.stringify(payload),
                });

                const data = await response.json();

                if (response.ok) {
                    this.results = data;
                    this.hasResults = true;
                    this.animateChromatogram(
                        data.chromatogram,
                        data.peaks,
                    );
                } else {
                    this.errorMessage = this.formatErrors(data.errors);
                    console.error('Simulation error:', data);
                }
            } catch (error) {
                this.errorMessage = 'Network error. Please try again.';
                console.error('Failed to run simulation:', error);
            } finally {
                this.running = false;
            }
        },

        formatErrors(errors) {
            if (!errors) return 'An unknown error occurred.';

            if (typeof errors === 'string') return errors;

            const messages = [];
            for (const [field, fieldErrors] of Object.entries(errors)) {
                if (Array.isArray(fieldErrors)) {
                    messages.push(`${field}: ${fieldErrors.join(', ')}`);
                } else if (typeof fieldErrors === 'object') {
                    for (const [subField, subErrors] of Object.entries(
                        fieldErrors,
                    )) {
                        if (Array.isArray(subErrors)) {
                            messages.push(
                                `${field}.${subField}: ${subErrors.join(', ')}`,
                            );
                        }
                    }
                }
            }

            return messages.length > 0
                ? messages.join('; ')
                : 'Validation error occurred.';
        },

        animateChromatogram(chromatogram, peaks) {
            if (!chromatogram || !chromatogram.time || !chromatogram.signal) {
                this.errorMessage = 'No chromatogram data received.';
                return;
            }

            const timeData = chromatogram.time;
            const signalData = chromatogram.signal.map(v => isFinite(v) ? v : 0);
            const totalPoints = timeData.length;

            if (totalPoints === 0) {
                this.errorMessage = 'Empty chromatogram data.';
                return;
            }

            const traceColor = '#14b8a6';
            const gridColor = '#30363d';
            const textColor = '#8b949e';
            const bgColor = '#0d1117';

            const trace = {
                x: [],
                y: [],
                type: 'scatter',
                mode: 'lines',
                line: { color: traceColor, width: 1.5 },
                name: 'Signal',
            };

            const peakAnnotations = peaks.map((peak) => ({
                x: peak.retention_time,
                y: peak.height,
                text: peak.analyte,
                showarrow: true,
                arrowhead: 2,
                arrowsize: 1,
                arrowwidth: 1,
                arrowcolor: textColor,
                font: { size: 10, color: textColor },
                ay: -20,
            }));

            const layout = {
                margin: { t: 30, r: 20, b: 40, l: 60 },
                xaxis: {
                    title: 'Time (min)',
                    gridcolor: gridColor,
                    zerolinecolor: gridColor,
                    color: textColor,
                },
                yaxis: {
                    title: 'Signal (mAU)',
                    gridcolor: gridColor,
                    zerolinecolor: gridColor,
                    color: textColor,
                    fixedrange: this.viewMode === 'manual',
                },
                plot_bgcolor: bgColor,
                paper_bgcolor: bgColor,
                font: { color: textColor },
                annotations: peakAnnotations,
                showlegend: false,
            };

            const config = {
                responsive: true,
                displayModeBar: true,
                modeBarButtonsToRemove: ['lasso2d', 'select2d'],
                displaylogo: false,
            };

            Plotly.newPlot('chromatogram', [trace], layout, config);

            const pointsPerFrame = Math.max(1, Math.floor(totalPoints / 100));
            let currentIndex = 0;

            const animate = () => {
                if (currentIndex >= totalPoints) {
                    return;
                }

                const endIndex = Math.min(
                    currentIndex + pointsPerFrame,
                    totalPoints,
                );
                trace.x = timeData.slice(0, endIndex);
                trace.y = signalData.slice(0, endIndex);

                Plotly.restyle('chromatogram', {
                    x: [trace.x],
                    y: [trace.y],
                });

                currentIndex = endIndex;
                requestAnimationFrame(animate);
            };

            animate();
        },

        setViewMode(mode) {
            this.viewMode = mode;
            if (this.plotInstance && mode === 'manual') {
                Plotly.relayout('chromatogram', {
                    'yaxis.fixedrange': true,
                });
            } else if (this.plotInstance) {
                Plotly.relayout('chromatogram', {
                    'yaxis.fixedrange': false,
                });
            }
        },

        pressurePercent() {
            if (!this.results.metrics.max_pressure_bar) return 0;
            const limit = this.level.max_pressure_bar || 400;
            return Math.min(
                100,
                (this.results.metrics.max_pressure_bar / limit) * 100,
            );
        },

        pressureBarClass() {
            const percent = this.pressurePercent();
            if (percent > 90) return 'bg-danger';
            if (percent > 75) return 'bg-warning';
            return 'bg-success';
        },

        async submitScore() {
            const payload = {
                level_id: this.level.id,
                score: this.results.score,
                total_run_time: this.results.metrics.total_run_time,
                min_resolution: this.results.metrics.min_resolution,
                max_pressure_bar: this.results.metrics.max_pressure_bar,
                is_successful: this.results.success,
                overpressure: this.results.overpressure,
                mobile_phase: {
                    start_b: this.params.start_b,
                    end_b: this.params.end_b,
                    ramp_time: this.params.ramp_time,
                    ph: this.params.ph,
                    is_gradient: this.params.is_gradient,
                },
                column_config: {
                    chemistry: this.params.column_chemistry,
                    length_mm: this.params.column_length_mm,
                    id_mm: this.params.column_id_mm,
                    particle_size_um: this.params.particle_size_um,
                },
                operation_config: {
                    flow_rate_ml_min: this.params.flow_rate_ml_min,
                    temperature_c: this.params.temperature_c,
                    injection_volume_ul: this.params.injection_volume_ul,
                },
            };

            try {
                await fetch('/hplc/api/scores/', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': this.getCookie('csrftoken'),
                    },
                    body: JSON.stringify(payload),
                });
            } catch (error) {
                console.error('Failed to submit score:', error);
            }
        },

        difficultyBadge(difficulty) {
            const classes = {
                beginner: 'badge-beginner',
                intermediate: 'badge-intermediate',
                advanced: 'badge-advanced',
                expert: 'badge-advanced',
            };
            return classes[difficulty] || 'badge-beginner';
        },

        getCookie(name) {
            let cookieValue = null;
            if (document.cookie && document.cookie !== '') {
                const cookies = document.cookie.split(';');
                for (let i = 0; i < cookies.length; i++) {
                    const cookie = cookies[i].trim();
                    if (
                        cookie.substring(0, name.length + 1) === name + '='
                    ) {
                        cookieValue = decodeURIComponent(
                            cookie.substring(name.length + 1),
                        );
                        break;
                    }
                }
            }
            return cookieValue;
        },
    };
}
