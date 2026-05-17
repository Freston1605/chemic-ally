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
                beginner: 'bg-success',
                intermediate: 'bg-info',
                advanced: 'bg-warning text-dark',
                expert: 'bg-danger',
            };
            return classes[difficulty] || 'bg-secondary';
        },
    };
}

function hplcSimulator() {
    return {
        level: {},
        availableColumns: ['C18', 'C8'],
        params: {
            is_gradient: true,
            start_b: 10,
            end_b: 90,
            ramp_time: 15,
            ph: 3.0,
            column_chemistry: 'C18',
            column_length_mm: 150,
            column_id_mm: 4.6,
            particle_size_um: 5.0,
            flow_rate_ml_min: 1.0,
            temperature_c: 30,
            injection_volume_ul: 10,
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

        async loadLevel(slug) {
            try {
                const response = await fetch(`/hplc/api/levels/${slug}/`);
                this.level = await response.json();
                this.availableColumns = this.level.constraints.available_columns;

                if (this.level.constraints.max_pressure_bar) {
                    this.level.max_pressure_bar = this.level.constraints.max_pressure_bar;
                }

                if (!this.availableColumns.includes(this.params.column_chemistry)) {
                    this.params.column_chemistry = this.availableColumns[0];
                }
            } catch (error) {
                console.error('Failed to load level:', error);
            }
        },

        resetParams() {
            this.params = {
                is_gradient: true,
                start_b: 10,
                end_b: 90,
                ramp_time: 15,
                ph: 3.0,
                column_chemistry: this.availableColumns[0],
                column_length_mm: 150,
                column_id_mm: 4.6,
                particle_size_um: 5.0,
                flow_rate_ml_min: 1.0,
                temperature_c: 30,
                injection_volume_ul: 10,
            };
        },

        async runSimulation() {
            this.running = true;
            this.hasResults = false;

            const payload = {
                level_id: this.level.id,
                mobile_phase: {
                    start_b: this.params.start_b,
                    end_b: this.params.end_b,
                    ramp_time: this.params.ramp_time,
                    ph: this.params.ph,
                    buffer_concentration_mm: 10,
                    is_gradient: this.params.is_gradient,
                },
                column: {
                    chemistry: this.params.column_chemistry,
                    length_mm: this.params.column_length_mm,
                    id_mm: this.params.column_id_mm,
                    particle_size_um: this.params.particle_size_um,
                },
                operation: {
                    flow_rate_ml_min: this.params.flow_rate_ml_min,
                    temperature_c: this.params.temperature_c,
                    injection_volume_ul: this.params.injection_volume_ul,
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
                    this.animateChromatogram(data.chromatogram, data.peaks);
                } else {
                    console.error('Simulation error:', data);
                }
            } catch (error) {
                console.error('Failed to run simulation:', error);
            } finally {
                this.running = false;
            }
        },

        animateChromatogram(chromatogram, peaks) {
            const timeData = chromatogram.time;
            const signalData = chromatogram.signal;
            const totalPoints = timeData.length;

            const trace = {
                x: [],
                y: [],
                type: 'scatter',
                mode: 'lines',
                line: { color: '#0d6efd', width: 1.5 },
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
                arrowcolor: '#6c757d',
                font: { size: 10, color: '#adb5bd' },
                ay: -20,
            }));

            const layout = {
                margin: { t: 30, r: 20, b: 40, l: 60 },
                xaxis: {
                    title: 'Time (min)',
                    gridcolor: '#495057',
                    zerolinecolor: '#495057',
                },
                yaxis: {
                    title: 'Signal (mAU)',
                    gridcolor: '#495057',
                    zerolinecolor: '#495057',
                    fixedrange: this.viewMode === 'manual',
                },
                plot_bgcolor: '#212529',
                paper_bgcolor: '#212529',
                font: { color: '#adb5bd' },
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

                const endIndex = Math.min(currentIndex + pointsPerFrame, totalPoints);
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
                beginner: 'bg-success',
                intermediate: 'bg-info',
                advanced: 'bg-warning text-dark',
                expert: 'bg-danger',
            };
            return classes[difficulty] || 'bg-secondary';
        },

        getCookie(name) {
            let cookieValue = null;
            if (document.cookie && document.cookie !== '') {
                const cookies = document.cookie.split(';');
                for (let i = 0; i < cookies.length; i++) {
                    const cookie = cookies[i].trim();
                    if (cookie.substring(0, name.length + 1) === name + '=') {
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
