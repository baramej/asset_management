import { registry } from "@web/core/registry";
import { Component, onWillStart, useState, onMounted } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { loadJS } from "@web/core/assets";

class FacilityDashboard extends Component {
    setup() {
        this.orm = useService("orm");
        this.action = useService("action");

        this.state = useState({
            data: {
                // Assets
                total_assets: 0,
                assets_active: 0,
                assets_maintenance: 0,
                assets_out_of_service: 0,
                assets_retired: 0,
                assets_by_company: "[]",
                assets_by_category: "[]",

                // Maintenance
                total_maintenance: 0,
                maintenance_scheduled: 0,
                maintenance_in_progress: 0,
                maintenance_pending_parts: 0,
                maintenance_completed: 0,
                maintenance_cancelled: 0,
                maintenance_overdue: 0,

                maintenance_preventive: 0,
                maintenance_corrective: 0,
                maintenance_predictive: 0,

                // Helpdesk
                total_helpdesk: 0,
                helpdesk_new: 0,
                helpdesk_open: 0,
                helpdesk_in_progress: 0,
                helpdesk_pending: 0,
                helpdesk_resolved: 0,
                helpdesk_closed: 0,
                helpdesk_unassigned: 0,

                helpdesk_urgent: 0,
                helpdesk_high: 0,
                helpdesk_normal: 0,
                helpdesk_low: 0,

                // Teams
                total_teams: 0,
                total_team_members: 0,
                teams_available: 0,
                teams_busy: 0,
                team_workload: "[]",

                // Trends
                monthly_maintenance_data: "[]",
                monthly_helpdesk_data: "[]",
            },
            loading: true,
        });

        onWillStart(async () => {
            await loadJS("/web/static/lib/Chart/Chart.js");
            await this.loadDashboardData();
        });

        onMounted(() => {
            if (!this.state.loading) {
                this.renderCharts();
            }
        });
    }

    async loadDashboardData() {
        try {
            const ids = await this.orm.call("asset.dashboard", "search", [[]]);
            let id = ids.length ? ids[0] : await this.orm.call("asset.dashboard", "create", [{}]);

            const data = await this.orm.call("asset.dashboard", "read", [
                [id],
                Object.keys(this.state.data),
            ]);

            if (data && data.length) {
                this.state.data = { ...this.state.data, ...data[0] };
            }

            this.state.loading = false;
            setTimeout(() => this.renderCharts(), 50);

        } catch (error) {
            console.error("Dashboard Load Error", error);
            this.state.loading = false;
        }
    }

    renderCharts() {
        if (this.state.loading) return;

        try {
            this.renderAssetsCompanyChart();
            this.renderAssetsCategoryChart();
            this.renderMaintenanceStatusChart();
            this.renderMaintenanceTypeChart();
            this.renderMaintenanceTypeBarChart();
            this.renderHelpdeskStatusChart();
            this.renderHelpdeskPriorityChart();
            this.renderTeamWorkloadChart();
            this.renderMonthlyMaintenanceChart();
            this.renderMonthlyHelpdeskChart();
        } catch (error) {
            console.error("Chart Render Error:", error);
        }
    }

    // --------------------------------------------------------------
    //  ASSETS BY COMPANY (NEW REPLACEMENT)
    // --------------------------------------------------------------
    renderAssetsCompanyChart() {
        const canvas = document.getElementById("assetsCompanyChart");
        if (!canvas) return;

        const existing = Chart.getChart(canvas);
        if (existing) existing.destroy();

        let items = [];
        try {
            items = JSON.parse(this.state.data.assets_by_company || "[]");
        } catch (e) {}

        new Chart(canvas, {
            type: "bar",
            data: {
                labels: items.map((x) => x.company),
                datasets: [{
                    label: "Assets",
                    data: items.map((x) => x.count),
                    backgroundColor: "#0d6efd",
                }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: { y: { beginAtZero: true } },
            },
        });
    }



    // --------------------------------------------------------------
    //  ASSETS BY CATEGORY
    // --------------------------------------------------------------
    renderAssetsCategoryChart() {
        const canvas = document.getElementById("assetsCategoryChart");
        if (!canvas) return;

        const existing = Chart.getChart(canvas);
        if (existing) existing.destroy();

        let items = [];
        try {
            items = JSON.parse(this.state.data.assets_by_category || "[]");
        } catch (e) {}

        new Chart(canvas, {
            type: "bar",
            data: {
                labels: items.map((d) => d.name),
                datasets: [{
                    label: "Assets",
                    data: items.map((d) => d.count),
                    backgroundColor: "#198754",
                }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: { y: { beginAtZero: true } },
                plugins: { legend: { display: false } },
            },
        });
    }

    // --------------------------------------------------------------
    //  MAINTENANCE STATUS
    // --------------------------------------------------------------
    renderMaintenanceStatusChart() {
        const canvas = document.getElementById("maintenanceStatusChart");
        if (!canvas) return;

        const ex = Chart.getChart(canvas);
        if (ex) ex.destroy();

        new Chart(canvas, {
            type: "doughnut",
            data: {
                labels: ["Scheduled", "In Progress", "Pending Parts", "Completed", "Cancelled"],
                datasets: [{
                    data: [
                        this.state.data.maintenance_scheduled,
                        this.state.data.maintenance_in_progress,
                        this.state.data.maintenance_pending_parts,
                        this.state.data.maintenance_completed,
                        this.state.data.maintenance_cancelled,
                    ],
                    backgroundColor: [
                        "#6c757d",
                        "#0d6efd",
                        "#ffc107",
                        "#198754",
                        "#dc3545"
                    ],
                }],
            },
            options: {
                responsive: true,
                plugins: { legend: { position: "bottom" } },
            },
        });
    }

    // --------------------------------------------------------------
    //  MAINTENANCE TYPE
    // --------------------------------------------------------------
    renderMaintenanceTypeChart() {
        const canvas = document.getElementById("maintenanceTypeChart");
        if (!canvas) return;

        const ex = Chart.getChart(canvas);
        if (ex) ex.destroy();

        new Chart(canvas, {
            type: "pie",
            data: {
                labels: ["Preventive", "Corrective", "Predictive"],
                datasets: [{
                    data: [
                        this.state.data.maintenance_preventive,
                        this.state.data.maintenance_corrective,
                        this.state.data.maintenance_predictive,
                    ],
                    backgroundColor: ["#198754", "#ffc107", "#0d6efd"],
                }],
            },
            options: {
                responsive: true,
                plugins: { legend: { position: "bottom" } },
            },
        });
    }

    renderMaintenanceTypeBarChart() {
    const ctx = document.getElementById("maintenanceTypeBarChart");
    if (!ctx) return;

    const existingChart = Chart.getChart(ctx);
    if (existingChart) existingChart.destroy();

    new Chart(ctx, {
        type: "bar",
        data: {
            labels: ["Preventive", "Corrective"],
            datasets: [{
                label: "Tasks",
                data: [
                    this.state.data.maintenance_preventive || 0,
                    this.state.data.maintenance_corrective || 0,
                ],
                backgroundColor: ["#198754", "#dc3545"],
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                y: { beginAtZero: true }
            },
            plugins: { legend: { display: false } },
        },
    });
    }

    // --------------------------------------------------------------
    //  HELPDESK STATUS
    // --------------------------------------------------------------
    renderHelpdeskStatusChart() {
        const canvas = document.getElementById("helpdeskStatusChart");
        if (!canvas) return;

        const ex = Chart.getChart(canvas);
        if (ex) ex.destroy();

        new Chart(canvas, {
            type: "bar",
            data: {
                labels: ["New", "Open", "In Progress", "Pending", "Resolved", "Closed"],
                datasets: [{
                    label: "Tickets",
                    data: [
                        this.state.data.helpdesk_new,
                        this.state.data.helpdesk_open,
                        this.state.data.helpdesk_in_progress,
                        this.state.data.helpdesk_pending,
                        this.state.data.helpdesk_resolved,
                        this.state.data.helpdesk_closed,
                    ],
                    backgroundColor: [
                        "#6c757d",
                        "#0d6efd",
                        "#ffc107",
                        "#fd7e14",
                        "#198754",
                        "#20c997",
                    ],
                }],
            },
            options: {
                responsive: true,
                scales: { y: { beginAtZero: true } },
                plugins: { legend: { display: false } },
            },
        });
    }

    // --------------------------------------------------------------
    //  HELPDESK PRIORITY
    // --------------------------------------------------------------
    renderHelpdeskPriorityChart() {
        const canvas = document.getElementById("helpdeskPriorityChart");
        if (!canvas) return;

        const ex = Chart.getChart(canvas);
        if (ex) ex.destroy();

        new Chart(canvas, {
            type: "doughnut",
            data: {
                labels: ["Urgent", "High", "Normal", "Low"],
                datasets: [{
                    data: [
                        this.state.data.helpdesk_urgent,
                        this.state.data.helpdesk_high,
                        this.state.data.helpdesk_normal,
                        this.state.data.helpdesk_low,
                    ],
                    backgroundColor: ["#dc3545", "#fd7e14", "#ffc107", "#198754"],
                }],
            },
            options: {
                responsive: true,
                plugins: { legend: { position: "bottom" } },
            },
        });
    }

    // --------------------------------------------------------------
    //  TEAM WORKLOAD
    // --------------------------------------------------------------
    renderTeamWorkloadChart() {
        const canvas = document.getElementById("teamWorkloadChart");
        if (!canvas) return;

        const existing = Chart.getChart(canvas);
        if (existing) existing.destroy();

        let items = [];
        try {
            items = JSON.parse(this.state.data.team_workload || "[]");
        } catch (e) {}

        new Chart(canvas, {
            type: "bar",
            data: {
                labels: items.map((d) => d.team),
                datasets: [{
                    label: "Active Tasks",
                    data: items.map((d) => d.tasks),
                    backgroundColor: "#0d6efd",
                }],
            },
            options: {
                responsive: true,
                scales: { y: { beginAtZero: true } },
                plugins: { legend: { display: false } },
            },
        });
    }

    // --------------------------------------------------------------
    //  MONTHLY MAINTENANCE TREND
    // --------------------------------------------------------------
    renderMonthlyMaintenanceChart() {
        const canvas = document.getElementById("monthlyMaintenanceChart");
        if (!canvas) return;

        const ex = Chart.getChart(canvas);
        if (ex) ex.destroy();

        let items = [];
        try {
            items = JSON.parse(this.state.data.monthly_maintenance_data || "[]");
        } catch (e) {}

        new Chart(canvas, {
            type: "line",
            data: {
                labels: items.map((d) => d.month),
                datasets: [
                    {
                        label: "Total Tasks",
                        data: items.map((d) => d.total),
                        borderColor: "#0d6efd",
                        backgroundColor: "rgba(13,110,253,0.2)",
                        tension: 0.3,
                        fill: true,
                    },
                    {
                        label: "Completed",
                        data: items.map((d) => d.completed),
                        borderColor: "#198754",
                        backgroundColor: "rgba(25,135,84,0.2)",
                        tension: 0.3,
                        fill: true,
                    },
                ],
            },
            options: {
                responsive: true,
                plugins: { legend: { position: "top" } },
            },
        });
    }

    // --------------------------------------------------------------
    //  MONTHLY HELPDESK TREND
    // --------------------------------------------------------------
    renderMonthlyHelpdeskChart() {
        const canvas = document.getElementById("monthlyHelpdeskChart");
        if (!canvas) return;

        const ex = Chart.getChart(canvas);
        if (ex) ex.destroy();

        let items = [];
        try {
            items = JSON.parse(this.state.data.monthly_helpdesk_data || "[]");
        } catch (e) {}

        new Chart(canvas, {
            type: "line",
            data: {
                labels: items.map((d) => d.month),
                datasets: [
                    {
                        label: "Total Tickets",
                        data: items.map((d) => d.total),
                        borderColor: "#0d6efd",
                        backgroundColor: "rgba(13,110,253,0.2)",
                        tension: 0.3,
                        fill: true,
                    },
                    {
                        label: "Resolved",
                        data: items.map((d) => d.resolved),
                        borderColor: "#198754",
                        backgroundColor: "rgba(25,135,84,0.2)",
                        tension: 0.3,
                        fill: true,
                    },
                ],
            },
            options: {
                responsive: true,
                plugins: { legend: { position: "top" } },
            },
        });
    }

    // Open list view
    async openView(model, name, domain = []) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: name,
            res_model: model,
            views: [[false, "list"], [false, "form"]],
            domain,
        });
    }
}

FacilityDashboard.template = "facility_management.Dashboard";
registry.category("actions").add("facility_dashboard", FacilityDashboard);
